"""Backend-owned selectors retain empty teams and enforce effective organization scope."""
from uuid import uuid4

import pytest

from sales_backend.repositories.dashboard import DashboardRepository
from sales_backend.repositories.dashboard_scope import dashboard_team_groups
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.repositories.opportunities import OpportunityRepository
from sales_backend.repositories.profile_scope import scope_options, resolve_scope
from sales_backend.repositories.team_directory import selectable_teams, require_team, attach_member_teams
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def add_team(connection, name, parent=None):
    admin = await actor(connection, 'ADMIN001')
    return await OperationsAccountRepository().save_department(connection, admin, None, {
        'code': 'DIR-' + uuid4().hex[:10], 'name': name, 'status': 'active', 'parent_team_id': parent,
    })


async def test_same_directory_across_pages_empty_team_is_selectable_and_has_no_facts(connection):
    empty = await add_team(connection, '新启用业务团队')
    manager = await actor(connection, 'ZJL001')
    teams = await selectable_teams(connection, manager)
    ids = {t['id'] for t in teams}
    assert empty['id'] in ids
    assert ids == {t['id'] for t in (await scope_options(connection, manager))['teams']}
    assert ids == {t['team_id'] for t in await dashboard_team_groups(connection, manager)}
    page = await OpportunityRepository().page(connection, manager, limit=20, team_id=empty['id'])
    assert ids == {t['id'] for t in page['team_options']}
    assert page['items'] == [] and page['summary']['total'] == 0
    scope = await resolve_scope(connection, manager, scope='team', team_id=empty['id'])
    assert scope['team_ids'] == [empty['id']] and scope['member_ids'] == []
    facts = await DashboardRepository().load(connection, manager, team_groups=['team:' + empty['id']])
    assert facts['opportunities'] == [] and facts['customers'] == []


async def test_parent_exclusion_is_structural_and_renaming_preserves_id(connection):
    parent = await add_team(connection, '任意汇总名称')
    leaf = await add_team(connection, '生态渠道', parent['id'])
    manager = await actor(connection, 'ZJL001')
    ids = {t['id'] for t in await selectable_teams(connection, manager)}
    assert parent['id'] not in ids and leaf['id'] in ids
    await actor(connection, 'ADMIN001')
    await connection.execute('UPDATE platform.team SET name=$2 WHERE id=$1::uuid', leaf['id'], '改名业务组')
    manager = await actor(connection, 'ZJL001')
    assert (await require_team(connection, manager, leaf['id']))['name'] == '改名业务组'
    with pytest.raises(PermissionError):
        await require_team(connection, manager, parent['id'])


@pytest.mark.parametrize('condition', ['inactive', 'expired', 'future', 'deleted'])
async def test_invalid_team_cannot_be_selected(connection, condition):
    team = await add_team(connection, '不可选组')
    updates = {'inactive': "status='inactive'", 'expired': 'valid_to=clock_timestamp()',
               'future': "valid_from=clock_timestamp()+interval '1 day'", 'deleted': 'deleted_at=clock_timestamp()'}
    await connection.execute(f"UPDATE platform.team SET {updates[condition]} WHERE id=$1::uuid", team['id'])
    manager = await actor(connection, 'ZJL001')
    assert team['id'] not in {t['id'] for t in await selectable_teams(connection, manager)}
    with pytest.raises(PermissionError):
        await OpportunityRepository().page(connection, manager, limit=20, team_id=team['id'])


@pytest.mark.parametrize('account', ['XS001', 'ZJ001'])
async def test_sales_and_supervisor_never_receive_or_select_unrelated_empty_team(connection, account):
    team = await add_team(connection, '无权团队')
    person = await actor(connection, account)
    ids = {t['id'] for t in await selectable_teams(connection, person)}
    assert ids == set(person.team_ids) and team['id'] not in ids
    with pytest.raises(PermissionError):
        await require_team(connection, person, team['id'])
    dashboard_ids = {row['id'] for row in await selectable_teams(connection, person, 'dashboard')}
    # A supervisor's configured team scope now provides an explicit team selector.
    assert dashboard_ids == (set(person.team_ids) if account == 'ZJ001' else set())


async def test_member_directory_retains_all_effective_team_ids(connection):
    extra = await add_team(connection, '兼任业务组')
    sales = await actor(connection, 'XS001')
    admin = await actor(connection, 'ADMIN001')
    await connection.execute('''INSERT INTO platform.team_membership
        (workspace_id,user_ref_id,team_id,membership_role,is_primary)
        VALUES($1::uuid,$2::uuid,$3::uuid,'sales',false)''', admin.workspace_id, sales.user_id, extra['id'])
    manager = await actor(connection, 'ZJL001')
    members = await attach_member_teams(connection, manager, [{'id':sales.user_id, 'team_id':sales.team_ids[0]}],
                                       await selectable_teams(connection, manager))
    assert len(members) == 1
    assert set(members[0]['team_ids']) == {*sales.team_ids, extra['id']}


async def test_public_directory_contract_and_business_filter_permissions(connection):
    from tests.integration.test_operations_api import client_for
    from tests.integration.test_profile_scores import business_login
    empty = await add_team(connection, '接口空团队')
    async with await client_for(connection, auth_mode='demo') as client:
        await business_login(client, 'ZJL001')
        response = await client.get('/api/v1/directory/teams')
        assert response.status_code == 200 and response.json()['data_source'] == 'database'
        teams = response.json()['teams']
        assert empty['id'] in {t['id'] for t in teams}
        members = await client.get('/api/v1/directory/members')
        assert members.status_code == 200 and members.json()['teams'] == teams
        assert all('team_ids' in m for m in members.json()['items'])
        result = await client.get('/api/v1/opportunities', params={'team_id':empty['id']})
        assert result.status_code == 200 and result.json()['summary']['total'] == 0
        assert result.json()['team_options'] == teams
        assert (await client.get('/api/v1/opportunities', params={
            'team_id':empty['id'], 'team':'不同团队名称'})).status_code == 422
        await business_login(client, 'XS001')
        assert (await client.get('/api/v1/directory/teams', params={'purpose':'assignment'})).status_code == 403
        assert (await client.get('/api/v1/opportunities', params={'team_id':empty['id']})).status_code == 403


async def test_parent_stays_aggregate_when_only_child_is_inactive(connection):
    parent = await add_team(connection, '部门汇总')
    child = await add_team(connection, '已停用团队', parent['id'])
    await connection.execute("UPDATE platform.team SET status='inactive' WHERE id=$1::uuid", child['id'])
    manager = await actor(connection, 'ZJL001')
    ids = {t['id'] for t in await selectable_teams(connection, manager)}
    assert parent['id'] not in ids and child['id'] not in ids


async def test_manager_reads_empty_and_fde_team_targets_without_granting_writes(connection):
    from tests.integration.test_operations_api import client_for
    from tests.integration.test_profile_scores import business_login
    from tests.integration.test_target_batches import body
    from sales_backend.services.targets import save_target_batch
    from uuid import UUID
    empty = await add_team(connection, '空业务组')
    from tests.integration.test_fde_identity_tasks import fde_fixture
    _, _, _, fde_team = await fde_fixture(connection)
    teams = [empty, {'id':fde_team}]
    operator = await actor(connection, 'OPS001')
    await save_target_batch(connection, operator, body(
        [{'kind':'collection', 'amount':'1200'}], scope='team', team_id=UUID(teams[1]['id'])))
    async with await client_for(connection, auth_mode='demo') as client:
        await business_login(client, 'ZJL001')
        for team in teams:
            result = await client.get('/api/v1/targets', params={'scope':'team','team_id':team['id'],'period_type':'quarter'})
            assert result.status_code == 200, result.text
            assert result.json()['editable'] is False
            assert len(result.json()['items']) == (1 if team == teams[1] else 0)
            data = body([{'kind':'collection','amount':'500'}], scope='team',team_id=UUID(team['id'])).model_dump(mode='json')
            assert (await client.post('/api/v1/targets/batch',json=data)).status_code == 403
        for account in ('XS001','ZJ001'):
            await business_login(client, account)
            assert (await client.get('/api/v1/targets', params={'scope':'team','team_id':teams[0]['id']})).status_code == 403


@pytest.mark.parametrize('condition', ['inactive', 'expired', 'future', 'deleted'])
async def test_target_scope_rejects_ineffective_team(connection, condition):
    team=await add_team(connection, '过期目标团队')
    updates={'inactive':"status='inactive'",'expired':'valid_to=clock_timestamp()',
             'future':"valid_from=clock_timestamp()+interval '1 day'",'deleted':'deleted_at=clock_timestamp()'}
    await connection.execute(f"UPDATE platform.team SET {updates[condition]} WHERE id=$1::uuid",team['id'])
    await actor(connection,'ZJL001')
    assert not await connection.fetchval("SELECT security.target_scope_read('team',NULL,$1::uuid,'sales')",team['id'])


async def test_business_options_require_identity_and_match_backend_contract(connection):
    from tests.integration.test_operations_api import client_for
    from tests.integration.test_profile_scores import business_login
    from sales_backend.domain.business_options import business_options
    async with await client_for(connection,auth_mode='demo') as client:
        assert (await client.get('/api/v1/metadata/business-options')).status_code == 401
        await business_login(client,'ZJL001')
        result=await client.get('/api/v1/metadata/business-options')
        assert result.status_code == 200
        assert result.json() == business_options()
