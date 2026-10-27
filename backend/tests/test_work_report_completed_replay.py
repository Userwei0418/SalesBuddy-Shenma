"""ACK-loss replay must not need terminal projection UPDATE eligibility.

These offline tests model the relevant policy distinction, not PostgreSQL RLS
itself. The isolated Review pipeline verifier exercises the actual Worker role.
"""

from copy import deepcopy
from uuid import uuid4

import pytest

from sales_backend.contracts.work_report import SnapshotSelection
from sales_backend.domain.work_report import WorkReportError
from sales_backend.job_context import JobLease, JobLeaseLost
from sales_backend.repositories.work_report import WorkReportRepository
from tests.test_work_report_repository import (
    ACTOR,
    PERIOD,
    SCOPE,
    FakeReportConnection,
    generate,
    lease,
    no_network,  # noqa: F401 -- explicitly reuse the offline fixture
    prepare,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("no_network")]


class SnapshotUpdatePolicyConnection(FakeReportConnection):
    async def fetchrow(self, sql, *args):
        if "SELECT * FROM agent.work_report_snapshot" in sql and "FOR UPDATE" in sql:
            row = self.rows["snapshot"].get(args[0])
            if row is not None and row["status"] != "preparing":
                self.query(sql, args)
                return None
        return await super().fetchrow(sql, *args)


async def completed(kind="snapshot"):
    repo, connection = WorkReportRepository(), SnapshotUpdatePolicyConnection()
    scheme, snapshot = await prepare(repo, connection)
    if kind == "generate":
        view, job, _ = await generate(repo, connection, scheme, snapshot)
    else:
        view = snapshot
        job_id = connection.rows["snapshot"][str(snapshot.id)]["job_id"]
        job = JobLease(job_id, connection.jobs[job_id]["token"])
    connection.statements.clear()
    return repo, connection, view, job


@pytest.mark.parametrize("kind", ["snapshot", "generate"])
async def test_committed_result_replays_without_projection_update_or_new_effect(kind):
    repo, connection, view, job = await completed(kind)
    before = deepcopy((connection.rows, connection.effects, connection.events))
    with lease(job):
        first = await repo.get_completed_job(connection, ACTOR, kind, view.id)
        second = await repo.get_completed_job(connection, ACTOR, kind, view.id)
    assert first.model_dump() == second.model_dump() == view.model_dump()
    assert (connection.rows, connection.effects, connection.events) == before
    projection_reads = [sql for sql, _ in connection.statements
                        if "SELECT * FROM agent.work_report_snapshot" in sql
                        or "SELECT * FROM agent.work_report_run" in sql]
    assert projection_reads and all("FOR UPDATE" not in sql for sql in projection_reads)
    assert any("SELECT 1 FROM ops.job" in sql and "FOR UPDATE" in sql
               for sql, _ in connection.statements)


@pytest.mark.parametrize("kind", ["snapshot", "generate"])
async def test_completed_result_without_committed_effect_is_rejected(kind):
    repo, connection, view, job = await completed(kind)
    connection.effects.remove(job.job_id)
    before = deepcopy(connection.rows)
    with lease(job), pytest.raises(WorkReportError) as error:
        await repo.get_completed_job(connection, ACTOR, kind, view.id)
    assert error.value.code == "WORK_REPORT_RUN_TERMINAL"
    assert job.job_id not in connection.effects
    assert connection.rows == before


@pytest.mark.parametrize("fault", ["missing", "other_job", "token", "expired", "workspace", "aggregate", "type"])
async def test_completed_replay_still_requires_exact_current_live_lease(fault):
    repo, connection, view, job = await completed()
    current = job
    if fault == "missing":
        current = None
    elif fault == "other_job":
        current = JobLease(str(uuid4()), job.token)
    elif fault == "token":
        current = JobLease(job.job_id, str(uuid4()))
    elif fault == "expired":
        connection.lease_valid = False
    else:
        key = {"workspace": "workspace_id", "aggregate": "aggregate_id", "type": "job_type"}[fault]
        connection.jobs[job.job_id][key] = str(uuid4())
    with lease(current), pytest.raises(JobLeaseLost):
        await repo.get_completed_job(connection, ACTOR, "snapshot", view.id)
    assert not any("FROM ops.job_effect" in sql for sql, _ in connection.statements)


@pytest.mark.parametrize("kind", ["snapshot", "generate"])
@pytest.mark.parametrize("fault", ["source_revoked", "snapshot_changed", "write_revoked"])
async def test_completed_replay_keeps_source_hash_and_current_action_guards(kind, fault):
    repo, connection, view, job = await completed(kind)
    snapshot = next(iter(connection.rows["snapshot"].values()))
    if fault == "source_revoked":
        connection.inaccessible.add(snapshot["payload"]["sources"][0]["record_id"])
    elif fault == "snapshot_changed":
        snapshot["payload"]["permission_version"] = "forged"
    else:
        connection.denied.add("work_report.write")
    with lease(job), pytest.raises(WorkReportError) as error:
        await repo.get_completed_job(connection, ACTOR, kind, view.id)
    expected = "WORK_REPORT_SNAPSHOT_CHANGED" if fault == "snapshot_changed" else "WORK_REPORT_PERMISSION_CHANGED"
    assert error.value.code == expected
    assert not any("FROM ops.job_effect" in sql for sql, _ in connection.statements)


async def test_pending_admission_has_no_receipt_and_mutation_path_keeps_projection_lock():
    repo, connection = WorkReportRepository(), SnapshotUpdatePolicyConnection()
    view = await repo.create_snapshot(connection, ACTOR, SnapshotSelection(sources=["visit"]),
                                      period=PERIOD, scope=SCOPE)
    job = connection.job(view.id, "snapshot")
    await repo.attach_snapshot_job(connection, ACTOR, view.id, job.job_id)
    connection.statements.clear()
    with lease(job):
        assert await repo.get_completed_job(connection, ACTOR, "snapshot", view.id) is None
        assert not any("FROM ops.job_effect" in sql for sql, _ in connection.statements)
        connection.statements.clear()
        current = await repo.snapshot_for_job(connection, ACTOR, view.id, lock=True)
    assert current["status"] == "preparing" and not connection.effects
    assert any("SELECT * FROM agent.work_report_snapshot" in sql and "FOR UPDATE" in sql
               for sql, _ in connection.statements)
