"""Exercise the real worker with a deterministic queue and blocking handlers.

No model/database is mocked as production evidence: these tests prove scheduling,
context isolation and cancellation. SQL lease behavior is covered in integration.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from sales_backend.job_context import JobLeaseLost, current_job_lease
from sales_backend.repositories.jobs import ClaimedJob
from sales_backend.request_metadata import current_actor
from sales_backend.worker import InvalidJobActor, Worker


def job(kind):
    return ClaimedJob(str(uuid4()), str(uuid4()), kind, str(uuid4()),
                      {"user_id": str(uuid4()), "role": "manager", "data_scope": "workspace"},
                      1, 3, str(uuid4()), 2.5)


class MemoryDatabase:
    def __init__(self, **settings):
        self.settings = SimpleNamespace(worker_id="scheduler-test", worker_poll_seconds=0.01,
                                        worker_lock_seconds=3, **settings)
        self.borrowed = 0

    @asynccontextmanager
    async def connection(self):
        self.borrowed += 1
        try:
            yield object()
        finally:
            self.borrowed -= 1

    def transaction(self, actor):
        return self.connection()


class MemoryQueue:
    def __init__(self, jobs):
        self.pending = list(jobs)
        self.claimed = []
        self.heartbeat = AsyncMock()

    async def claim(self, connection, *, job_types, exclude_types, **kwargs):
        for index, value in enumerate(self.pending):
            if job_types is None or (value.job_type in job_types) != exclude_types:
                self.claimed.append(value)
                return self.pending.pop(index)
        return None


@pytest.fixture(autouse=True)
def maintenance(monkeypatch):
    class IdleMaintenance:
        def __init__(self, database):
            pass

        async def run_forever(self):
            await asyncio.Future()

    monkeypatch.setattr("sales_backend.worker.WorkerMaintenance", IdleMaintenance)


async def stop(task):
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)


@pytest.mark.asyncio
async def test_long_import_does_not_claim_more_imports_or_block_short_agent(caplog):
    long, short = job("visit.import"), job("agent.run")
    queue = MemoryQueue([long, *[job("visit.import") for _ in range(20)], short])
    worker = Worker(MemoryDatabase())
    worker.jobs = queue
    import_started, agent_finished, import_stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    worker._finish_succeeded = AsyncMock()
    worker._finish_failed = AsyncMock()

    async def handle(value):
        assert current_job_lease.get().job_id == value.id
        assert current_actor.get().user_id == value.payload["user_id"]
        assert worker.database.borrowed == 0, "No connection held while the handler awaits a remote result"
        if value.job_type == "visit.import":
            import_started.set()
            try:
                await asyncio.Future()
            finally:
                import_stopped.set()
        else:
            agent_finished.set()

    worker._handle_job = handle
    caplog.set_level("INFO", logger="sales_backend.worker")
    running = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(import_started.wait(), 1)
        await asyncio.wait_for(agent_finished.wait(), 1)
        await asyncio.sleep(0.05)
        assert queue.claimed == [long, short]
        worker._finish_succeeded.assert_awaited_once_with(short)
        assert "queue_wait_ms=2500.0" in caplog.text
        assert "execution_ms=" in caplog.text
    finally:
        await stop(running)
    assert import_stopped.is_set()
    worker._finish_failed.assert_not_awaited()  # shutdown is not an application failure/retry
    assert current_actor.get() is None and current_job_lease.get() is None


@pytest.mark.asyncio
async def test_all_lanes_bound_claims_and_release_only_finished_slots():
    kinds = ["visit.import", "agent.run", "business.advice", "battle_map.review", "sales_competency.review"]
    worker = Worker(MemoryDatabase())
    queue = MemoryQueue([job(kind) for kind in kinds for _ in range(8)])
    worker.jobs = queue
    active, stopped, releases = {}, [], {}
    four_started, next_started = asyncio.Event(), asyncio.Event()
    worker._finish_succeeded, worker._finish_failed = AsyncMock(), AsyncMock()

    async def handle(value):
        active[value.id] = value
        releases[value.id] = asyncio.Event()
        if len(queue.claimed) == 4:
            four_started.set()
        if len(queue.claimed) == 5:
            next_started.set()
        try:
            await releases[value.id].wait()
        finally:
            active.pop(value.id)
            stopped.append(value.id)

    worker._handle_job = handle
    running = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(four_started.wait(), 1)
        await asyncio.sleep(0.05)
        assert len(queue.claimed) == 4 and len(active) == 4
        assert [item.job_type for item in queue.claimed].count("visit.import") == 1
        assert [item.job_type for item in queue.claimed].count("agent.run") == 2
        assert [item.job_type for item in queue.claimed].count("battle_map.review") == 1
        released = next(value for value in active.values() if value.job_type == "agent.run")
        releases[released.id].set()
        await asyncio.wait_for(next_started.wait(), 1)
        assert len(queue.claimed) == 5 and len(active) == 4
        assert queue.claimed[-1].job_type == "agent.run"
        worker._finish_succeeded.assert_awaited_once_with(released)
    finally:
        await stop(running)
    assert not active and len(stopped) == 5
    worker._finish_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_heartbeat_loss_cancels_handler_and_never_acknowledges_result():
    value, cancelled = job("agent.run"), asyncio.Event()
    worker = Worker(MemoryDatabase())
    worker.jobs = MemoryQueue([value])
    worker.jobs.heartbeat.side_effect = JobLeaseLost("expired attempt")
    worker._finish_succeeded, worker._finish_failed = AsyncMock(), AsyncMock()

    async def handle(_):
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    worker._handle_job = handle
    assert await asyncio.wait_for(worker.run_once(), 2)
    assert cancelled.is_set()
    worker._finish_succeeded.assert_not_awaited()
    worker._finish_failed.assert_not_awaited()
    assert current_job_lease.get() is None and current_actor.get() is None


@pytest.mark.asyncio
async def test_completion_lease_loss_does_not_fail_a_new_attempt():
    value = job("business.advice")
    worker = Worker(MemoryDatabase())
    worker.jobs = MemoryQueue([value])
    worker._handle_job = AsyncMock()
    worker._finish_succeeded = AsyncMock(side_effect=JobLeaseLost("claimed elsewhere"))
    worker._finish_failed = AsyncMock()
    assert await worker.run_once()
    worker._finish_failed.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("shutdown", [False, True])
async def test_handler_returning_after_cancellation_never_acknowledges_result(shutdown):
    value = job("agent.run")
    started, returned = asyncio.Event(), asyncio.Event()
    worker = Worker(MemoryDatabase())
    worker.jobs = MemoryQueue([value])
    if not shutdown:
        worker.jobs.heartbeat.side_effect = JobLeaseLost("lease no longer belongs to this attempt")
    worker._finish_succeeded, worker._finish_failed = AsyncMock(), AsyncMock()

    async def handle(_):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            returned.set()  # Simulate a provider adapter suppressing cancellation.
            return

    worker._handle_job = handle
    running = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(started.wait(), 1)
    if shutdown:
        await stop(running)
    else:
        assert await asyncio.wait_for(running, 2)
    assert returned.is_set()
    worker._finish_succeeded.assert_not_awaited()
    worker._finish_failed.assert_not_awaited()
    assert current_job_lease.get() is None and current_actor.get() is None


@pytest.mark.asyncio
async def test_unknown_job_is_consumed_by_review_lane_and_failed_observably():
    unknown, completed = job("future.unsupported"), asyncio.Event()
    worker = Worker(MemoryDatabase())
    worker.jobs = MemoryQueue([unknown])
    worker._finish_succeeded = AsyncMock()

    async def fail(value, error):
        assert value == unknown and isinstance(error, ValueError)
        assert "unsupported job type" in str(error)
        completed.set()

    worker._finish_failed = fail
    running = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(completed.wait(), 1)
        assert worker.jobs.claimed == [unknown]
        worker._finish_succeeded.assert_not_awaited()
    finally:
        await stop(running)


@pytest.mark.asyncio
async def test_retryable_handler_failure_leaves_other_slots_running():
    failed, completed = job("agent.run"), job("business.advice")
    worker = Worker(MemoryDatabase())
    worker.jobs = MemoryQueue([failed, completed])
    worker._finish_succeeded, worker._finish_failed = AsyncMock(), AsyncMock()

    async def handle(value):
        if value.id == failed.id:
            raise TimeoutError("test upstream timeout")

    worker._handle_job = handle
    assert await asyncio.gather(worker.run_once(), worker.run_once()) == [True, True]
    worker._finish_succeeded.assert_awaited_once_with(completed)
    worker._finish_failed.assert_awaited_once()
    assert isinstance(worker._finish_failed.await_args.args[1], TimeoutError)


@pytest.mark.parametrize("change", [
    {"role": "removed_legacy_role"}, {"role": ""}, {"data_scope": "invalid_scope"},
    {"data_scope": None}, {"user_id": "not-a-uuid"}, {"user_id": str(uuid4()).split("-")[0]},
    {"user_id": "00000000-0000-0000-0000-000000000000"}, {"team_ids": "not-a-list"},
    {"team_ids": ["not-a-uuid"]}, {"workspace_id": str(uuid4())},
])
def test_invalid_queued_identity_never_defaults_to_privileged_actor(change):
    value = job("agent.run")
    with pytest.raises(InvalidJobActor, match="queued job identity is invalid"):
        Worker._job_actor(replace(value, payload={**value.payload, **change}))


@pytest.mark.parametrize("payload", [None, [], "payload", {}, {"user_id": str(uuid4())}])
def test_missing_queued_identity_is_rejected(payload):
    with pytest.raises(InvalidJobActor):
        Worker._job_actor(replace(job("agent.run"), payload=payload))


@pytest.mark.asyncio
async def test_invalid_identity_is_rejected_without_cancelling_another_lane():
    long, invalid, next_job = job("visit.import"), job("agent.run"), job("agent.run")
    invalid = replace(invalid, payload={**invalid.payload, "role": "removed_legacy_role"})
    started, stopped, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Queue(MemoryQueue):
        async def claim(self, connection, **kwargs):
            if "agent.run" in (kwargs.get("job_types") or ()):
                await started.wait()
            return await super().claim(connection, **kwargs)

    worker = Worker(MemoryDatabase())
    worker.jobs = Queue([long, invalid, next_job])
    worker._finish_succeeded = AsyncMock(side_effect=lambda value: completed.set())
    worker._finish_failed = AsyncMock()

    async def reject(value):
        assert value == invalid
        assert current_actor.get() is None and current_job_lease.get() is None

    worker._reject_invalid_job = AsyncMock(side_effect=reject)

    async def handle(value):
        assert value != invalid
        if value == long:
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
    worker._handle_job = handle
    running = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(completed.wait(), 1)
        await asyncio.sleep(0)
        assert not stopped.is_set() and not running.done()
        worker._reject_invalid_job.assert_awaited_once_with(invalid)
        worker._finish_succeeded.assert_awaited_once_with(next_job)
        worker._finish_failed.assert_not_awaited()
    finally:
        await stop(running)
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_invalid_identity_with_lost_lease_is_not_acknowledged_twice():
    value = replace(job("agent.run"), payload={})
    worker = Worker(MemoryDatabase())
    worker.jobs = MemoryQueue([value])
    worker._reject_invalid_job = AsyncMock(side_effect=JobLeaseLost("new attempt owns this job"))
    worker._handle_job, worker._finish_succeeded, worker._finish_failed = AsyncMock(), AsyncMock(), AsyncMock()
    assert await worker.run_once()
    worker._reject_invalid_job.assert_awaited_once_with(value)
    worker._handle_job.assert_not_awaited()
    worker._finish_succeeded.assert_not_awaited()
    worker._finish_failed.assert_not_awaited()
    assert current_actor.get() is None and current_job_lease.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["dead_letter", "succeeded", None])
async def test_invalid_job_rejection_passes_only_job_and_lease_to_terminal_function(caplog, outcome):
    value = replace(job("agent.run"), payload={"role": "private-invalid-role", "workspace_id": str(uuid4())})
    calls = []

    class Connection:
        async def fetchval(self, sql, *args):
            calls.append((sql, args))
            return outcome

    class Database(MemoryDatabase):
        @asynccontextmanager
        async def connection(self):
            yield Connection()

    worker = Worker(Database())
    if outcome is None:
        with pytest.raises(JobLeaseLost):
            await worker._reject_invalid_job(value)
    else:
        await worker._reject_invalid_job(value)
    assert len(calls) == 1
    sql, args = calls[0]
    assert args == (value.id, value.lease_token)
    assert sql == "SELECT ops.reject_invalid_job_actor($1::uuid,$2::uuid)"
    assert "private-invalid-role" not in caplog.text


@pytest.mark.parametrize("capacity", [0, -1, 9])
def test_invalid_lane_capacity_rejected(capacity):
    with pytest.raises(ValueError):
        Worker(MemoryDatabase(worker_import_concurrency=capacity)).lanes()


def test_total_capacity_cannot_exceed_process_budget():
    worker = Worker(MemoryDatabase(worker_import_concurrency=8, worker_interactive_concurrency=8,
                                   worker_review_concurrency=1))
    with pytest.raises(ValueError):
        worker.lanes()


def test_slot_settings_are_loaded_without_enlarging_api_pool(monkeypatch):
    from sales_backend.config import get_settings
    monkeypatch.setenv("WORKER_IMPORT_CONCURRENCY", "2")
    monkeypatch.setenv("WORKER_INTERACTIVE_CONCURRENCY", "3")
    monkeypatch.setenv("WORKER_REVIEW_CONCURRENCY", "2")
    monkeypatch.setenv("WORKER_DATABASE_MAX_POOL_SIZE", "10")
    monkeypatch.setenv("DATABASE_MAX_POOL_SIZE", "6")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert [lane.capacity for lane in Worker(SimpleNamespace(settings=settings)).lanes()] == [2, 3, 2]
        assert settings.worker_database_max_pool_size == 10
        assert settings.database_max_pool_size == 6
        assert replace(settings, database_max_pool_size=10).worker_id == settings.worker_id
    finally:
        get_settings.cache_clear()
