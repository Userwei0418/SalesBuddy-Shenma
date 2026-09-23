import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.feishu_worker import FeishuWorker
from sales_backend.integrations.feishu import FeishuError
from sales_backend.repositories.feishu_sync import FeishuRepository


class Database:
    settings = None

    @asynccontextmanager
    async def connection(self):
        yield None


@pytest.mark.asyncio
async def test_worker_reserves_every_eleventh_claim_for_fifo():
    worker = FeishuWorker(Database())
    event = dict(connection_id='connection', object_kind='customer', object_id='customer')
    worker.repository = SimpleNamespace(
        worker_config=AsyncMock(),
        schedule_reconcile=AsyncMock(), claim=AsyncMock(return_value=event),
        try_lock=AsyncMock(return_value=True), finish=AsyncMock(), unlock=AsyncMock())
    # Nonempty configuration is required by run_once.
    worker.repository.worker_config.side_effect = lambda *args, **kwargs: (
        None if kwargs.get('validation') else {'configured': True})
    worker.service = SimpleNamespace(handle=AsyncMock())
    for _ in range(22):
        assert await worker.run_once()
    assert [c.kwargs['prefer_history'] for c in worker.repository.claim.await_args_list] == (
        [False] * 10 + [True]) * 2
    assert worker.service.handle.await_count == 22
    assert worker.repository.finish.await_count == 22


@pytest.mark.asyncio
async def test_empty_queue_does_not_consume_fairness_slot():
    worker = FeishuWorker(Database())
    worker.priority_claims = 10
    worker.repository = SimpleNamespace(worker_config=AsyncMock(return_value=None),
        schedule_reconcile=AsyncMock(), claim=AsyncMock(return_value=None))
    assert not await worker.run_once()
    assert worker.priority_claims == 10


@pytest.mark.asyncio
async def test_other_worker_defers_same_object_until_object_lock_releases():
    first, second = FeishuWorker(Database()), FeishuWorker(Database())
    event = dict(id=1, connection_id='connection', object_kind='customer', object_id='customer')
    later_event = {**event, 'id': 2}
    held, entered, release = set(), asyncio.Event(), asyncio.Event()

    def try_lock(connection, key):
        if key in held:
            return False
        held.add(key)
        return True

    async def handle(*args):
        entered.set()
        await release.wait()

    repo = SimpleNamespace(
        worker_config=AsyncMock(side_effect=lambda *args, **kwargs: (
            None if kwargs.get('validation') else {'configured': True})),
        schedule_reconcile=AsyncMock(), claim=AsyncMock(side_effect=[event, later_event]),
        try_lock=AsyncMock(side_effect=try_lock), finish=AsyncMock(),
        unlock=AsyncMock(side_effect=lambda connection, key: held.remove(key)))
    first.repository = second.repository = repo
    first.service = SimpleNamespace(handle=AsyncMock(side_effect=handle))
    second.service = SimpleNamespace(handle=AsyncMock())
    task = asyncio.create_task(first.run_once())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert await asyncio.wait_for(second.run_once(), 1)
        second.service.handle.assert_not_awaited()
        repo.finish.assert_awaited_once_with(None, later_event, error='OBJECT_BUSY', retryable=True)
        assert held == {'feishu:connection:customer:customer'} and not task.done()
    finally:
        release.set()
        await task
    repo.unlock.assert_awaited_once_with(None, 'feishu:connection:customer:customer')
    assert not held
    assert repo.finish.await_args_list[-1].args == (None, event)
    assert not repo.finish.await_args_list[-1].kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize('retry_after,expected_delay', [(0, 2), (7, 7)])
async def test_write_conflict_uses_existing_failed_queue_backoff_and_releases_object_lock(
    retry_after, expected_delay,
):
    connection = SimpleNamespace(execute=AsyncMock(return_value='UPDATE 1'))

    class QueueDatabase(Database):
        @asynccontextmanager
        async def connection(self):
            yield connection

    worker = FeishuWorker(QueueDatabase())
    event = dict(id=1, lease_token='lease', attempts=1, connection_id='connection',
                 object_kind='customer', object_id='customer')
    worker.repository = SimpleNamespace(
        worker_config=AsyncMock(side_effect=lambda *args, **kwargs: (
            None if kwargs.get('validation') else {'configured': True})),
        schedule_reconcile=AsyncMock(), claim=AsyncMock(return_value=event),
        try_lock=AsyncMock(return_value=True), finish=FeishuRepository().finish, unlock=AsyncMock())
    worker.service = SimpleNamespace(handle=AsyncMock(side_effect=FeishuError(
        'WRITE_CONFLICT', retryable=True, retry_after=retry_after)))
    assert await worker.run_once()
    connection.execute.assert_awaited_once()
    # Actual repository.finish must retain the lease guard and defer just this event.
    query, *args = connection.execute.await_args.args
    assert 'WHERE id=$1 AND lease_token=$2' in query
    assert args == [1, 'lease', 'failed', 'WRITE_CONFLICT', expected_delay]
    worker.repository.unlock.assert_awaited_once_with(connection, 'feishu:connection:customer:customer')


@pytest.mark.asyncio
@pytest.mark.parametrize('attempts,expected_state,expected_delay', [(1, 'failed', 10), (10, 'dead_letter', 1024)])
async def test_unknown_internal_write_retains_native_retry_budget_and_lease_guard(
        attempts, expected_state, expected_delay):
    connection = SimpleNamespace(execute=AsyncMock(return_value='UPDATE 1'))

    class QueueDatabase(Database):
        @asynccontextmanager
        async def connection(self):
            yield connection

    worker = FeishuWorker(QueueDatabase())
    event = dict(id=1, lease_token='lease', attempts=attempts, connection_id='connection',
                 object_kind='customer', object_id='customer')
    worker.repository = SimpleNamespace(
        worker_config=AsyncMock(side_effect=lambda *args, **kwargs: (
            None if kwargs.get('validation') else {'configured': True})),
        schedule_reconcile=AsyncMock(), claim=AsyncMock(return_value=event),
        try_lock=AsyncMock(return_value=True), finish=FeishuRepository().finish, unlock=AsyncMock())
    worker.service = SimpleNamespace(handle=AsyncMock(side_effect=FeishuError(
        'FEISHU_1255001', unknown=True, retry_after=10)))
    assert await worker.run_once()
    connection.execute.assert_awaited_once()
    query, *args = connection.execute.await_args.args
    assert "WHERE id=$1 AND lease_token=$2 AND status='running' AND locked_until>clock_timestamp()" in query
    assert args == [1, 'lease', expected_state, 'FEISHU_1255001', expected_delay]
    assert event['attempts'] == attempts  # Recovery does not reset or enlarge the attempt budget.
    worker.repository.unlock.assert_awaited_once_with(connection, 'feishu:connection:customer:customer')


@pytest.mark.asyncio
async def test_idle_worker_does_not_schedule_remote_reconciliation():
    worker=FeishuWorker(Database())
    worker.repository=SimpleNamespace(worker_config=AsyncMock(return_value=None),
        claim=AsyncMock(return_value=None),schedule_reconcile=AsyncMock())
    assert await worker.run_once() is False
    worker.repository.schedule_reconcile.assert_not_awaited()
