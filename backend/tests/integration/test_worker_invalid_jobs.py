"""Malformed queued identities are rejected with real workspace RLS and leases."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.domain.agent import AgentMode
from sales_backend.job_context import JobLeaseLost
from sales_backend.repositories.assistant import AssistantRepository
from sales_backend.repositories.jobs import JobRepository
from sales_backend.repositories.visit_imports import create_import, import_detail, retry_import
from sales_backend.worker import Worker

pytestmark = pytest.mark.asyncio


class TransactionDatabase:
    """Reuse the fixture's rollback transaction; claim/failure still run actual SQL."""

    def __init__(self, connection):
        self.conn = connection
        self.settings = SimpleNamespace(worker_id="invalid-actor-probe", worker_lock_seconds=30)

    @asynccontextmanager
    async def connection(self):
        yield self.conn


async def enqueue(connection, actor, kind, *, aggregate_id=None):
    return await connection.fetchval(
        """INSERT INTO ops.job(workspace_id,job_type,payload,aggregate_id,priority)
        VALUES($1::uuid,$2,$3::jsonb,$4::uuid,100) RETURNING id""",
        actor.workspace_id, kind,
        {"user_id": actor.user_id, "role": "removed_legacy_role", "data_scope": "workspace"},
        aggregate_id,
    )


async def test_invalid_actor_is_dead_lettered_once_without_business_handler(connection, manager_actor):
    kind, other_kind = "invalid.actor." + uuid4().hex, "untouched.actor." + uuid4().hex
    identifier = await enqueue(connection, manager_actor, kind)
    other = await enqueue(connection, manager_actor, other_kind)
    worker = Worker(TransactionDatabase(connection))
    worker._handle_job = AsyncMock()
    assert await worker.run_once(job_types=(kind,))
    worker._handle_job.assert_not_awaited()
    # Function-local queue context is restored; no fabricated actor leaks to caller.
    assert await connection.fetchval("SELECT current_setting('app.role_code',true)") == manager_actor.role.value
    assert await connection.fetchval("SELECT current_setting('app.user_ref_id',true)") == manager_actor.user_id
    row = await connection.fetchrow(
        "SELECT status,attempts,lease_token,last_error_code FROM ops.job WHERE id=$1", identifier,
    )
    assert dict(row) == {
        "status": "dead_letter", "attempts": 1, "lease_token": None, "last_error_code": "INVALID_JOB_ACTOR",
    }
    assert await connection.fetchval("SELECT count(*) FROM ops.job_effect WHERE job_id=$1", identifier) == 0
    assert await connection.fetchval("SELECT status FROM ops.job WHERE id=$1", other) == "queued"
    assert not await worker.run_once(job_types=(kind,))


async def test_invalid_actor_rejection_cannot_finish_a_reclaimed_attempt(connection, manager_actor):
    kind = "invalid.reclaim." + uuid4().hex
    identifier = await enqueue(connection, manager_actor, kind)
    repo = JobRepository()
    old = await repo.claim(connection, worker_id="old", lock_seconds=3, job_types=(kind,))
    await connection.execute(
        "UPDATE ops.job SET locked_until=clock_timestamp()-interval '1 second' WHERE id=$1", identifier,
    )
    fresh = await repo.claim(connection, worker_id="fresh", lock_seconds=30, job_types=(kind,))
    with pytest.raises(JobLeaseLost):
        await Worker(TransactionDatabase(connection))._reject_invalid_job(old)
    row = await connection.fetchrow("SELECT status,attempts,lease_token FROM ops.job WHERE id=$1", identifier)
    assert row["status"] == "running" and row["attempts"] == 2 and str(row["lease_token"]) == fresh.lease_token


async def test_invalid_actor_rejection_requires_the_exact_lease(connection, manager_actor):
    kind = "invalid.token." + uuid4().hex
    identifier = await enqueue(connection, manager_actor, kind)
    claimed = await JobRepository().claim(connection, worker_id="owned", lock_seconds=30, job_types=(kind,))
    with pytest.raises(JobLeaseLost):
        await Worker(TransactionDatabase(connection))._reject_invalid_job(
            replace(claimed, lease_token=str(uuid4())),
        )
    row = await connection.fetchrow("SELECT status,lease_token FROM ops.job WHERE id=$1", identifier)
    assert row["status"] == "running" and str(row["lease_token"]) == claimed.lease_token


async def claimed_import(connection, owner, *, status="queued"):
    import_id = str(uuid4())
    await create_import(connection, owner, import_id, "synthetic.txt", "/not-opened/synthetic.txt", 16)
    identifier = await connection.fetchval(
        """UPDATE ops.job SET payload=$2::jsonb,priority=100
        WHERE aggregate_id=$1::uuid AND job_type='visit.import' RETURNING id""",
        import_id, {"user_id": owner.user_id, "role": "removed_legacy_role", "data_scope": "workspace"},
    )
    claimed = await JobRepository().claim(connection, worker_id="invalid-import", lock_seconds=30,
                                          job_types=("visit.import",))
    assert claimed.id == str(identifier)
    await connection.execute("UPDATE activity.visit_import SET status=$2 WHERE id=$1::uuid", import_id, status)
    return import_id, claimed


@pytest.mark.parametrize("prior_status", ["queued", "processing"])
async def test_invalid_import_becomes_failed_and_owner_can_retry(connection, manager_actor, prior_status):
    import_id, claimed = await claimed_import(connection, manager_actor, status=prior_status)
    worker = Worker(TransactionDatabase(connection))
    worker.jobs.claim = AsyncMock(return_value=claimed)
    worker._handle_job = AsyncMock()
    assert await worker.run_once(job_types=("visit.import",))
    worker._handle_job.assert_not_awaited()
    visible = await import_detail(connection, manager_actor, import_id)
    assert visible["status"] == "failed" and "身份快照无效" in visible["error_message"]
    assert await connection.fetchval("SELECT status FROM ops.job WHERE id=$1::uuid", claimed.id) == "dead_letter"
    result = await retry_import(connection, manager_actor, import_id)
    assert result["status"] == "queued"
    queued = await connection.fetchrow(
        "SELECT id,payload FROM ops.job WHERE aggregate_id=$1::uuid AND status='queued'", import_id,
    )
    assert queued is not None and str(queued["id"]) != claimed.id
    assert Worker._job_actor(replace(claimed, payload=queued["payload"])) == manager_actor
    # Duplicate terminal calls are stale and cannot break the newly queued retry.
    with pytest.raises(JobLeaseLost):
        await worker._reject_invalid_job(claimed)
    assert (await import_detail(connection, manager_actor, import_id))["status"] == "queued"


@pytest.mark.parametrize("with_receipt", [False, True])
async def test_invalid_identity_preserves_completed_import_and_receipt(connection, manager_actor, with_receipt):
    import_id, claimed = await claimed_import(connection, manager_actor, status="succeeded")
    await connection.execute(
        "UPDATE activity.visit_import SET extracted_text='confirmed result',evidence='[{\"source\":\"fixture\"}]' "
        "WHERE id=$1::uuid", import_id,
    )
    if with_receipt:
        await connection.execute(
            "INSERT INTO ops.job_effect(job_id,workspace_id,lease_token) VALUES($1::uuid,$2::uuid,$3::uuid)",
            claimed.id, manager_actor.workspace_id, claimed.lease_token,
        )
    before = await connection.fetchrow("SELECT * FROM activity.visit_import WHERE id=$1::uuid", import_id)
    await Worker(TransactionDatabase(connection))._reject_invalid_job(claimed)
    assert before == await connection.fetchrow("SELECT * FROM activity.visit_import WHERE id=$1::uuid", import_id)
    queue = await connection.fetchrow("SELECT status,last_error_code FROM ops.job WHERE id=$1::uuid", claimed.id)
    assert dict(queue) == {"status": "succeeded", "last_error_code": None}


@asynccontextmanager
async def fixture_admin(connection):
    """Privileged fixture setup only; restore the non-bypass role before the call."""
    role = await connection.fetchval("SELECT current_user")
    await connection.execute("RESET ROLE")
    try:
        yield
    finally:
        await connection.execute('SET LOCAL ROLE "' + role.replace('"', '""') + '"')


async def test_invalid_job_cannot_mutate_an_aggregate_in_another_workspace(connection, manager_actor):
    import_id, original = await claimed_import(connection, manager_actor, status="processing")
    foreign_workspace, foreign_job, foreign_lease = uuid4(), uuid4(), uuid4()
    async with fixture_admin(connection):
        await connection.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'隔离错误关联空间')",
            foreign_workspace, uuid4().hex,
        )
        await connection.execute(
            """INSERT INTO ops.job(id,workspace_id,job_type,aggregate_id,payload,status,attempts,lease_token,locked_until)
            VALUES($1,$2,'visit.import',$3::uuid,'{}','running',1,$4,clock_timestamp()+interval '30 seconds')""",
            foreign_job, foreign_workspace, import_id, foreign_lease,
        )
    before = await connection.fetchrow("SELECT * FROM activity.visit_import WHERE id=$1::uuid", import_id)
    assert await connection.fetchval(
        "SELECT ops.reject_invalid_job_actor($1,$2)", foreign_job, foreign_lease,
    ) == "dead_letter"
    assert before == await connection.fetchrow("SELECT * FROM activity.visit_import WHERE id=$1::uuid", import_id)
    assert await connection.fetchval("SELECT status FROM ops.job WHERE id=$1::uuid", original.id) == "running"
    async with fixture_admin(connection):
        assert await connection.fetchval("SELECT status FROM ops.job WHERE id=$1", foreign_job) == "dead_letter"


@pytest.mark.parametrize("kind", ["agent.run", "sales_competency.review", "business.advice"])
async def test_invalid_identity_closes_other_pending_projections(connection, manager_actor, kind):
    from sales_backend.repositories.capabilities import CapabilityRepository
    identity = await CapabilityRepository().analysis_identity(connection, manager_actor)
    identifier = str(uuid4())
    if kind == "agent.run":
        conversation = await AssistantRepository().create_conversation(
            connection, manager_actor, mode=AgentMode.CHATBI, customer_id=None,
        )
        await connection.execute(
            "INSERT INTO agent.run(id,workspace_id,conversation_id,identity_context,status) "
            "VALUES($1::uuid,$2::uuid,$3::uuid,$4::jsonb,'running')",
            identifier, manager_actor.workspace_id, conversation["id"], identity,
        )
        table = "agent.run"
    elif kind == "sales_competency.review":
        framework = await connection.fetchval("SELECT version_no FROM config.sales_competency_framework LIMIT 1")
        await connection.execute(
            "INSERT INTO insight.sales_competency_review(id,workspace_id,subject_user_ref_id,review_date,"
            "framework_version,status) VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5,'running')",
            identifier, manager_actor.workspace_id, manager_actor.user_id, date(2100, 1, 1), framework,
        )
        table = "insight.sales_competency_review"
    else:
        from tests.integration.test_business_rankings import opportunity
        project = await opportunity(connection, "XS001", 100)
        await set_request_context(connection, manager_actor)
        await connection.execute(
            """INSERT INTO insight.business_advice(id,workspace_id,actor_user_ref_id,actor_role_code,
            subject_kind,subject_id,customer_id,section,cache_key,facts_fingerprint,configuration_fingerprint,
            identity_snapshot,facts_snapshot,configuration_snapshot,status)
            VALUES($1::uuid,$2::uuid,$3::uuid,'manager','customer',$4::uuid,$4::uuid,'overview',$5,$5,$5,
            $6::jsonb,'{}'::jsonb,'{}'::jsonb,'running')""",
            identifier, manager_actor.workspace_id, manager_actor.user_id, project["customer_id"], uuid4().hex * 2,
            await CapabilityRepository().analysis_identity(connection, manager_actor),
        )
        table = "insight.business_advice"
    queue_id = await enqueue(connection, manager_actor, kind, aggregate_id=identifier)
    claimed = await JobRepository().claim(connection, worker_id="invalid-projection", lock_seconds=30, job_types=(kind,))
    assert claimed.id == str(queue_id)
    await Worker(TransactionDatabase(connection))._reject_invalid_job(claimed)
    row = await connection.fetchrow(f"SELECT status,error_code FROM {table} WHERE id=$1::uuid", identifier)
    assert dict(row) == {"status": "failed", "error_code": "INVALID_JOB_ACTOR"}
