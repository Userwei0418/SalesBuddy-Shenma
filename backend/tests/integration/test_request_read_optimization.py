"""Real PostgreSQL regression for request snapshots, refresh rollback and paging."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sales_backend.domain.agent import RoleCode
from sales_backend.repositories.passwords import PasswordRepository
from sales_backend.repositories.tasks import TaskRepository
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


async def test_management_request_reads_credentials_once_and_rechecks_next_request(connection, monkeypatch):
    calls = []
    original = PasswordRepository.session_credentials
    async def read(self, conn, session_id):
        calls.append(session_id)
        return await original(self, conn, session_id)
    async with await client_for(connection) as client:
        await sign_in(client)
        monkeypatch.setattr(PasswordRepository, 'session_credentials', read)
        for count in (1, 2):
            assert (await client.get('/api/v1/console/organization')).status_code == 200
            assert len(calls) == count


@pytest.mark.parametrize('step', ['credentials', 'capabilities'])
async def test_refresh_failure_rolls_back_token_rotation(connection, monkeypatch, step):
    from sales_backend.services import auth
    async def fail(*args, **kwargs):
        raise RuntimeError('injected response dependency failure')
    async with await client_for(connection) as client:
        await sign_in(client)
        with monkeypatch.context() as patch:
            if step == 'credentials':
                patch.setattr(PasswordRepository, 'session_credentials', fail)
            else:
                patch.setattr(auth, 'capability_snapshot', fail)
            with pytest.raises(RuntimeError, match='injected response'):
                await client.post('/api/v1/console/auth/refresh')
        # The same original cookie remains valid after the failed transaction.
        result = await client.post('/api/v1/console/auth/refresh')
        assert result.status_code == 200, result.text


@pytest.mark.parametrize('role', [RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER])
async def test_task_paging_plans_and_overview_match_complete_visible_facts(connection, actor_factory, role):
    await actor_factory(role)
    repo = TaskRepository()
    results = []
    for mode in ('force_custom_plan', 'force_generic_plan'):
        await connection.execute('SET LOCAL plan_cache_mode=' + mode)
        all_rows = await repo.list(connection, status=None, customer_id=None, limit=None)
        pages = []
        for offset in range(0,len(all_rows),3):
            pages.extend(await repo.list(connection,status=None,customer_id=None,limit=3,offset=offset))
        assert pages == all_rows
        results.append(all_rows)
    assert results[0] == results[1]
    rows = results[0]
    overview = await repo.overview(connection, task_ids=[r['id'] for r in rows[:100]])
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    def day(value):
        return value.astimezone(ZoneInfo('Asia/Shanghai')).date() if value else None
    assert overview['metrics'] == {
        'today_completed': sum(r['status']=='completed' and day(r['completed_at'])==today for r in rows),
        'today_pending': sum(r['status'] not in ('completed','cancelled') and day(r['due_at'])==today for r in rows),
        'all_pending': sum(r['status'] not in ('completed','cancelled') for r in rows),
    }
    by_id = {r['id']: r for r in rows}
    for item in overview['items']:
        detail = by_id[item['id']]
        for field in ('status','completion_note','handover_required','last_event_type','last_event_note'):
            assert item[field] == detail[field]


async def test_named_recipient_task_overview_serializes_boolean_and_tracks_acceptance(connection):
    """A direct personal assignment has no target_position and needs no FDE handover."""
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, 'ZJ001')
        created = await client.post('/api/v1/tasks', json={
            'description': '指定个人接收的待办概览契约回归',
            'assignee_account_code': 'XS001',
            'due_at': (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        }, headers={'Idempotency-Key': str(uuid4())})
        assert created.status_code == 201, created.text
        task = created.json()
        assert task['target_position'] is None
        assert task['status'] == 'pending_confirm'
        assert task['handover_required'] is False

        await business_login(client, 'XS001')
        for expected_status in ('pending_confirm', 'pending_execution'):
            # Exercise the actual FastAPI response_model, not only equal SQL rows.
            overview = await client.get('/api/v1/tasks/overview', params={'task_ids': task['id']})
            assert overview.status_code == 200, overview.text
            assert len(overview.json()['items']) == 1
            item = overview.json()['items'][0]
            assert item['status'] == expected_status
            assert item['handover_required'] is False
            detail = await client.get('/api/v1/tasks/' + task['id'])
            assert detail.status_code == 200, detail.text
            assert detail.json()['handover_required'] is False
            listed = await client.get('/api/v1/tasks', params={'inbox': True, 'page_size': 100})
            assert listed.status_code == 200, listed.text
            assert next(row for row in listed.json()['items'] if row['id'] == task['id'])['handover_required'] is False
            if expected_status == 'pending_confirm':
                accepted = await client.post('/api/v1/tasks/' + task['id'] + '/events', json={'event_type': 'accept'})
                assert accepted.status_code == 200, accepted.text
