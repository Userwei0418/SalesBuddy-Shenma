"""Personnel pickers share live organization facts without expanding action scopes."""

from uuid import uuid4

import pytest

from sales_backend.repositories.team_directory import default_team_id, selectable_teams
from tests.integration.feishu_fixtures import fixture_owner
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login
from tests.integration.test_team_directory import add_team

pytestmark = pytest.mark.asyncio

DIRECTORIES = (
    ('/api/v1/directory/members', 'items', 'teams'),
    ('/api/v1/dashboard/options', 'members', 'teams'),
    ('/api/v1/profile/scope-options', 'members', 'teams'),
    ('/api/v1/opportunities', 'owner_options', 'team_options'),
    ('/api/v1/tasks/recipients', 'items', 'teams'),
)


async def test_picker_metadata_retains_empty_teams_all_memberships_and_real_primary(connection):
    empty = await add_team(connection, 'AAA空团队')
    extra = await add_team(connection, '兼任团队')
    inactive = await add_team(connection, '停用团队')
    sales = await actor(connection, 'XS001')
    manager = await actor(connection, 'ZJL001')
    admin = await actor(connection, 'ADMIN001')
    for team_id in (extra['id'], inactive['id']):
        await connection.execute('''INSERT INTO platform.team_membership
            (workspace_id,user_ref_id,team_id,membership_role,is_primary)
            VALUES($1::uuid,$2::uuid,$3::uuid,'sales',false)''',
            admin.workspace_id, sales.user_id, team_id)
    await connection.execute("UPDATE platform.team SET status='inactive' WHERE id=$1::uuid", inactive['id'])
    await connection.execute('''UPDATE platform.team_membership SET is_primary=true
        WHERE user_ref_id=$1::uuid AND team_id=$2::uuid''', manager.user_id, manager.team_ids[0])
    async with await client_for(connection, auth_mode='demo') as client:
        await business_login(client, 'ZJL001')
        for path, member_key, team_key in DIRECTORIES:
            response = await client.get(path)
            assert response.status_code == 200, (path, response.text)
            body = response.json()
            team_ids = {row['id'] for row in body[team_key]}
            assert empty['id'] in team_ids and inactive['id'] not in team_ids
            assert body['defaults']['team_id'] == manager.team_ids[0], path
            person = next(row for row in body[member_key] if row['id'] == sales.user_id)
            assert person['account_code'] == 'XS001'
            assert set(person['team_ids']) == {*sales.team_ids, extra['id']}, (path, person)
            # No business records are needed to offer a valid person or empty team.
            if path.endswith('/opportunities'):
                assert body['items'] == []


async def test_default_does_not_guess_first_team_or_expose_unselectable_primary(connection):
    empty = await add_team(connection, 'AAA第一个团队')
    manager = await actor(connection, 'ZJL001')
    await actor(connection, 'ADMIN001')
    await connection.execute('UPDATE platform.team_membership SET is_primary=false WHERE user_ref_id=$1::uuid',
                             manager.user_id)
    async with await client_for(connection, auth_mode='demo') as client:
        await business_login(client, 'ZJL001')
        for path, _, _ in DIRECTORIES:
            response = await client.get(path)
            assert response.status_code == 200, (path, response.text)
            assert response.json()['defaults']['team_id'] is None, path
    sales = await actor(connection, 'XS001')
    assert await default_team_id(connection, sales, [empty]) is None
    assert await default_team_id(connection, sales, []) is None
    assert await selectable_teams(connection, sales, 'dashboard') == []


async def test_same_name_owner_ids_filter_exactly_and_legacy_names_still_work(connection):
    first = await opportunity(connection, 'XS001', 100)
    second = await opportunity(connection, 'XS002', 200)
    first_owner = await actor(connection, 'XS001')
    second_owner = await actor(connection, 'XS002')
    await actor(connection, 'ADMIN001')
    await connection.execute('UPDATE platform.user_ref SET display_name=$2 WHERE id=ANY($1::uuid[])',
                             [first_owner.user_id, second_owner.user_id], '同名负责人')
    async with await client_for(connection, auth_mode='demo') as client:
        await business_login(client, 'ZJL001')
        response = await client.get('/api/v1/opportunities', params={'owner': '同名负责人'})
        assert response.status_code == 200, response.text
        assert {row['id'] for row in response.json()['items']} == {first['id'], second['id']}
        options = [row for row in response.json()['owner_options'] if row['name'] == '同名负责人']
        assert {row['id'] for row in options} == {first_owner.user_id, second_owner.user_id}
        assert {row['account_code'] for row in options} == {'XS001', 'XS002'}
        for owner, expected in ((first_owner, first), (second_owner, second)):
            response = await client.get('/api/v1/opportunities', params={
                'owner_id': owner.user_id, 'owner': '过时姓名不能覆盖稳定ID'})
            assert response.status_code == 200, response.text
            assert [row['id'] for row in response.json()['items']] == [expected['id']]
        assert (await client.get('/api/v1/opportunities', params={'owner_id': 'invalid'})).status_code == 422
        assert (await client.get('/api/v1/opportunities', params={'owner_id': str(uuid4())})).json()['items'] == []
        await business_login(client, 'XS001')
        denied = await client.get('/api/v1/opportunities', params={'owner_id': second_owner.user_id})
        assert denied.status_code == 200 and denied.json()['items'] == []
        assert second_owner.user_id not in {row['id'] for row in denied.json()['owner_options']}


async def test_picker_metadata_never_returns_other_company_people_or_teams(connection):
    workspace, team, person = uuid4(), uuid4(), uuid4()
    async with fixture_owner(connection):
        await connection.execute('''INSERT INTO platform.workspace(id,external_workspace_id,name)
            VALUES($1,$2,'其他公司')''', workspace, 'picker-foreign-' + uuid4().hex)
        await connection.execute('''INSERT INTO platform.team(id,workspace_id,code,name)
            VALUES($1,$2,'foreign','其他公司团队')''', team, workspace)
        await connection.execute('''INSERT INTO platform.user_ref
            (id,workspace_id,external_user_id,display_name,account_code)
            VALUES($1,$2,'foreign','其他公司人员','FOREIGN')''', person, workspace)
        await connection.execute('''INSERT INTO platform.team_membership
            (workspace_id,user_ref_id,team_id,membership_role,is_primary)
            VALUES($1,$2,$3,'sales',true)''', workspace, person, team)
        await connection.execute('''INSERT INTO platform.role_binding
            (workspace_id,user_ref_id,team_id,role_code,data_scope_code)
            VALUES($1,$2,$3,'sales','self')''', workspace, person, team)
    async with await client_for(connection, auth_mode='demo') as client:
        await business_login(client, 'ZJL001')
        for path, _, _ in DIRECTORIES:
            response = await client.get(path)
            assert response.status_code == 200, (path, response.text)
            assert str(person) not in response.text and str(team) not in response.text, path
