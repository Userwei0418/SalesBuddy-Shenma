"""Management browsing through real runtime RLS and HTTP; no external integrations."""
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from sales_backend.repositories.task_management import TaskManagementRepository
from tests.integration.feishu_fixtures import seed_execute
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_authorization_tasks import make_task
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login
from tests.integration.test_task_imports import confirmation, preview, submit

pytestmark = pytest.mark.asyncio


async def seed_tasks(connection):
    admin = await actor(connection, 'ADMIN001')
    person = await actor(connection, 'XS001')
    await seed_execute(connection, """WITH inserted AS (
      INSERT INTO workflow.task(workspace_id,creator_user_ref_id,creator_team_id,title,description,
        association_kind,status,created_at,due_at,completed_at)
      SELECT $1::uuid,$2::uuid,$3::uuid,'管理任务'||n,'完整任务描述'||n,'daily',
        CASE WHEN n>40 THEN 'completed' ELSE 'pending_execution' END,
        '2026-09-01 00:00+08'::timestamptz,
        '2026-09-30 23:59:59+08'::timestamptz,
        CASE WHEN n>40 THEN '2026-09-20 00:00+08'::timestamptz END
      FROM generate_series(1,45) n RETURNING id,workspace_id)
      INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role)
      SELECT id,workspace_id,$4::uuid,$3::uuid,'sales' FROM inserted""",
      admin.workspace_id, admin.user_id, person.team_ids[0], person.user_id)
    return admin, person


async def test_full_counts_pagination_date_overlap_and_inactive_owners(connection):
    admin, person = await seed_tasks(connection)
    await actor(connection, 'ADMIN001')
    repo = TaskManagementRepository()
    first = await repo.page(connection, limit=20)
    assert first['summary']['total'] == 45 and first['summary']['pending_count'] == 40
    assert first['summary']['completed_count'] == 5 and first['summary']['undated_count'] == 0
    assert len(first['items']) == 20 and first['next_offset'] == 20
    second = await repo.page(connection, limit=20, offset=20)
    assert not {t['id'] for t in first['items']} & {t['id'] for t in second['items']}
    overlap = await repo.page(connection, date_from=date(2026, 9, 10), date_to=date(2026, 9, 10))
    assert overlap['summary']['total'] == 45  # Includes spans, not just deadlines on this day.
    outside = await repo.page(connection, date_from=date(2026, 10, 1), undated='exclude')
    assert outside['summary']['total'] == 0
    assert (await repo.page(connection, date_from=date(2026, 10, 1)))['summary']['total'] == 0
    assert (await repo.page(connection, undated='only'))['summary']['total'] == 0
    assert (await repo.page(connection, member_id=str(uuid4())))['summary']['total'] == 0
    await seed_execute(connection, "UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", person.user_id)
    owners = await repo.owners(connection, query='XS001')
    assert len(owners['items']) == 1 and owners['items'][0]['active'] is False
    rows = await repo.page(connection, member_id=person.user_id)
    assert rows['items'][0]['owner_active'] is False and rows['items'][0]['owner_name'] == 'XS001'


async def test_existing_scopes_mine_read_denial_and_directory_without_create(connection):
    admin = await actor(connection, 'ADMIN001')
    task = await make_task(connection, admin, 'XS001')
    await actor(connection, 'XS002')
    repo = TaskManagementRepository()
    assert (await repo.page(connection))['summary']['total'] == 0
    assert (await repo.owners(connection))['items'] == []
    recipient = await actor(connection, 'XS001')
    assert (await repo.page(connection, scope='mine'))['items'][0]['id'] == task['id']
    await grant(connection, admin, recipient.user_id, [
        dict(permission='task.create_daily', effect='deny'), dict(permission='task.create_customer', effect='deny')])
    async with await client_for(connection, auth_mode='demo') as client:
        await business_login(client, 'XS001')
        response = await client.get('/api/v1/tasks/management/owners')
        assert response.status_code == 200, response.text
        assert response.json()['items'][0]['id'] == recipient.user_id
        await grant(connection, admin, recipient.user_id, [dict(permission='task.read', effect='deny')], version=1)
        for path in ('/api/v1/tasks/management', '/api/v1/tasks/management/owners'):
            assert (await client.get(path)).status_code == 403


async def test_confirmed_batch_metadata_current_owner_and_normal_http_contract(connection, monkeypatch):
    async def unavailable(*args, **kwargs):
        raise ValueError('No remote models in isolated test')
    monkeypatch.setattr('sales_backend.services.task_imports.load_runtime_configuration', unavailable)
    async with await client_for(connection) as client:
        await sign_in(client, 'ADMIN001')
        batch = await preview(client, source='XS001：\n核验周会派发任务')
        dispatched = await submit(client, batch, confirmation(batch))
        assert dispatched.status_code == 200, dispatched.text
        task = dispatched.json()['tasks'][0]
        response = await client.get('/api/v1/tasks/management', params={'batch_id': batch['id']})
        assert response.status_code == 200, response.text
        item = response.json()['items'][0]
        assert item['start_kind'] == 'dispatched' and item['schedule_start_at'] == item['dispatched_at']
        assert item['accepted_at'] is None and item['batch_name'] == '隔离周会'
        assert item['action_permissions']['task.coordinate'] is True
        for params in ({'page_size': 101}, {'scope': 'workspace'}, {'date_from': '2026-10-02', 'date_to': '2026-10-01'},
                       {'member_id': item['owner_id'], 'unassigned': 'true'}, {'status': 'unknown'}, {'undated': 'bad'}):
            assert (await client.get('/api/v1/tasks/management', params=params)).status_code == 422
        moved = await client.post(f"/api/v1/tasks/{task['id']}/events", json={
            'event_type': 'reassign', 'assignee_account_code': 'XS002', 'note': '核对新负责人', 'version_no': task['version_no']})
        assert moved.status_code == 200, moved.text
        rows = (await client.get('/api/v1/tasks/management', params={'batch_id': batch['id']})).json()
        assert rows['items'][0]['owner_name'] == 'XS002'
        assert rows['items'][0]['batch_id'] == batch['id']
        assert (await client.get('/api/v1/tasks', params={'inbox': 'true'})).json()['items'] == []
    # A recipient can read safe batch metadata without access to the raw creator draft.
    await actor(connection, 'XS002')
    assert await connection.fetchval('SELECT id FROM workflow.task_import_batch WHERE id=$1::uuid', batch['id']) is None
    assert (await TaskManagementRepository().page(connection))['items'][0]['batch_id'] == batch['id']


async def test_unassigned_overdue_terminal_and_created_interval(connection):
    admin = await actor(connection, 'ADMIN001')
    task = await make_task(connection, admin, 'XS001')
    past = datetime.now(UTC) - timedelta(days=3)
    await seed_execute(connection, "UPDATE workflow.task SET due_at=$2,created_at=$2::timestamptz-interval '1 day' WHERE id=$1::uuid", task['id'], past)
    await actor(connection, 'ADMIN001')
    repo = TaskManagementRepository()
    row = (await repo.page(connection, overdue=True))['items'][0]
    assert row['schedule_invalid'] is False and row['due_at'] == past
    await seed_execute(connection, 'DELETE FROM workflow.task_assignee WHERE task_id=$1::uuid', task['id'])
    unassigned = await repo.page(connection, unassigned=True)
    assert unassigned['summary']['total'] == 1 and unassigned['items'][0]['owner_id'] is None
    assert (await repo.owners(connection))['unassigned_count'] == 1
    await seed_execute(connection, "UPDATE workflow.task SET status='cancelled' WHERE id=$1::uuid", task['id'])
    assert (await repo.page(connection, overdue=True))['summary']['total'] == 0
    assert (await repo.page(connection))['summary']['cancelled_count'] == 1


async def test_multiple_current_owners_are_filterable_without_cross_tenant_leaks(connection):
    admin = await actor(connection, 'ADMIN001')
    task = await make_task(connection, admin, 'XS001')
    first = await actor(connection, 'XS001')
    second = await actor(connection, 'XS002')
    await seed_execute(connection, """INSERT INTO workflow.task_assignee
      (task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role)
      VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,'sales')""",
      task['id'], admin.workspace_id, second.user_id, second.team_ids[0])
    foreign_ws, foreign_user, foreign_task = uuid4(), uuid4(), uuid4()
    await seed_execute(connection, "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'合成外公司')",
                       foreign_ws, 'task-management-' + uuid4().hex)
    await seed_execute(connection, """INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name)
      VALUES($1,$2,$3,'OUTSIDE','外部不可见')""", foreign_user, foreign_ws, str(foreign_user))
    await seed_execute(connection, """INSERT INTO workflow.task(id,workspace_id,creator_user_ref_id,title,description,due_at,association_kind)
      VALUES($1,$2,$3,'外公司任务','外公司任务',clock_timestamp()+interval '1 day','daily')""", foreign_task, foreign_ws, foreign_user)
    await seed_execute(connection, """INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_role)
      VALUES($1,$2,$3,'sales')""", foreign_task, foreign_ws, foreign_user)
    await actor(connection, 'ADMIN001')
    repo = TaskManagementRepository()
    page = await repo.page(connection)
    assert page['summary']['total'] == 1 and page['items'][0]['owner_count'] == 2
    assert {o['id'] for o in page['items'][0]['owners']} == {first.user_id, second.user_id}
    for person in (first, second):
        assert (await repo.page(connection, member_id=person.user_id))['summary']['total'] == 1
    directory = await repo.owners(connection)
    assert {o['id'] for o in directory['items']} == {first.user_id, second.user_id}
    assert all(o['task_count'] == 1 for o in directory['items'])
    assert (await repo.page(connection, member_id=str(foreign_user)))['summary']['total'] == 0
    assert (await repo.owners(connection, query='OUTSIDE'))['items'] == []
    # The second historical co-owner also sees this task through normal RLS.
    await actor(connection, 'XS002')
    assert (await repo.page(connection, scope='mine'))['summary']['total'] == 1


async def test_http_management_runs_in_actual_readonly_repeatable_snapshot(connection, monkeypatch):
    """Use a real pooled Database here; rollback-fixture HTTP helpers use savepoints."""
    import os
    import asyncpg
    from sales_backend.db import Database, _initialize_connection
    from sales_backend.main import app
    from tests.integration.conftest import _dsn

    role = os.environ['SALES_TEST_ROLE']
    assert role.startswith('salegent_verify_role_')

    async def setup(conn):
        await conn.execute(f'SET ROLE "{role}"')

    observed = []
    for method in ('page', 'owners'):
        original = getattr(TaskManagementRepository, method)

        async def inspect(self, conn, _original=original, **kwargs):
            observed.append(await conn.fetchrow("""SELECT current_setting('transaction_read_only') AS readonly,
                current_setting('transaction_isolation') AS isolation,
                (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user) AS bypass"""))
            return await _original(self, conn, **kwargs)

        monkeypatch.setattr(TaskManagementRepository, method, inspect)

    pool = await asyncpg.create_pool(dsn=_dsn(), min_size=1, max_size=2, init=_initialize_connection, setup=setup)
    try:
        async with await client_for(connection) as client:
            monkeypatch.setattr(app.state, 'database', Database(app.state.settings, pool=pool))
            await sign_in(client, 'ADMIN001')
            for path in ('/api/v1/tasks/management', '/api/v1/tasks/management/owners'):
                response = await client.get(path)
                assert response.status_code == 200, response.text
        assert len(observed) == 2
        assert all(dict(row) == {'readonly': 'on', 'isolation': 'repeatable read', 'bypass': False} for row in observed)
    finally:
        await pool.close()


async def test_read_only_creator_can_filter_inactive_historical_owner_without_org_access(connection):
    admin = await actor(connection, 'ADMIN001')
    creator = await actor(connection, 'XS002')
    task = await make_task(connection, creator, 'XS001')
    recipient = await actor(connection, 'XS001')
    await grant(connection, admin, creator.user_id, [
        dict(permission='task.create_daily', effect='deny'),
        dict(permission='task.create_customer', effect='deny'),
        dict(permission='organization.read', effect='deny'),
    ])
    await seed_execute(connection, "UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", recipient.user_id)
    async with await client_for(connection, auth_mode='demo') as client:
        await business_login(client, 'XS002')
        directory = await client.get('/api/v1/tasks/management/owners', params={'q': 'XS001'})
        assert directory.status_code == 200, directory.text
        assert directory.json()['items'][0]['active'] is False
        page = await client.get('/api/v1/tasks/management', params={'member_id': recipient.user_id})
        assert page.status_code == 200, page.text
        assert page.json()['summary']['total'] == 1
        assert page.json()['items'][0]['id'] == task['id']
        assert page.json()['items'][0]['owner_active'] is False
