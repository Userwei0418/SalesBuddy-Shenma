"""An existing task can be handed over without permission to create a new one."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_operations_api import client_for
from tests.integration.test_sales_opportunity_permissions import account_fixture, native_login

pytestmark = pytest.mark.asyncio


async def make_task(client, account):
    result = await client.post('/api/v1/tasks', json={
        'association_kind': 'daily', 'description': '隔离测试：请核对本周工作计划',
        'assignee_account_code': account['account_code'],
        'due_at': (datetime.now(UTC)+timedelta(days=2)).isoformat(),
    })
    assert result.status_code == 201, result.text
    return result.json()


async def test_coordinate_without_create_uses_task_scoped_options_and_rechecks_revocation(connection):
    user, _, admin = await account_fixture(connection, ['sales'])
    peer, _, _ = await account_fixture(connection, ['sales'])
    async with await client_for(connection) as client:
        await native_login(client, user)
        task = await make_task(client, user)
        permissions = [dict(permission=p, effect='deny') for p in
                       ('task.create_daily', 'task.create_customer', 'organization.read', 'directory.read')]
        permissions.append(dict(permission='task.coordinate', effect='allow', scope='self'))
        await grant(connection, admin, user['id'], permissions)
        assert (await client.get('/api/v1/tasks/recipients')).status_code == 403
        path = f"/api/v1/tasks/{task['id']}/reassignment-options"
        options = await client.get(path, params={'q': peer['account_code'], 'page_size': 1})
        assert options.status_code == 200, options.text
        person, = options.json()['items']
        assert person['account_code'] == peer['account_code']
        assert set(person) == {'id', 'account_code', 'name', 'role', 'role_name', 'team_id', 'team', 'team_ids'}
        # account_fixture places sales appointments in a product_sales department.
        assert person['role'] == 'sales' and person['role_name'] == '产品销售'
        assert person['team_id'] in person['team_ids']
        assert person['team_id'] in {team['id'] for team in options.json()['teams']}
        assert options.json()['defaults']['team_id'] is not None
        result = await client.post(f"/api/v1/tasks/{task['id']}/events", json={
            'event_type': 'reassign', 'note': '只具备协调权限的交接验证',
            'version_no': task['version_no'], 'assignee_account_code': peer['account_code'],
        })
        assert result.status_code == 200, result.text
        assert result.json()['status'] == 'pending_confirm'
        assert any(a['user_id'] == peer['id'] and a['responsibility'] == 'owner'
                   for a in result.json()['assignees'])
        await grant(connection, admin, user['id'], [*permissions[:-1],
                    dict(permission='task.coordinate', effect='deny')], version=1)
        assert (await client.get(path)).status_code == 403


async def test_reassignment_options_require_task_visibility_coordination_and_live_status(connection):
    user, _, admin = await account_fixture(connection, ['sales'])
    peer, _, _ = await account_fixture(connection, ['sales'])
    async with await client_for(connection) as client:
        await native_login(client, peer)
        unrelated = await make_task(client, peer)
        await native_login(client, user)
        task = await make_task(client, user)
        path = f"/api/v1/tasks/{task['id']}/reassignment-options"
        for task_id in (unrelated['id'], str(uuid4())):
            result = await client.get(f'/api/v1/tasks/{task_id}/reassignment-options')
            assert result.status_code == 404, result.text
        # Broad read access never expands a self-only coordination grant.
        await grant(connection, admin, user['id'], [
            dict(permission='task.coordinate', effect='allow', scope='self'),
            dict(permission='task.read', effect='allow', scope='workspace'),
        ])
        result = await client.get(f"/api/v1/tasks/{unrelated['id']}/reassignment-options")
        assert result.status_code in {403, 404}, result.text
        cancelled = await client.post(f"/api/v1/tasks/{task['id']}/events", json={
            'event_type': 'cancel', 'note': '隔离测试已结束', 'version_no': task['version_no'],
        })
        assert cancelled.status_code == 200, cancelled.text
        result = await client.get(path)
        assert result.status_code == 409, result.text
