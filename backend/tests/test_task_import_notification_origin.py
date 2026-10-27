"""Group announcements belong only to weekly dispatch from an administrator console session."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.api import task_imports


def identity(role='administrator', channel='web', method='password'):
    return SimpleNamespace(actor=SimpleNamespace(role=SimpleNamespace(value=role)),
                           client_channel=channel, auth_method=method)


@pytest.mark.asyncio
@pytest.mark.parametrize(('role', 'channel', 'method', 'expected'), [
    ('administrator', 'web', 'password', 'admin_weekly_dispatch'),
    ('administrator', 'wechat-mini-program', 'password', ''),
    ('administrator', 'web', 'demo', ''),
    ('sales', 'web', 'password', ''),
    ('operations', 'web', 'password', ''),
    ('sales', 'wechat-mini-program', 'password', ''),
])
async def test_only_persisted_admin_web_identity_enables_origin_and_restores_prior(role, channel, method, expected):
    connection = SimpleNamespace(fetchval=AsyncMock(return_value='prior_origin'), execute=AsyncMock())
    async with task_imports.task_dispatch_notification_origin(connection, identity(role, channel, method)):
        assert connection.execute.await_args.args == (
            "SELECT set_config('app.task_group_notification_origin',$1,true)", expected)
    assert connection.execute.await_args.args[-1] == 'prior_origin'
    assert connection.execute.await_count == 2


@pytest.mark.asyncio
async def test_failed_confirmation_does_not_mask_original_database_failure_with_restore_query():
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=None), execute=AsyncMock())
    original = asyncpg.CheckViolationError('invalid task import')
    with pytest.raises(asyncpg.CheckViolationError) as raised:
        async with task_imports.task_dispatch_notification_origin(connection, identity()):
            raise original
    assert raised.value is original
    assert connection.execute.await_count == 1  # LOCAL setting rolls back with the outer business transaction.


@pytest.mark.asyncio
async def test_confirm_route_sets_origin_only_inside_mutation_callback_and_restores_before_detail(monkeypatch):
    batch_id, key = str(uuid4()), uuid4()
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=None), execute=AsyncMock())
    who = identity()

    @asynccontextmanager
    async def transaction(actor):
        assert actor is who.actor
        yield connection

    async def confirm(conn, actor, batch, body):
        assert connection.execute.await_args.args[-1] == 'admin_weekly_dispatch'
        assert (conn, actor, batch) == (connection, who.actor, batch_id)

    async def detail(conn, actor, batch):
        assert connection.execute.await_args.args[-1] == ''
        return {'id': batch, 'status': 'confirmed'}

    async def execute(conn, actor, given_key, operation, payload, callback):
        assert connection.execute.await_count == 0
        assert given_key == key and operation == f'task-imports.confirm:{batch_id}'
        return await callback()

    service = SimpleNamespace(confirm=AsyncMock(side_effect=confirm), detail=AsyncMock(side_effect=detail))
    monkeypatch.setattr(task_imports, 'TaskImportService', lambda *args: service)
    monkeypatch.setattr(task_imports, 'execute_mutation', execute)
    response = await task_imports.confirm_task_import(
        batch_id, SimpleNamespace(model_dump=lambda: {}), who,
        SimpleNamespace(transaction=transaction), None, key)
    assert response == {'id': batch_id, 'status': 'confirmed'}
    service.confirm.assert_awaited_once()
