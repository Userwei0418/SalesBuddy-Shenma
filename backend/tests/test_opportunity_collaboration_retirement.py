"""Focused API/service retirement checks; no database or external services."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from sales_backend.api import opportunity_access as api
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.domain.opportunity_access import OpportunityAccessError
from sales_backend.services import opportunity_access as service

OPPORTUNITY = UUID(int=1)
CUSTOMER = UUID(int=2)


def client_for(database):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=object())
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test')


@pytest.mark.asyncio
async def test_retired_endpoints_and_service_never_open_business_transaction():
    database = SimpleNamespace(transaction=Mock(side_effect=AssertionError('retired endpoint opened a transaction')))
    async with client_for(database) as client:
        candidates = await client.get(f'/api/v1/opportunities/{OPPORTUNITY}/access-candidates',
                                      params={'purpose': 'collaborate'})
        update = await client.put(f'/api/v1/opportunities/{OPPORTUNITY}/collaborators',
            headers={'Idempotency-Key': str(UUID(int=3))}, json={'version_no': 1, 'member_ids': []})
    for response in (candidates, update):
        assert response.status_code == 410
        body = response.json()
        assert body['code'] == 'OPPORTUNITY_COLLABORATION_RETIRED'
        assert body['message'] == '商机协作名单设置已停用，请刷新页面'
        assert body['retryable'] is False
    database.transaction.assert_not_called()
    connection = AsyncMock()
    with pytest.raises(OpportunityAccessError) as error:
        await service.set_collaborators(connection, object(), OPPORTUNITY, object(), None)
    assert error.value.status == 410 and error.value.code == 'OPPORTUNITY_COLLABORATION_RETIRED'
    assert not connection.mock_calls


@pytest.mark.asyncio
async def test_access_and_both_option_orders_only_disable_collaboration(monkeypatch):
    original = {'opportunity_id': str(OPPORTUNITY), 'name': '历史商机', 'version_no': 4,
        'collaborators': [{'user_id': str(UUID(int=4)), 'display_name': '历史协作人'}],
        'capabilities': {'can_transfer': True, 'can_manage_collaborators': True, 'can_follow_up': True, 'can_edit': False},
        'capability_reasons': {'transfer': None}}
    expected = deepcopy(original)
    expected['capabilities']['can_manage_collaborators'] = False
    repository = SimpleNamespace(access=AsyncMock(side_effect=lambda *args: deepcopy(original)),
        options=AsyncMock(side_effect=lambda *args, **kwargs: {'items': [deepcopy(original)], 'total': 1, 'limit': 20, 'offset': 0}))
    monkeypatch.setattr(service, 'OpportunityAccessRepository', lambda: repository)
    capability = AsyncMock(return_value={'can_transfer': True, 'reason': None})
    monkeypatch.setattr(service, 'transfer_capability', capability)
    connection = object()
    assert await service.read_access(connection, OPPORTUNITY) == expected
    for order in ('name', 'recent_follow_up'):
        page = await service.read_options(connection, query='历史', customer_id=CUSTOMER, limit=20, offset=0, order=order)
        assert page == {'items': [expected], 'total': 1, 'limit': 20, 'offset': 0}
    assert capability.await_count == 2  # Access + name order; recent order remains batched.
    assert original['capabilities']['can_manage_collaborators'] is True


@pytest.mark.asyncio
async def test_transfer_and_company_follow_up_routes_keep_existing_contracts(monkeypatch):
    reference = {'opportunity_id': str(OPPORTUNITY), 'name': '公司商机',
        'customer_reference': {'id': str(CUSTOMER), 'name': '公司客户'}, 'can_follow_up': True}
    options = {'items': [reference], 'total': 1, 'limit': 20, 'offset': 0}
    customers = {'items': [reference['customer_reference']], 'total': 1, 'limit': 20, 'offset': 0}
    connection = SimpleNamespace(fetchval=AsyncMock(side_effect=[options, customers, reference]))
    transactions = []

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        transactions.append(kwargs)
        yield connection

    transfer = AsyncMock(return_value={'items': [], 'total': 0})
    monkeypatch.setattr(api, 'transfer_candidates', transfer)
    async with client_for(SimpleNamespace(transaction=transaction)) as client:
        response = await client.get(f'/api/v1/opportunities/{OPPORTUNITY}/access-candidates',
            params={'purpose': 'transfer', 'q': ' 转交 ', 'limit': 20, 'offset': 0})
        assert response.status_code == 200 and response.json() == {'items': [], 'total': 0}
        transfer.assert_awaited_once_with(connection, OPPORTUNITY, query='转交', limit=20, offset=0)
        for route, expected in (('/company-follow-up-options', options), ('/company-follow-up-customers', customers),
                                (f'/{OPPORTUNITY}/follow-up-access', reference)):
            response = await client.get('/api/v1/opportunities' + route)
            assert response.status_code == 200 and response.json() == expected
    assert all(transaction['readonly'] is True for transaction in transactions)
    assert [call.args[0].split('(')[0] for call in connection.fetchval.call_args_list] == [
        'SELECT security.company_opportunity_follow_up_options',
        'SELECT security.company_opportunity_follow_up_customers',
        'SELECT security.company_opportunity_follow_up_reference',
    ]
