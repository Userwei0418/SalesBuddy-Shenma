"""Real approval API normalization and locked repository flow; no external I/O."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import ValidationError

from sales_backend.api import operations_customers as api
from sales_backend.contracts.customer_creation import CustomerCreationRequestDecision
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.repositories import customer_creation_requests as repository

OWN = '10000000-0000-0000-0000-000000000001'
EXTERNAL = '20000000-0000-0000-0000-000000000002'
REQUEST = UUID('30000000-0000-0000-0000-000000000003')
BASE = dict(name='合成企业', customer_type='潜在客户', level_code='Tier-1', source='销售自拓',
            target_team='合成团队', contact_name='联系人', contact_title='职位', contact_role='使用者')
IDENTITIES = [
    dict(business_identities=['customer'], customer_type='潜在客户', partner_profile_id=None, partner_id=EXTERNAL),
    dict(business_identities=['partner'], customer_type=None, partner_profile_id=OWN, partner_id=None),
    dict(business_identities=['customer', 'partner'], customer_type='已成单客户', partner_profile_id=OWN, partner_id=EXTERNAL),
]


async def approve(monkeypatch, original, approved, *, version=7):
    locked = dict(id=str(REQUEST), payload=original, status='pending', version_no=7)
    connection = SimpleNamespace(fetchval=AsyncMock(side_effect=[locked, {**locked, 'status': 'approved'}]),
                                 execute=AsyncMock())
    create = AsyncMock(return_value={'id': OWN})
    permission = AsyncMock()
    monkeypatch.setattr(repository, 'require_permission', permission)
    monkeypatch.setattr(repository.CustomerMutationRepository, 'create', create)
    captured = {}

    async def boundary(database, actor, key, operation, fingerprint, action):
        captured.update(key=key, operation=operation, fingerprint=fingerprint)
        return await action(connection)

    monkeypatch.setattr(api, 'management_write', boundary)
    body = CustomerCreationRequestDecision.model_validate(dict(
        decision='approved', version_no=version, approved_payload=approved))
    actor = SimpleNamespace(workspace_id=EXTERNAL, user_id=OWN)
    result = await api.review_customer_creation_request(REQUEST, body, 'same-key',
                                                       SimpleNamespace(actor=actor), object())
    permission.assert_awaited_once_with(connection, 'customer.create_review')
    assert 'customer_creation_request_lock' in connection.fetchval.await_args_list[0].args[0]
    assert 'complete_customer_creation_request' in connection.fetchval.await_args_list[1].args[0]
    reviewed = create.await_args.kwargs['data']
    assert connection.execute.await_args.args[2] == reviewed
    assert result['customer']['id'] == OWN
    return reviewed, captured


@pytest.mark.asyncio
@pytest.mark.parametrize('identity', IDENTITIES)
async def test_old_approval_preserves_locked_identity_and_ids_after_api_defaults(monkeypatch, identity):
    original = {**BASE, **identity}
    # The old console always sends a customer-type choice and none of the new fields.
    approved = {**BASE, 'name': '审定企业', 'contact_name': '审定联系人', 'customer_type': '商机客户'}
    reviewed, captured = await approve(monkeypatch, original, approved)
    for field in ('business_identities', 'partner_profile_id', 'partner_id'):
        assert reviewed[field] == identity[field]
        assert field not in captured['fingerprint']['approved_payload']
    assert reviewed['customer_type'] == ('商机客户' if 'customer' in identity['business_identities'] else None)
    assert reviewed['name'] == '审定企业' and reviewed['contact_name'] == '审定联系人'
    assert captured['key'] == 'same-key'


@pytest.mark.asyncio
@pytest.mark.parametrize('identity', IDENTITIES)
async def test_new_approval_preserves_explicit_identity_and_ids(monkeypatch, identity):
    reviewed, captured = await approve(monkeypatch, {**BASE, **identity}, {**BASE, **identity})
    for field in ('business_identities', 'partner_profile_id', 'partner_id', 'customer_type'):
        assert reviewed[field] == identity[field]
    for field in ('partner_profile_id', 'partner_id'):
        assert field in captured['fingerprint']['approved_payload']


@pytest.mark.asyncio
async def test_explicit_clear_and_identity_change_remain_distinct_from_old_omission(monkeypatch):
    original = {**BASE, **IDENTITIES[2]}
    _, omitted = await approve(monkeypatch, original, BASE)
    reviewed, explicit = await approve(monkeypatch, original, {
        **BASE, 'business_identities': ['customer'], 'partner_profile_id': None, 'partner_id': None})
    assert reviewed['business_identities'] == ['customer']
    assert reviewed['partner_profile_id'] is None and reviewed['partner_id'] is None
    assert omitted['fingerprint'] != explicit['fingerprint']
    assert explicit['fingerprint']['approved_payload']['partner_id'] is None


@pytest.mark.asyncio
async def test_explicit_identity_does_not_silently_drop_an_omitted_original_own_profile(monkeypatch):
    with pytest.raises(ValidationError, match='关联自身伙伴档案需要合作伙伴业务身份'):
        await approve(monkeypatch, {**BASE, **IDENTITIES[2]}, {**BASE, 'business_identities': ['customer']})
    repository.CustomerMutationRepository.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_approval_version_stops_before_creation(monkeypatch):
    with pytest.raises(VersionConflict):
        await approve(monkeypatch, {**BASE, **IDENTITIES[2]}, BASE, version=6)
    repository.CustomerMutationRepository.create.assert_not_awaited()


@pytest.mark.parametrize('changes', [
    {'business_identities': []}, {'business_identities': None},
    {'business_identities': ['partner'], 'customer_type': '潜在客户'},
])
def test_explicit_invalid_identity_is_still_rejected_before_repository(changes):
    with pytest.raises(ValidationError):
        CustomerCreationRequestDecision.model_validate(dict(
            decision='approved', approved_payload={**BASE, **changes}))
