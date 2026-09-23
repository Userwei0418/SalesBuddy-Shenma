"""Typed claims/recovery through the real security-definer function, rolled back."""
from uuid import uuid4

import pytest

from sales_backend.job_context import JobLeaseLost
from sales_backend.repositories.jobs import JobRepository

pytestmark = pytest.mark.asyncio


async def enqueue(connection, actor, kind, **fields):
    return await connection.fetchval(
        """INSERT INTO ops.job(workspace_id,job_type,payload,priority,max_attempts,attempts,status)
        VALUES($1::uuid,$2,$3::jsonb,100,$4,$5,$6) RETURNING id""",
        actor.workspace_id, kind, {"user_id": actor.user_id, "role": actor.role.value},
        fields.get("max_attempts", 3), fields.get("attempts", 0), fields.get("status", "queued"),
    )


async def test_include_filter_leaves_other_lane_jobs_unclaimed(connection, manager_actor):
    imports, agents = "probe.import." + uuid4().hex, "probe.agent." + uuid4().hex
    long_id = await enqueue(connection, manager_actor, imports)
    short_id = await enqueue(connection, manager_actor, agents)
    repo = JobRepository()
    short = await repo.claim(connection, worker_id="interactive", lock_seconds=30, job_types=(agents,))
    assert short.id == str(short_id) and short.attempts == 1 and short.queue_wait_seconds >= 0
    row = await connection.fetchrow("SELECT attempts,status,lease_token FROM ops.job WHERE id=$1", long_id)
    assert dict(row) == {"attempts": 0, "status": "queued", "lease_token": None}
    assert await repo.claim(connection, worker_id="interactive", lock_seconds=30, job_types=(agents,)) is None


async def test_complement_covers_unknown_type_without_import_lease(connection, manager_actor):
    excluded = list(await connection.fetch("SELECT DISTINCT job_type FROM ops.job"))
    excluded = [row["job_type"] for row in excluded]
    long_kind, unknown_kind = "probe.import." + uuid4().hex, "future.job." + uuid4().hex
    long_id = await enqueue(connection, manager_actor, long_kind)
    unknown_id = await enqueue(connection, manager_actor, unknown_kind)
    selected = await JobRepository().claim(connection, worker_id="review", lock_seconds=30,
                                           job_types=tuple([*excluded, long_kind]), exclude_types=True)
    assert selected.id == str(unknown_id)
    assert await connection.fetchval("SELECT attempts FROM ops.job WHERE id=$1", long_id) == 0


async def test_expired_running_reclaims_only_requested_lane_and_fences_old_attempt(connection, manager_actor):
    one, two = "probe.one." + uuid4().hex, "probe.two." + uuid4().hex
    await enqueue(connection, manager_actor, one)
    other_id = await enqueue(connection, manager_actor, two)
    repo = JobRepository()
    old = await repo.claim(connection, worker_id="lost", lock_seconds=3, job_types=(one,))
    await connection.execute("UPDATE ops.job SET locked_until=clock_timestamp()-interval '1 second' WHERE id=$1::uuid",
                             old.id)
    fresh = await repo.claim(connection, worker_id="recovered", lock_seconds=30, job_types=(one,))
    assert fresh.id == old.id and fresh.attempts == 2 and fresh.lease_token != old.lease_token
    for operation in [repo.succeed(connection, job=old), repo.heartbeat(connection, job=old, lock_seconds=30),
                      repo.fail(connection, job=old, error_code="late", error_detail="late", retryable=True)]:
        with pytest.raises(JobLeaseLost):
            await operation
    assert await connection.fetchval("SELECT attempts FROM ops.job WHERE id=$1", other_id) == 0
    await repo.succeed(connection, job=fresh)


async def test_receipt_recovery_does_not_reexecute_or_touch_other_lane(connection, manager_actor):
    one, two = "probe.receipt." + uuid4().hex, "probe.other." + uuid4().hex
    job_id = await enqueue(connection, manager_actor, one)
    other_id = await enqueue(connection, manager_actor, two, attempts=3, max_attempts=3)
    repo = JobRepository()
    old = await repo.claim(connection, worker_id="after-result", lock_seconds=3, job_types=(one,))
    await connection.execute(
        "INSERT INTO ops.job_effect(job_id,workspace_id,lease_token) VALUES($1,$2::uuid,$3::uuid)",
        job_id, manager_actor.workspace_id, old.lease_token,
    )
    await connection.execute("UPDATE ops.job SET locked_until=clock_timestamp()-interval '1 second' WHERE id=$1", job_id)
    assert await repo.claim(connection, worker_id="next", lock_seconds=30, job_types=(one,)) is None
    row = await connection.fetchrow("SELECT status,attempts,lease_token FROM ops.job WHERE id=$1", job_id)
    assert dict(row) == {"status": "succeeded", "attempts": 1, "lease_token": None}
    assert await connection.fetchval("SELECT status FROM ops.job WHERE id=$1", other_id) == "queued"
    assert await repo.claim(connection, worker_id="other", lock_seconds=30, job_types=(two,)) is None
    assert await connection.fetchval("SELECT status FROM ops.job WHERE id=$1", other_id) == "dead_letter"


async def test_retry_schedule_is_respected_and_wait_excludes_backoff(connection, manager_actor):
    kind = "probe.retry." + uuid4().hex
    job_id = await enqueue(connection, manager_actor, kind)
    repo = JobRepository()
    first = await repo.claim(connection, worker_id="worker", lock_seconds=30, job_types=(kind,))
    await repo.fail(connection, job=first, error_code="TIMEOUT", error_detail="transient", retryable=True)
    assert await repo.claim(connection, worker_id="worker", lock_seconds=30, job_types=(kind,)) is None
    await connection.execute("UPDATE ops.job SET available_at=clock_timestamp()-interval '2 seconds' WHERE id=$1", job_id)
    second = await repo.claim(connection, worker_id="worker", lock_seconds=30, job_types=(kind,))
    assert second.attempts == 2 and 2 <= second.queue_wait_seconds < 5
    await repo.fail(connection, job=second, error_code="INVALID", error_detail="terminal", retryable=False)
    assert await connection.fetchval("SELECT status FROM ops.job WHERE id=$1", job_id) == "dead_letter"


async def test_empty_include_filter_does_not_recover_or_claim_any_job(connection, manager_actor):
    job_id = await enqueue(connection, manager_actor, "probe.empty." + uuid4().hex, attempts=3, max_attempts=3)
    assert await JobRepository().claim(connection, worker_id="empty", lock_seconds=30, job_types=()) is None
    assert await connection.fetchval("SELECT status FROM ops.job WHERE id=$1", job_id) == "queued"


async def test_legacy_claim_remains_available_and_calls_canonical_function(connection, manager_actor):
    # Verify the overload itself, not a mock or a separately maintained old algorithm.
    source = await connection.fetchval("SELECT prosrc FROM pg_proc WHERE oid='ops.claim_job(text,integer)'::regprocedure")
    assert "NULL::text[],false" in source
    assert await connection.fetchval(
        "SELECT has_function_privilege(current_user,'ops.claim_job(text,integer,text[],boolean)','EXECUTE')"
    )
    await connection.fetch("SELECT * FROM ops.claim_job('legacy-reader',30)")
