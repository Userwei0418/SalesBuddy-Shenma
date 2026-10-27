"""Explicit FDE team selection keeps quarter money and immutable visit ownership aligned."""

from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from sales_backend.repositories.customer_assets import CustomerAssetRepository, today
from sales_backend.repositories.collaboration import fde_scope_options
from sales_backend.repositories.fde_dashboard import fde_activity, fde_dashboard
from sales_backend.services.profile_performance import quarter_period
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_fde_owned_recording import login_fde, own_visit
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_team_directory import add_team

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize('has_primary', [True, False])
async def test_fde_picker_primary_default_and_memberships_respect_selectable_team_scope(connection, has_primary):
    _, _, people, primary_team = await fde_fixture(connection)
    first_team = await add_team(connection, 'AAA可选兼任FDE组')
    hidden_team = await add_team(connection, '不可选FDE归属')
    admin = await actor(connection, 'ADMIN001')
    lead_id = people['lead']['id']
    await connection.execute('''UPDATE platform.team_membership SET is_primary=$2
        WHERE user_ref_id=$1::uuid''', lead_id, has_primary)
    await connection.execute('''INSERT INTO platform.team_membership
        (workspace_id,user_ref_id,team_id,membership_role,is_primary)
        VALUES($1::uuid,$2::uuid,$3::uuid,'fde_lead',false)''',
        admin.workspace_id, lead_id, first_team['id'])
    await connection.execute('''INSERT INTO platform.role_binding
        (workspace_id,user_ref_id,team_id,role_code,data_scope_code)
        VALUES($1::uuid,$2::uuid,$3::uuid,'fde_lead','team')''',
        admin.workspace_id, lead_id, first_team['id'])
    # A visible person may have another valid FDE membership outside this
    # lead's team scope, and a non-FDE membership in an otherwise visible team.
    await connection.execute('''INSERT INTO platform.team_membership
        (workspace_id,user_ref_id,team_id,membership_role,is_primary)
        VALUES($1::uuid,$2::uuid,$3::uuid,'fde',false),
              ($1::uuid,$2::uuid,$4::uuid,'sales',false)''',
        admin.workspace_id, people['first']['id'], hidden_team['id'], first_team['id'])
    lead = await actor(connection, people['lead']['code'])
    options = await fde_scope_options(connection, lead)
    assert options['teams'][0]['id'] == first_team['id']
    assert {team['id'] for team in options['teams']} == {primary_team, first_team['id']}
    assert options['defaults']['team_id'] == (primary_team if has_primary else None)
    first = next(person for person in options['members'] if person['id'] == people['first']['id'])
    assert first['team_ids'] == [primary_team]
    assert first['account_code'] == people['first']['code']
    lead = next(person for person in options['members'] if person['id'] == lead_id)
    assert set(lead['team_ids']) == {primary_team, first_team['id']}
    async with await client_for(connection) as client:
        await login_fde(connection, client, people['lead'])
        response = await client.get('/api/v1/fde/scope-options')
        assert response.status_code == 200, response.text
        assert response.json()['defaults'] == options['defaults']
        assert response.json()['members'] == options['members']


async def test_fde_explicit_team_ledger_union_and_history_survive_member_transfer(connection):
    _, project, people, team = await fde_fixture(connection)
    _, other_project, others, other_team = await fde_fixture(connection)
    first = await actor(connection, people['first']['code'])
    await own_visit(connection, first, project, on=today().isoformat())
    other_first = await actor(connection, others['first']['code'])
    await own_visit(connection, other_first, other_project, on=today().isoformat())
    admin = await actor(connection, 'ADMIN001')
    # One real lead can manage two real teams; selecting one must not merge both.
    await connection.execute(
        "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) "
        "VALUES($1::uuid,$2::uuid,$3::uuid,'fde_lead')", admin.workspace_id, people['lead']['id'], other_team)
    await connection.execute(
        "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) "
        "VALUES($1::uuid,$2::uuid,'fde_lead','team',$3::uuid)", admin.workspace_id, people['lead']['id'], other_team)
    current = quarter_period(today().year, (today().month-1)//3+1)
    manager = await actor(connection, 'ZJL001')
    for chosen, amount, day in [(project, 100, current['start']),
                                (project, 900, current['start']-timedelta(days=1)),
                                (other_project, 300, current['start'])]:
        await CustomerAssetRepository().create(connection, manager, {
            'customer_id': UUID(chosen['customer_id']), 'opportunity_id': UUID(chosen['id']),
            'kind': 'recognized', 'amount': Decimal(amount), 'occurred_on': day, 'note': '隔离季度验证',
            'source_ref': uuid4().hex, 'request_id': uuid4(),
        })
    lead = await actor(connection, people['lead']['code'])
    first_team = await fde_dashboard(connection, lead, scope='team', team_id=team,
                                     year=current['year'], quarters=[current['quarter']])
    second_team = await fde_dashboard(connection, lead, scope='team', team_id=other_team,
                                      year=current['year'], quarters=[current['quarter']])
    assert first_team['summary']['recognized_amount'] == 100  # two participants, one ledger entry
    assert second_team['summary']['recognized_amount'] == 300
    assert first_team['summary']['period_visits'] == second_team['summary']['period_visits'] == 1
    async with await client_for(connection) as client:
        await login_fde(connection, client, people['lead'])
        options = (await client.get('/api/v1/fde/scope-options')).json()
        assert {t['id'] for t in options['teams']} == {team, other_team}
        portrait = await client.get('/api/v1/fde/profile', params={'scope': 'team', 'team_id': team})
        assert portrait.status_code == 200, portrait.text
        assert portrait.json()['sample_count'] == 1 and not portrait.json()['can_review']
        await login_fde(connection, client, people['first'])
        assert (await client.get('/api/v1/fde/dashboard', params={'scope': 'team', 'team_id': team})).status_code == 403
    await actor(connection, 'ADMIN001')
    for query in (
            'UPDATE platform.team_membership SET team_id=$1::uuid WHERE user_ref_id=$2::uuid',
            'UPDATE platform.role_binding SET team_id=$1::uuid WHERE user_ref_id=$2::uuid'):
        await connection.execute(query,
                                 other_team, people['first']['id'])
    lead = await actor(connection, people['lead']['code'])
    old_team = await fde_activity(connection, lead, scope='team', team_id=team, all_history=True)
    new_team = await fde_activity(connection, lead, scope='team', team_id=other_team, all_history=True)
    assert old_team['total'] == 1
    assert new_team['total'] == 1
    assert old_team['items'][0]['recorder_user_id'] == first.user_id
