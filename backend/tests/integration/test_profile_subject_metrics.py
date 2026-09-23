"""Real PostgreSQL checks for subject, quarter and ratio boundaries."""

from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from sales_backend.repositories.customer_assets import CustomerAssetRepository, today
from sales_backend.repositories.profile_customers import subject_customers
from sales_backend.repositories.profile_scope import resolve_scope, scope_options
from sales_backend.repositories.targets import TargetRepository
from sales_backend.services.profile_performance import performance, quarter_period
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_dashboard_selection import two_teams
from tests.integration.test_fde_battle_map_reviews import answer, archived_visit, handler_for
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


async def assess(connection, person, customer_id, potential):
    handler = handler_for(connection)
    facts = await handler._load_facts(customer_id, person)
    await handler._persist(customer_id, person, facts, answer(handler, facts, potential), {})


async def test_profile_directory_bounds_stable_selection_and_manager_nonapplicable_portrait(connection):
    _, _, other_id = await two_teams(connection)
    supervisor = await actor(connection, 'ZJ001')
    options = await scope_options(connection, supervisor)
    assert other_id not in {m['id'] for m in options['members']}
    assert all(m['role'] in {'sales', 'supervisor', 'manager'} for m in options['members'])
    assert all(m['account_code'] not in {'OPS001', 'ADMIN001'} for m in options['members'])
    with pytest.raises(PermissionError):
        await resolve_scope(connection, supervisor, scope='person', member_id=other_id)
    with pytest.raises(PermissionError):
        await resolve_scope(connection, supervisor, scope='person')
    with pytest.raises(PermissionError):
        await resolve_scope(connection, supervisor, scope='team', team_id=str(uuid4()))
    own = await resolve_scope(connection, supervisor, scope='self')
    assert own['editable'] and own['member_ids'] == [supervisor.user_id]
    async with await client_for(connection, auth_mode='demo') as client:
        await business_login(client, 'ZJL001')
        directory = (await client.get('/api/v1/profile/scope-options')).json()
        assert other_id in {m['id'] for m in directory['members']}
        selected = await client.get('/api/v1/profile/performance', params={'scope': 'person', 'member_id': other_id})
        assert selected.status_code == 200, selected.text
        assert selected.json()['selected_subject']['member_id'] == other_id
        assert not selected.json()['editable']
        result = await client.get('/api/v1/profile/sales-growth/scoped', params={'scope': 'self'})
        assert result.status_code == 200, result.text
        assert result.json()['applicable'] is False and result.json()['latest'] is None
        assert (await client.get('/api/v1/profile/performance', params={'scope': 'person'})).status_code == 403
        await business_login(client, 'XS001')
        assert (await client.get('/api/v1/profile/performance', params={
            'scope': 'person', 'member_id': other_id})).status_code == 403
        old = await client.post('/api/v1/profile/sales-targets', headers={'Idempotency-Key': str(uuid4())},
                                json={'scope': 'self', 'year': today().year, 'kind': 'collection', 'amount': 100})
        assert old.status_code == 422


async def test_profile_quarter_uses_actual_occurrence_and_does_not_change_efficiency_window(connection):
    first = await opportunity(connection, 'XS001', 500000)
    person = await actor(connection, 'XS001')
    current = quarter_period(today().year, (today().month-1)//3+1)
    await archived_visit(connection, person, first['customer_id'])
    await actor(connection, 'OPS001')
    target = await TargetRepository().save_batch(connection,
        scope={'scope_type': 'person', 'user_id': person.user_id, 'team_id': None}, period=current,
        items=[{'kind': 'collection', 'amount': '1000'}, {'kind': 'recognized', 'amount': '2000'}],
        reason='隔离季度口径测试')
    assert target
    manager = await actor(connection, 'ZJL001')
    for occurred, amount in [(current['start'], 100), (current['start']-timedelta(days=1), 200)]:
        await CustomerAssetRepository().create(connection, manager, dict(
            customer_id=UUID(first['customer_id']), opportunity_id=UUID(first['id']),
            kind='recognized', amount=Decimal(amount), occurred_on=occurred,
            source_ref=uuid4().hex, note='隔离季度实绩', request_id=uuid4()))
    person = await actor(connection, 'XS001')
    result = await performance(connection, person)
    assert result['actuals']['recognized'] == 100
    assert result['targets']['recognized'] == 2000
    assert result['scores']['maturity']['inputs'][1]['raw_value'] == 5
    other_year = await performance(connection, person, year=today().year-1, quarter=1)
    assert other_year['targets'] == {} and not other_year['editable']
    assert other_year['efficiency'] == result['efficiency']
    assert other_year['windows'] == result['windows']


async def test_profile_ratios_pending_customer_denominator_and_current_opportunity_grades(connection):
    person = await actor(connection, 'XS001')
    baseline = (await performance(connection, person))['efficiency']['customers']
    first = await opportunity(connection, 'XS001', 500000)
    await opportunity(connection, 'XS001', 100000)
    await opportunity(connection, 'XS001', 1000000, status='won')
    await opportunity(connection, 'XS001', 9000000, status='lost')
    person = await actor(connection, 'XS001')
    await assess(connection, person, first['customer_id'], 90)
    result = await performance(connection, person)
    customer_ratio = result['efficiency']['customers']
    assert customer_ratio['denominator'] == baseline['denominator']+4
    assert customer_ratio['numerator'] == baseline['numerator']+1
    assert customer_ratio['evaluated_count'] == baseline['evaluated_count']+1
    assert customer_ratio['pending_count'] == baseline['pending_count']+3
    assert customer_ratio['rate'] == Decimal(customer_ratio['numerator']*100)/customer_ratio['denominator']
    assert customer_ratio['status'] == 'partial'
    assert result['efficiency']['opportunities']['numerator'] == 2
    assert result['efficiency']['opportunities']['denominator'] == 3
    assert result['supplementals']['opportunities'] == Decimal(200)/3
    assert result['scores']['efficiency']['provenance']['efficiency_formula'].endswith('_v2')
    from sales_backend.repositories.customer_assets import today
    from tests.integration.test_customer_map_projection import formal_visit
    await formal_visit(connection, person, first['customer_id'], today().isoformat(), first['id'])
    async with await client_for(connection, auth_mode='demo') as client:
        await business_login(client, 'ZJ001')
        params = {'scope': 'person', 'member_id': person.user_id}
        viewed = (await client.get('/api/v1/profile/performance', params=params)).json()
        mapped = await client.get('/api/v1/customer-assets/map', params=params)
        assert mapped.status_code == 200, mapped.text
        points = mapped.json()['items']
        # Claiming and assessment alone no longer activate the map. Profile
        # denominators retain the full portfolio, independently of the map.
        assert len(points) == 1 < viewed['efficiency']['customers']['denominator']
        assert points[0]['id'] == first['customer_id']
        map_numerator = sum(p['quadrant_code'] in {'main_attack', 'customer_asset'} for p in points)
        assert map_numerator == customer_ratio['numerator']
        assert Decimal(str(viewed['efficiency']['customers']['rate'])) == customer_ratio['rate']


async def test_team_chooses_one_latest_assessment_but_person_keeps_own_evidence(connection):
    first = await opportunity(connection, 'XS001', 100000)
    owner = await actor(connection, 'XS001')
    await assess(connection, owner, first['customer_id'], 90)
    supervisor = await actor(connection, 'ZJ001')
    await assess(connection, supervisor, first['customer_id'], 30)
    team = await resolve_scope(connection, supervisor, scope='team', team_id=supervisor.team_ids[0])
    rows = await subject_customers(connection, member_ids=team['member_ids'], scope='team', team_ids=team['team_ids'])
    current_rows = [row for row in rows if row['id'] == first['customer_id']]
    assert len(current_rows) == 1
    assert current_rows[0]['subject_user_ref_id'] == supervisor.user_id
    assert current_rows[0]['quadrant_code'] == 'order_driven'
    personal = await performance(connection, supervisor, scope='person', member_id=owner.user_id)
    aggregate = await performance(connection, supervisor, scope='team', team_id=supervisor.team_ids[0])
    assert personal['efficiency']['customers']['numerator'] == 1
    assert aggregate['efficiency']['customers']['numerator'] == 0
    assert aggregate['efficiency']['customers']['denominator'] == len(rows)


async def test_structure_window_uses_created_dates_without_changing_quarter_actuals_or_followups(connection):
    old = await opportunity(connection, 'XS001', 500000)
    current = await opportunity(connection, 'XS001', 100000)
    owner = await actor(connection, 'XS001')
    await assess(connection, owner, old['customer_id'], 90)
    await assess(connection, owner, current['customer_id'], 30)
    await archived_visit(connection, owner, old['customer_id'])
    await actor(connection, 'ADMIN001')
    previous_year = today().replace(year=today().year-1, month=12, day=1)
    await connection.execute(
        "UPDATE crm.customer SET created_at=$1::date::timestamp AT TIME ZONE 'Asia/Shanghai' WHERE id=$2::uuid",
        previous_year, old['customer_id'])
    await connection.execute(
        "UPDATE crm.opportunity SET created_at=$1::date::timestamp AT TIME ZONE 'Asia/Shanghai' WHERE id=$2::uuid",
        previous_year, old['id'])
    owner = await actor(connection, 'XS001')
    all_rows = await performance(connection, owner, structure_period='current')
    this_year = await performance(connection, owner, structure_period='year')
    assert all_rows['efficiency']['customers']['denominator'] == this_year['efficiency']['customers']['denominator']+1
    assert all_rows['efficiency']['customers']['numerator'] == this_year['efficiency']['customers']['numerator']+1
    assert all_rows['efficiency']['opportunities']['numerator'] == 1
    assert this_year['efficiency']['opportunities']['numerator'] == 0
    assert all_rows['efficiency']['followup'] == this_year['efficiency']['followup']
    assert all_rows['efficiency']['followup']['count'] == 1
    assert all_rows['actuals'] == this_year['actuals']
