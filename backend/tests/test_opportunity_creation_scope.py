"""Creation capability API and shared save/archive rejection before business writes."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from sales_backend.api import business
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.opportunity_creation import OpportunityCreationError, translate_creation_error
from sales_backend.error_contract import error_response
from sales_backend.repositories.opportunity_creation import creation_access
from sales_backend.services import opportunities, visit_archive


@pytest.mark.asyncio
async def test_creation_capability_is_minimal_and_keeps_refused_target_private():
    customer_id=str(uuid4())
    c=SimpleNamespace(fetchval=AsyncMock(return_value={
        'can_create_opportunity':True,'denial_reason':None,
        'customer_reference':{'id':customer_id,'name':'Synthetic','owner_team_id':'private','business_identities':['customer']}}))
    assert (await creation_access(c,customer_id))['customer_reference']=={'id':customer_id,'name':'Synthetic'}
    c.fetchval.return_value={'can_create_opportunity':False,'denial_reason':'OPPORTUNITY_CUSTOMER_CLAIM_REQUIRED','customer_reference':None}
    assert (await creation_access(c,customer_id))['customer_reference'] is None


@pytest.mark.asyncio
async def test_candidate_route_bounds_and_forwards_exact_search_pagination():
    actor=ActorContext(workspace_id=str(uuid4()),user_id=str(uuid4()),role='sales',data_scope='self')
    c=SimpleNamespace(fetchval=AsyncMock(return_value={'items':[],'total':0,'limit':7,'offset':14}))
    @asynccontextmanager
    async def transaction(*args,**kwargs):
        assert kwargs['readonly'] is True
        yield c
    app=FastAPI();app.include_router(business.router)
    app.dependency_overrides[get_identity]=lambda:SimpleNamespace(actor=actor)
    app.dependency_overrides[get_database]=lambda:SimpleNamespace(transaction=transaction)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
        response=await client.get('/api/v1/opportunities/create-customers',params={'q':' Synthetic ','limit':7,'offset':14})
        assert response.status_code==200
        assert c.fetchval.await_args.args[1:]==('Synthetic',7,14)
        assert (await client.get('/api/v1/opportunities/create-customers',params={'limit':101})).status_code==422
        assert (await client.get('/api/v1/opportunities/create-access',params={'customer_id':'bad'})).status_code==422


@pytest.mark.asyncio
async def test_direct_save_denial_before_material_or_write_and_update_uses_original_reference(monkeypatch):
    monkeypatch.setattr(opportunities,'require_permission',AsyncMock())
    guard=AsyncMock(side_effect=OpportunityCreationError('OPPORTUNITY_CUSTOMER_CLAIM_SCOPE_DENIED'))
    reference=AsyncMock(return_value=None)
    writer=AsyncMock()
    monkeypatch.setattr(opportunities,'require_creation_customer',guard)
    monkeypatch.setattr(opportunities,'customer_for_opportunity',reference)
    monkeypatch.setattr(opportunities,'write_opportunity',writer)
    c=SimpleNamespace(execute=AsyncMock())
    with pytest.raises(OpportunityCreationError):
        await opportunities.save_opportunity(c,SimpleNamespace(),customer_id=str(uuid4()),data={'action':'create'})
    reference.assert_not_awaited();writer.assert_not_awaited()
    guard.reset_mock()
    with pytest.raises(LookupError):
        await opportunities.save_opportunity(c,SimpleNamespace(),customer_id=str(uuid4()),data={'action':'update'})
    reference.assert_awaited_once();guard.assert_not_awaited()


@pytest.mark.asyncio
async def test_visit_human_archive_new_opportunity_uses_same_guard_before_creating_visit(monkeypatch):
    for name in ('require_capability','require_visit_recording_scope','validate_visit_optional_actions','validate_visit_attendance'):
        monkeypatch.setattr(visit_archive,name,AsyncMock())
    monkeypatch.setattr(opportunities,'require_permission',AsyncMock())
    guard=AsyncMock(side_effect=OpportunityCreationError('OPPORTUNITY_CUSTOMER_CLAIM_REQUIRED'))
    monkeypatch.setattr(opportunities,'require_creation_customer',guard)
    monkeypatch.setattr(visit_archive,'VisitReviewRepository',lambda:SimpleNamespace(lock_previous_archive=AsyncMock(return_value=None)))
    writer=AsyncMock()
    monkeypatch.setattr(visit_archive,'VisitRepository',lambda:SimpleNamespace(create=writer))
    fields={'_quality_review_run_id':str(uuid4()),'_opportunity_mutation':{
        'action':'create','name':'Synthetic','amount':'100','probability':10,'expected_close_date':'2026-12-01'}}
    monkeypatch.setattr(visit_archive,'consume_review',AsyncMock(return_value=(fields,str(uuid4()))))
    actor=ActorContext(workspace_id=str(uuid4()),user_id=str(uuid4()),role='sales',data_scope='self')
    with pytest.raises(OpportunityCreationError):
        await visit_archive.archive_visit(SimpleNamespace(execute=AsyncMock()),actor,customer_id=str(uuid4()),fields=fields)
    guard.assert_awaited_once();writer.assert_not_awaited()


def test_only_private_claim_sqlstate_is_translated_and_other_conflicts_stay_errors():
    failure=asyncpg.PostgresError('OPPORTUNITY_CUSTOMER_CLAIM_REQUIRED');failure.sqlstate='P0297';failure.message='OPPORTUNITY_CUSTOMER_CLAIM_REQUIRED'
    with pytest.raises(OpportunityCreationError) as raised: translate_creation_error(failure)
    assert raised.value.code=='OPPORTUNITY_CUSTOMER_CLAIM_REQUIRED'
    conflict=asyncpg.SerializationError('synthetic concurrent change')
    with pytest.raises(asyncpg.SerializationError): translate_creation_error(conflict)
    result=error_response(SimpleNamespace(state=SimpleNamespace(),headers={}),status_code=403,code=raised.value.code)
    assert '请先认领客户'.encode() in result.body
    assert b'P0297' not in result.body
