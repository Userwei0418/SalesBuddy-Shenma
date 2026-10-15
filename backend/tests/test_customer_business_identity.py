"""Identity contract, legacy compatibility and safe side-effect boundaries (offline)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sales_backend.contracts.customer_creation import CustomerCreationRequestBody
from sales_backend.contracts.customer_profile import CustomerProfileUpdate
from sales_backend.contracts.detail_reads import CustomerProfile
from sales_backend.contracts.models import CustomerCreate, CustomerUpdate
from sales_backend.domain.customer_identity import identity_update
from sales_backend.domain.customer_profile import customer_profile
from sales_backend.repositories.customer_identity import related_partner


def payload(**changes):
    return dict(name="Synthetic enterprise", industry="企业软件", customer_type="潜在客户", level_code="Tier-1",
                source="合作伙伴", target_team="Synthetic", contact_name="Person", contact_title="Director",
                contact_role="决策者", **changes)


@pytest.mark.parametrize('model', [CustomerCreate, CustomerCreationRequestBody])
def test_legacy_create_defaults_to_customer_without_inferring_source_or_name(model):
    value = payload()
    value['partner_name'] = 'Legacy partner text'
    item = model.model_validate(value)
    assert item.business_identities == ['customer']
    assert item.partner_profile_id is None and item.partner_id is None


@pytest.mark.parametrize('roles,kind,expected', [
    (['partner'], None, ['partner']),
    (['partner', 'customer'], '商机客户', ['customer', 'partner']),
    (['customer', 'customer'], '已成单客户', ['customer']),
])
def test_three_roles_and_canonical_order(roles, kind, expected):
    data = payload(); data.update(business_identities=roles, customer_type=kind)
    item = CustomerCreationRequestBody.model_validate(data)
    assert item.business_identities == expected and item.customer_type == kind


@pytest.mark.parametrize('changes', [
    {'business_identities': []}, {'business_identities': None}, {'business_identities': ['owner']},
    {'business_identities': ['partner']}, {'business_identities': ['customer'], 'customer_type': None},
    {'business_identities': ['customer'], 'partner_profile_id': str(uuid4())},
])
def test_invalid_identity_does_not_silently_change_business_semantics(changes):
    data = payload(); data.update(changes)
    with pytest.raises(ValidationError): CustomerCreationRequestBody.model_validate(data)


@pytest.mark.parametrize('model', [CustomerUpdate, CustomerProfileUpdate])
def test_old_update_omission_preserves_roles_type_and_stable_ids(model):
    body = model.model_validate({'version_no': 3, 'name': 'New name'})
    data = body.model_dump(exclude_unset=True); data.pop('version_no')
    before = {'business_identities': ['customer', 'partner'], 'customer_type_code': '新拓客户',
              'partner_profile_id': uuid4(), 'primary_partner_id': uuid4()}
    assert identity_update(data, before) == {'name': 'New name'}


def test_partner_only_transition_clears_type_but_preserves_stable_profile():
    profile = uuid4()
    before = {'business_identities': ['customer', 'partner'], 'customer_type_code': '商机客户',
              'partner_profile_id': profile}
    assert identity_update({'business_identities': ['partner']}, before) == {
        'business_identities': ['partner'], 'customer_type': None}
    for value in (None, uuid4()):
        with pytest.raises(ValueError, match='不可替换'):
            identity_update({'partner_profile_id': value}, before)


def test_adding_customer_requires_valid_type_but_unrelated_legacy_edits_remain_valid():
    before = {'business_identities': ['partner'], 'customer_type_code': None}
    with pytest.raises(ValueError): identity_update({'business_identities': ['customer', 'partner']}, before)
    data = {'business_identities': ['customer', 'partner'], 'customer_type': '潜在客户'}
    assert identity_update(data, before) == data
    assert identity_update({'name': 'Legacy rename'}, {'customer_type_code': None}) == {'name': 'Legacy rename'}


@pytest.mark.parametrize('kind', ['存量客户', '公司分配', None])
def test_existing_customer_type_is_preserved_when_echoed_or_adding_partner(kind):
    before = {'business_identities': ['customer'], 'customer_type_code': kind}
    for roles in (['customer'], ['customer', 'partner']):
        data = {'business_identities': roles, 'customer_type': kind}
        assert identity_update(data, before) == data
    with pytest.raises(ValueError):
        identity_update({'customer_type': 'New invalid classification'}, before)


@pytest.mark.asyncio
async def test_partner_selection_is_id_based_active_scoped_and_rejects_self():
    p = uuid4(); c = SimpleNamespace(fetchrow=AsyncMock(return_value={'id': p, 'name': 'Selected', 'status': 'active'}))
    result = await related_partner(c, {'partner_id': p, 'partner_name': 'untrusted'})
    assert result == {'partner_id': str(p), 'partner_name': 'Selected'}
    assert c.fetchrow.await_args.args[1] == str(p)
    assert 'workspace_id=common.current_workspace_id()' in c.fetchrow.await_args.args[0]
    with pytest.raises(ValueError, match='自身'):
        await related_partner(c, {'partner_id': p, 'partner_profile_id': p})
    c.fetchrow.return_value = None
    with pytest.raises(ValueError): await related_partner(c, {'partner_id': p})
    c.fetchrow.return_value = {'id': p, 'name': 'Selected', 'status': 'inactive'}
    with pytest.raises(ValueError): await related_partner(c, {'partner_id': p})
    assert (await related_partner(c, {'partner_id': p}, {'primary_partner_id': p}))['partner_id'] == str(p)


@pytest.mark.asyncio
async def test_legacy_text_never_auto_resolves_and_explicit_clear_is_preserved():
    c = SimpleNamespace(fetchrow=AsyncMock())
    assert (await related_partner(c, {'partner_name': 'Legacy'}))['partner_name'] == 'Legacy'
    c.fetchrow.assert_not_awaited()
    p = uuid4()
    assert await related_partner(c, {'partner_id': None}, {'primary_partner_id': p}) == {
        'partner_id': None, 'partner_name': ''}
    changed = await related_partner(c, {'partner_name': 'Changed'}, {
        'primary_partner_id': p, 'primary_partner_name': 'Old'})
    assert changed == {'partner_name': 'Changed', 'partner_id': None}


def test_pure_partner_profile_is_not_applicable_even_with_old_customer_scores():
    profile = customer_profile({'business_identities': ['partner'], 'potential_score': 90},
                               [{'status': 'won', 'probability': 100}], [{'status': 'archived'}], [], [])
    assert profile['scope'] == 'not_applicable' and profile['coverage'] == 0
    assert all(d['value'] is None for d in profile['dimensions'])
    assert CustomerProfile.model_validate(profile).scope == 'not_applicable'


@pytest.mark.asyncio
async def test_pure_partner_skips_both_shared_job_producers_before_any_write():
    from sales_backend.repositories.jobs import enqueue_battle_map_review
    from sales_backend.repositories.customer_risk import enqueue_customer_risk_review
    c = SimpleNamespace(fetchval=AsyncMock(return_value=False), execute=AsyncMock())
    for enqueue in (enqueue_battle_map_review, enqueue_customer_risk_review):
        await enqueue(c, SimpleNamespace(), customer_id=str(uuid4()), trigger_type='changed', trigger_id=str(uuid4()))
    c.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_opportunity_rejects_pure_partner_before_creation_side_effects(monkeypatch):
    from sales_backend.services import opportunities
    monkeypatch.setattr(opportunities, 'require_permission', AsyncMock())
    monkeypatch.setattr(opportunities, 'customer_for_opportunity', AsyncMock(return_value={
        'id': str(uuid4()), 'name': 'Partner', 'business_identities': ['partner']}))
    writer = AsyncMock(); monkeypatch.setattr(opportunities, 'write_opportunity', writer)
    with pytest.raises(ValueError, match='纯合作伙伴'):
        await opportunities.save_opportunity(SimpleNamespace(execute=AsyncMock()), SimpleNamespace(),
            customer_id=str(uuid4()), data={'action': 'create'})
    writer.assert_not_awaited()


def test_real_openapi_exposes_same_filter_and_identity_on_both_create_routes():
    from sales_backend.main import app
    spec = app.openapi()
    for path in ('/api/v1/customers','/api/v1/customers/claim-pool','/api/v1/customers/portfolio',
                 '/api/v1/customer-assets/map','/api/v1/console/customers'):
        field = next(p for p in spec['paths'][path]['get']['parameters'] if p['name']=='business_identity')
        assert field['schema']['enum'] == ['all','customer','partner']
        assert field['schema']['default'] == 'all'
    for name in ('CustomerCreate','CustomerCreationRequestBody','CustomerUpdate','CustomerProfileUpdate'):
        props=spec['components']['schemas'][name]['properties']
        assert {'business_identities','partner_id','partner_profile_id'} <= props.keys()


def test_omitted_fields_preserve_old_idempotency_payload_shape():
    from sales_backend.domain.customer_identity import legacy_compatible_payload
    body = CustomerCreate.model_validate(payload())
    encoded = legacy_compatible_payload(body)
    assert not {'business_identities','partner_id','partner_profile_id'} & encoded.keys()
    modified = CustomerCreate.model_validate({**payload(), 'business_identities': ['customer','partner']})
    assert legacy_compatible_payload(modified)['business_identities'] == ['customer','partner']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['customer_chatbi', 'opportunity_draft'])
async def test_pure_partner_customer_agent_rejected_before_inference(monkeypatch, mode):
    from sales_backend.services import agent_access
    monkeypatch.setattr(agent_access, 'require_permission', AsyncMock())
    c = SimpleNamespace(fetchval=AsyncMock(return_value=False))
    with pytest.raises(ValueError, match='纯合作伙伴'):
        await agent_access.require_agent_access(c, SimpleNamespace(), mode, str(uuid4()))


@pytest.mark.asyncio
async def test_stale_pure_partner_map_job_never_loads_facts_or_calls_provider(monkeypatch):
    from contextlib import asynccontextmanager
    from sales_backend.services import battle_map_reviews
    c = SimpleNamespace(fetchval=AsyncMock(return_value=False))
    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield c
    handler = battle_map_reviews.BattleMapReviewHandler(SimpleNamespace(transaction=transaction), None)
    handler._load_facts = AsyncMock()
    handler._evaluate = AsyncMock()
    handler._persist = AsyncMock()
    monkeypatch.setattr(battle_map_reviews, 'record_job_effect', AsyncMock())
    await handler.handle(str(uuid4()), SimpleNamespace(workspace_id=str(uuid4())))
    handler._load_facts.assert_not_awaited()
    handler._evaluate.assert_not_awaited()
    handler._persist.assert_not_awaited()
