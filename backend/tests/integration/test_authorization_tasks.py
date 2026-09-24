"""Feature grants, workflow eligibility and actual task scopes stay independent."""
from datetime import UTC, datetime, timedelta

import pytest

from sales_backend.db import set_request_context
from sales_backend.domain.tasks import TaskForbidden
from sales_backend.repositories.tasks import TaskRepository
from sales_backend.services.tasks import TaskService
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_authorization_store import resolve
from tests.integration.test_sales_opportunity_permissions import account_fixture
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def make_task(connection, creator, account):
    await set_request_context(connection, creator)
    return await TaskService().create(connection, actor=creator, description="验证独立功能和接收岗位",
        due_at=datetime.now(UTC) + timedelta(days=1), priority_code="normal", assignee_account_code=account,
        association_kind="daily")


async def test_recipient_can_act_under_any_valid_display_role_and_personal_deny_wins(connection):
    user, _, admin = await account_fixture(connection, ['sales', 'fde'])
    creator = await actor(connection, 'XS001')
    task = await make_task(connection, creator, user['account_code'])
    recipient = await resolve(connection, admin, user, 'sales')
    assert (await TaskRepository().detail(connection, task_id=task['id']))['requires_action']
    accepted = await TaskService().apply_event(connection, actor=recipient, task_id=task['id'], event_type='accept', note=None)
    assert accepted['status'] == 'pending_execution'
    await grant(connection, admin, user['id'], [dict(permission='task.complete', effect='deny')])
    await set_request_context(connection, recipient)
    with pytest.raises(TaskForbidden):
        await TaskService().complete(connection, actor=recipient, task_id=task['id'], note='不应完成')
    assert (await TaskRepository().detail(connection, task_id=task['id']))['status'] == 'pending_execution'
    await grant(connection, admin, user['id'], [], version=1)
    await set_request_context(connection, recipient)
    completed = await TaskService().complete(connection, actor=recipient, task_id=task['id'], note='已完成')
    assert completed['status'] == 'pending_review'


async def test_task_assign_permission_is_independent_from_create_daily(connection):
    user, _, admin = await account_fixture(connection, ['sales'])
    other = await actor(connection, 'XS001')
    await grant(connection, admin, user['id'], [dict(permission='task.assign', effect='deny')])
    creator = await resolve(connection, admin, user, 'sales')
    task = await make_task(connection, creator, user['account_code'])
    assert task['association_kind'] == 'daily'
    with pytest.raises(PermissionError):
        await make_task(connection, creator, 'XS001')
    # A create/assign grant never makes the recipient's other customer data visible.
    await set_request_context(connection, other)
    assert not await connection.fetchval('SELECT id FROM workflow.task WHERE id=$1::uuid', task['id'])
