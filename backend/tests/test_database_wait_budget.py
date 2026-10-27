"""Pool exhaustion is bounded, distinct from SQL failures, and recoverable."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from sales_backend.db import Database, DatabaseBusy
from sales_backend.main import database_busy_handler
from sales_backend.performance import RequestTimings, request_timings
from sales_backend.services import request_audit
from sales_backend.services.system_events import DatabaseEventSink
from sales_backend.worker import Worker
from tests.test_worker_scheduling import MemoryDatabase, job
from tests.test_tokens import settings


class BoundedPool:
    def __init__(self):
        self.slots = asyncio.Semaphore(1)
        self.connection = object()
        self.releases = 0

    async def acquire(self, *, timeout):
        await asyncio.wait_for(self.slots.acquire(), timeout)
        return self.connection

    async def release(self, connection, *, timeout):
        assert connection is self.connection
        assert timeout == 5
        self.releases += 1
        self.slots.release()


@pytest.mark.asyncio
async def test_pool_timeout_is_measured_and_next_request_can_recover():
    pool = BoundedPool()
    database = Database(replace(settings(), database_pool_wait_seconds=.015), pool)
    timing = RequestTimings()
    token = request_timings.set(timing)
    try:
        async with database.connection():
            with pytest.raises(DatabaseBusy):
                async with database.connection():
                    pytest.fail('exhausted pool unexpectedly acquired')
        assert timing.pool_wait_ms >= 10
        async with database.connection() as connection:
            assert connection is pool.connection
        assert pool.releases == 2
    finally:
        request_timings.reset(token)


@pytest.mark.asyncio
async def test_sql_timeout_is_not_relabelled_as_pool_exhaustion():
    pool = BoundedPool()
    database = Database(settings(), pool)
    error = TimeoutError('query timeout')
    with pytest.raises(TimeoutError) as caught:
        async with database.connection():
            raise error
    assert caught.value is error
    assert pool.releases == 1


@pytest.mark.asyncio
async def test_release_timeout_retains_its_meaning_and_separate_budget():
    failure = TimeoutError('connection reset timeout')

    class Pool(BoundedPool):
        async def release(self, connection, *, timeout):
            await super().release(connection, timeout=timeout)
            raise failure

    pool = Pool()
    database = Database(replace(settings(), database_pool_wait_seconds=.01), pool)
    with pytest.raises(TimeoutError) as caught:
        async with database.connection():
            pass
    assert caught.value is failure
    assert pool.releases == 1


@pytest.mark.asyncio
async def test_waiter_cancellation_does_not_leak_the_next_slot():
    pool = BoundedPool()
    database = Database(settings(), pool)
    waiting = asyncio.Event()

    async def waiter():
        waiting.set()
        async with database.connection():
            pytest.fail('cancelled waiter acquired')

    async with database.connection():
        task = asyncio.create_task(waiter())
        await waiting.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with database.connection():
        pass
    assert pool.releases == 2


@pytest.mark.parametrize('wait', [0, -1, float('nan'), float('inf'), 31])
def test_unbounded_or_invalid_pool_wait_configuration_fails_before_connect(wait):
    with pytest.raises(ValueError, match='DATABASE_POOL_WAIT_SECONDS'):
        Database(replace(settings(), database_pool_wait_seconds=wait))


def test_pool_exhaustion_remains_transient_for_the_bounded_event_sink():
    assert DatabaseEventSink._transient(DatabaseBusy('busy'))
    assert not DatabaseEventSink._transient(ValueError('invalid record'))
    # Inference routing catches TimeoutError as a provider timeout. A local pool
    # wait must retain its distinct type, not trigger a model fallback.
    assert not isinstance(DatabaseBusy('busy'), TimeoutError)


@pytest.mark.asyncio
async def test_worker_keeps_its_lane_after_a_transient_pool_wait(monkeypatch):
    worker = Worker(MemoryDatabase())
    worker.run_once = AsyncMock(side_effect=[DatabaseBusy('busy'), asyncio.CancelledError()])
    delay = AsyncMock()
    monkeypatch.setattr('sales_backend.worker.asyncio.sleep', delay)
    with pytest.raises(asyncio.CancelledError):
        await worker._consume(worker.lanes()[0])
    assert worker.run_once.await_count == 2
    delay.assert_awaited_once_with(5)


@pytest.mark.asyncio
async def test_import_pool_wait_failure_requeues_instead_of_permanent_failure():
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=False), execute=AsyncMock())

    class Database(MemoryDatabase):
        @asynccontextmanager
        async def transaction(self, actor):
            yield connection

    worker = Worker(Database())
    worker.jobs.fail = AsyncMock()
    item = job('visit.import')
    await worker._finish_failed(item, DatabaseBusy('busy'))
    assert connection.execute.await_args.args[2] == 'queued'
    assert worker.jobs.fail.await_args.kwargs['retryable'] is True


@pytest.mark.asyncio
async def test_pool_busy_has_retryable_status_without_claiming_business_rollback():
    app = FastAPI()
    app.add_exception_handler(DatabaseBusy, database_busy_handler)

    @app.get('/busy')
    async def busy():
        raise DatabaseBusy('internal connection or host detail')

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        result = await client.get('/busy')
    assert result.status_code == 503
    assert result.json()['code'] == 'DATABASE_BUSY'
    assert result.headers['retry-after'] == '5'
    assert result.headers['cache-control'] == 'no-store'
    assert 'internal connection' not in result.text


@pytest.mark.asyncio
async def test_separate_request_receipt_timeout_does_not_fail_committed_business(monkeypatch, caplog):
    finished = []

    class Database:
        @asynccontextmanager
        async def transaction(self, actor):
            try:
                await asyncio.Future()
                yield object()
            finally:
                finished.append(True)

    monkeypatch.setattr(request_audit, 'RECEIPT_TIMEOUT_SECONDS', .01)
    await asyncio.wait_for(request_audit.record_http_operation(
        Database(), SimpleNamespace(actor=object()), 'POST', '/api/v1/test', 201,
    ), .2)
    assert finished == [True]
    assert 'request audit receipt failed' in caplog.text


@pytest.mark.asyncio
async def test_receipt_cancellation_propagates_and_closes_transaction():
    entered = asyncio.Event()
    closed = []

    class Database:
        @asynccontextmanager
        async def transaction(self, actor):
            entered.set()
            try:
                await asyncio.Future()
                yield object()
            finally:
                closed.append(True)

    task = asyncio.create_task(request_audit.record_http_operation(
        Database(), SimpleNamespace(actor=object()), 'GET', '/api/v1/test', 200,
    ))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True]
