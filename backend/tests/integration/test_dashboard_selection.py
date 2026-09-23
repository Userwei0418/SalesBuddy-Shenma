from uuid import uuid4

import pytest

from sales_backend.repositories.dashboard import DashboardRepository
from sales_backend.repositories.dashboard_scope import dashboard_members
from sales_backend.services.dashboard_rankings import dashboard_rankings
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


async def two_teams(connection):
    first = await opportunity(connection, 'XS001', 100)
    second = await opportunity(connection, 'XS002', 200)
    other = await actor(connection, 'XS002')
    manager = await actor(connection, 'ZJL001')
    north = uuid4()
    await connection.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'test-north','北区')",
                             north, manager.workspace_id)
    await connection.execute(
        'UPDATE platform.team_membership SET team_id=$1 WHERE user_ref_id=$2::uuid', north, other.user_id)
    await connection.execute(
        'UPDATE platform.role_binding SET team_id=$1 WHERE user_ref_id=$2::uuid', north, other.user_id)
    await connection.execute('UPDATE crm.opportunity SET owner_team_id=$1 WHERE id=$2::uuid', north, second['id'])
    await connection.execute('UPDATE crm.customer SET owner_team_id=$1 WHERE id=$2::uuid', north, second['customer_id'])
    return first, second, other.user_id


async def test_sales_peer_regions_are_complete_aggregates_not_detail_access(connection):
    first, second, _ = await two_teams(connection)
    sales = await actor(connection, 'XS001')
    data = await dashboard_rankings(connection, sales, year=2026, quarters=[3], personal=True)
    assert data['scope'] == 'peer'
    assert len(data['opportunity_acv']['rows']) == 2
    assert {r['code']: r['value'] for r in data['region']['rows']} == {'north_east': 200, 'south_hkmo': 100}
    assert data['own_region_codes'] == ['south_hkmo']
    assert first['id'] not in str(data) and second['id'] not in str(data)
    assert await connection.fetchval('SELECT count(*) FROM crm.opportunity WHERE id=$1::uuid', second['id']) == 0


async def test_manager_member_and_group_filters_do_not_change_full_rankings(connection):
    first, second, other_id = await two_teams(connection)
    manager = await actor(connection, 'ZJL001')
    repo = DashboardRepository()
    all_data = await repo.load(connection, manager, team_groups=['north_east', 'south_hkmo'])
    north = await repo.load(connection, manager, team_groups=['north_east'])
    south = await repo.load(connection, manager, team_groups=['south_hkmo'])
    selected = await repo.load(connection, manager, personal=True, member_id=other_id)
    own = await repo.load(connection, manager, personal=True)
    assert {r['id'] for r in all_data['opportunities']} == {first['id'], second['id']}
    assert [r['id'] for r in north['opportunities']] == [second['id']]
    assert [r['id'] for r in south['opportunities']] == [first['id']]
    assert selected['scope'] == 'member' and selected['selection']['member_id'] == other_id
    assert [r['id'] for r in selected['opportunities']] == [second['id']]
    assert own['scope'] == 'self' and own['opportunities'] == []
    ranks = await dashboard_rankings(connection, manager, year=2026, quarters=[3], personal=True)
    assert ranks['scope'] == 'peer' and len(ranks['opportunity_acv']['rows']) == 1
    assert ranks['selection']['cohort_role'] == 'manager'
    assert sum(r['value'] for r in ranks['region']['rows']) == 300
    assert sum(r['amount'] for r in north['opportunities']) + sum(r['amount'] for r in south['opportunities']) == 300


async def test_supervisor_member_directory_and_read_enforce_effective_team_membership(connection):
    first, _, other_id = await two_teams(connection)
    supervisor = await actor(connection, 'ZJ001')
    members = await dashboard_members(connection, supervisor)
    assert supervisor.user_id in {row['id'] for row in members}
    assert other_id not in {row['id'] for row in members}
    with pytest.raises(PermissionError, match='不在可查看范围'):
        await DashboardRepository().load(connection, supervisor, personal=True, member_id=other_id)
    sales_id = next(row['id'] for row in members if row['account_code'] == 'XS001')
    result = await DashboardRepository().load(connection, supervisor, personal=True, member_id=sales_id)
    assert [row['id'] for row in result['opportunities']] == [first['id']]
    sales = await actor(connection, 'XS001')
    with pytest.raises(PermissionError, match='仅可查看本人'):
        await DashboardRepository().load(connection, sales, personal=True, member_id=other_id)
    manager = await actor(connection, 'ZJL001')
    with pytest.raises(PermissionError):
        await DashboardRepository().load(connection, manager, personal=True, member_id=str(uuid4()))


async def test_dashboard_filter_http_validation(connection):
    async with await client_for(connection, auth_mode='demo') as client:
        await business_login(client, 'ZJL001')
        options = await client.get('/api/v1/dashboard/options')
        assert options.status_code == 200
        groups = options.json()['team_groups']
        assert [row['name'] for row in groups] == ['南区']
        assert all(row['code'] == 'team:' + row['team_id'] for row in groups)
        assert (await client.get('/api/v1/dashboard', params={'team_groups':'invalid'})).status_code == 422
        assert (await client.get('/api/v1/dashboard', params={'member_id':str(uuid4())})).status_code == 422
        denied = await client.get('/api/v1/dashboard', params={'personal': True, 'member_id': str(uuid4())})
        assert denied.status_code == 403
        await business_login(client, 'XS001')
        assert (await client.get('/api/v1/dashboard', params={'team_groups':'north_east'})).status_code == 403
