"""Task pickers display organization affiliation without creating business authority."""

from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.directory import DirectoryRepository
from sales_backend.repositories.task_targets import TaskTargetRepository
from sales_backend.repositories.team_directory import attach_member_teams
from tests.integration.feishu_fixtures import fixture_owner, assert_restricted
from tests.integration.test_operations_api import client_for
from tests.integration.test_sales_opportunity_permissions import account_fixture, native_login
from tests.integration.test_task_reassignment_options import make_task
from tests.integration.test_team_directory import add_team

pytestmark = pytest.mark.asyncio


async def set_affiliation(connection, admin, user, team_id):
    await set_request_context(connection, admin)
    await connection.execute(
        "UPDATE platform.user_ref SET attributes=attributes || "
        "jsonb_build_object('organization_team_id',$2::text) WHERE id=$1::uuid",
        user['id'], team_id,
    )


@pytest.mark.parametrize('company_role', ['administrator', 'operations'])
@pytest.mark.parametrize('directory', ['create', 'reassign'])
async def test_task_directories_show_company_only_organization_without_business_membership(
    connection, company_role, directory,
):
    recipient, _, admin = await account_fixture(connection, [], company_role=company_role)
    growth = await add_team(connection, '任务目录组织归属')
    await set_affiliation(connection, admin, recipient, growth['id'])
    creator, _, _ = await account_fixture(connection, ['sales'])
    async with await client_for(connection) as client:
        await native_login(client, creator)
        path = '/api/v1/tasks/recipients'
        if directory == 'reassign':
            task = await make_task(client, creator)
            path = f"/api/v1/tasks/{task['id']}/reassignment-options"
        response = await client.get(path, params={'q': recipient['account_code']})
        assert response.status_code == 200, response.text
        person, = response.json()['items']
        assert person['team_ids'] == [growth['id']]
        assert person['team'] == '任务目录组织归属'
        assert person['team_id'] is None
        assert person['role'] == company_role
        assert growth['id'] in {team['id'] for team in response.json()['teams']}

    # Display metadata must never flow into role/team resolution for task writes.
    await set_request_context(connection, admin)
    resolved, = await TaskTargetRepository().resolve(
        connection, admin, account=recipient['account_code'],
    )
    assert resolved['team_id'] is None and resolved['role_code'] == company_role
    assert await connection.fetchval(
        'SELECT count(*) FROM platform.team_membership WHERE user_ref_id=$1::uuid', recipient['id'],
    ) == 0
    assert recipient['id'] not in {
        person['id'] for person in await DirectoryRepository().task_assignees(connection, admin)
    }
    assert recipient['id'] not in {
        person['id'] for person in await DirectoryRepository().members(connection, admin)
    }


async def test_task_display_combines_membership_and_affiliation_without_changing_shared_directory(connection):
    recipient, memberships, admin = await account_fixture(connection, ['sales'], company_role='administrator')
    growth = await add_team(connection, '组织归属与任职不同')
    business_team = memberships['sales']
    await set_affiliation(connection, admin, recipient, growth['id'])
    repository = TaskTargetRepository()
    page = await repository.recipient_page(connection, admin, q=recipient['account_code'])
    person, = page['items']
    assert set(person['team_ids']) == {business_team, growth['id']}
    assert person['team_id'] == business_team
    assert '组织归属与任职不同' in person['team']
    shared, = await attach_member_teams(connection, admin, [person], page['teams'])
    assert shared['team_ids'] == [business_team]
    await set_affiliation(connection, admin, recipient, business_team)
    same, = (await repository.recipient_page(connection, admin, q=recipient['account_code']))['items']
    assert same['team_ids'] == [business_team]


@pytest.mark.parametrize('condition', ['inactive', 'expired', 'future', 'deleted', 'missing', 'malformed'])
async def test_ineffective_affiliations_never_become_task_directory_groups(connection, condition):
    recipient, _, admin = await account_fixture(connection, [], company_role='operations')
    growth = await add_team(connection, '无效组织归属')
    affiliation = growth['id']
    updates = {
        'inactive': "status='inactive'", 'expired': 'valid_to=clock_timestamp()',
        'future': "valid_from=clock_timestamp()+interval '1 day'", 'deleted': 'deleted_at=clock_timestamp()',
    }
    if condition in updates:
        await connection.execute(f"UPDATE platform.team SET {updates[condition]} WHERE id=$1::uuid", growth['id'])
    elif condition == 'missing':
        affiliation = str(uuid4())
    else:
        affiliation = 'not-a-team-uuid'
    await set_affiliation(connection, admin, recipient, affiliation)
    page = await TaskTargetRepository().recipient_page(connection, admin, q=recipient['account_code'])
    person, = page['items']
    assert person['team_ids'] == [] and person['team_id'] is None
    assert person['team'] == '未分组'


async def test_other_company_affiliation_cannot_leak_team_or_change_task_resolution(connection):
    recipient, _, admin = await account_fixture(connection, [], company_role='administrator')
    workspace_id, team_id = uuid4(), uuid4()
    # Seed only the foreign tenant under the disposable test database owner.
    async with fixture_owner(connection):
        await connection.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'外公司夹具')",
            workspace_id, 'task-org-' + uuid4().hex,
        )
        await connection.execute(
            "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,'外公司部门')",
            team_id, workspace_id, 'task-org-' + uuid4().hex,
        )
    await assert_restricted(connection)
    await set_affiliation(connection, admin, recipient, str(team_id))
    repository = TaskTargetRepository()
    page = await repository.recipient_page(connection, admin, q=recipient['account_code'])
    person, = page['items']
    assert person['team_ids'] == [] and person['team_id'] is None
    assert person['team'] == '未分组'
    assert str(team_id) not in {team['id'] for team in page['teams']}
    resolved, = await repository.resolve(connection, admin, account=recipient['account_code'])
    assert resolved['team_id'] is None
