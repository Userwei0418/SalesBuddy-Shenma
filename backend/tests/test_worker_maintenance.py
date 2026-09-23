import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.services.worker_maintenance import WorkerMaintenance
from sales_backend.worker import Worker


class DatabaseStub:
    def __init__(self, execute):
        self.execute = execute
        self.settings = SimpleNamespace(worker_id="probe", worker_lock_seconds=30, worker_poll_seconds=0.01)

    @asynccontextmanager
    async def connection(self):
        yield SimpleNamespace(fetchval=self.execute)


@pytest.mark.asyncio
async def test_failed_monitor_has_bounded_retry_and_other_monitors_continue(caplog):
    calls = []

    async def execute(query):
        calls.append(query)
        if "evaluate_ai_usage_alerts" in query:
            raise ValueError("invalid monitoring rule")

    scheduler = WorkerMaintenance(DatabaseStub(execute))
    results = await asyncio.gather(*(scheduler.run_once(name) for name in scheduler.INTERVALS))
    assert results == [30, 86400, 300, 1800]
    assert len(calls) == 4
    assert "worker maintenance failed: ai_usage" in caplog.text


@pytest.mark.asyncio
async def test_monitor_timeout_releases_connection_and_uses_retry_delay():
    released = []

    async def block(_):
        await asyncio.sleep(10)

    database = DatabaseStub(block)
    original = database.connection

    @asynccontextmanager
    async def connection():
        try:
            async with original() as value:
                yield value
        finally:
            released.append(True)

    database.connection = connection
    scheduler = WorkerMaintenance(database)
    scheduler.TIMEOUT_SECONDS = 0.01
    assert await scheduler.run_once("log_retention") == 30
    assert released == [True]


@pytest.mark.asyncio
async def test_monitor_cancellation_is_not_converted_to_a_retry():
    async def cancelled(_):
        raise asyncio.CancelledError

    scheduler = WorkerMaintenance(DatabaseStub(cancelled))
    with pytest.raises(asyncio.CancelledError):
        await scheduler.run_once("ai_usage")


@pytest.mark.asyncio
async def test_job_claim_does_not_wait_for_blocked_maintenance(monkeypatch):
    started, stopped, claimed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class BlockedMaintenance:
        def __init__(self, database):
            pass

        async def run_forever(self):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

    monkeypatch.setattr("sales_backend.worker.WorkerMaintenance", BlockedMaintenance)
    worker = Worker(DatabaseStub(AsyncMock()))

    async def claim(*args, **kwargs):
        claimed.set()
        return None

    worker.jobs.claim = claim
    running = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(claimed.wait(), 1)
        assert not stopped.is_set()
    finally:
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    assert stopped.is_set()
