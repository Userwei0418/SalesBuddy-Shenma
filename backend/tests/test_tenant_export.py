import asyncio
from datetime import date
from decimal import Decimal
import json
from types import SimpleNamespace
import uuid

import pytest
from pydantic import ValidationError
from starlette.requests import ClientDisconnect

from sales_backend.api.tenant_export import ExportRequest, ExportResponse
from sales_backend.domain.authorization import Authorization, Grant
from sales_backend.services.tenant_export import Budget, ExportRejected, Limits, Package, SnapshotDatabase, json_bytes, table_query
from sales_backend.services.tenant_export_catalog import SECTIONS, require_permissions, required_permissions


def effective(sections, originals=False, denied=(), narrow=None):
    return Authorization.combine('company','user', [Grant(p, 'self' if p == narrow else 'workspace')
        for p in required_permissions(sections, originals)], account_denies=denied)


@pytest.mark.parametrize('code', list(SECTIONS))
def test_export_needs_its_own_permission_and_each_business_permission(code):
    require_permissions(effective([code]), [code], False)
    for permission in required_permissions([code], False):
        with pytest.raises(PermissionError):
            require_permissions(effective([code], denied=[permission]), [code], False)


def test_workspace_export_does_not_expand_a_narrow_business_grant():
    with pytest.raises(PermissionError):
        require_permissions(effective(['customers'], narrow='customer.read'), ['customers'], False)


@pytest.mark.parametrize('value', [
    {'sections':[]}, {'sections':['bad']}, {'sections':['customers','customers']},
    {'sections':['customers'],'include_originals':True}, {'sections':['visits'],'workspace_id':str(uuid.uuid4())},
    {'sections':['customers'],'reason':'   '},
])
def test_requests_cannot_select_tenants_or_unsupported_combinations(value):
    with pytest.raises(ValidationError): ExportRequest.model_validate({'reason':'测试数据交付',**value})


def test_original_permission_is_separate():
    with pytest.raises(PermissionError): require_permissions(effective(['visits']), ['visits'], True)


def test_budget_refuses_instead_of_truncating():
    budget=Budget(Limits(max_bytes=10,max_rows=2))
    budget.add(5,1)
    with pytest.raises(ExportRejected): budget.add(6)
    with pytest.raises(ExportRejected): budget.add(1,2)
    assert (budget.byte_count,budget.row_count)==(5,1)


def test_explicit_projection_has_no_credentials_or_storage_paths():
    prohibited={'password_hash','mfa_secret','refresh_token_hash','file_path','storage_key','attributes','import_meta'}
    for section in SECTIONS.values():
        for table in section.tables:
            assert not prohibited & set(table.columns.split(','))
            assert '*' not in table_query(table)
            assert 'common.current_workspace_id()' in table_query(table)


def test_json_preserves_decimal_text_dates_unicode_and_formula_like_text():
    value={'amount':Decimal('9007199254740993.01'),'day':date(2026,9,26),'text':'=中国\n客户'}
    decoded=json.loads(json_bytes(value))
    assert decoded['amount']=='9007199254740993.01' and decoded['text']==value['text']


@pytest.mark.asyncio
async def test_snapshot_adapter_rejects_a_different_actor_and_writes():
    actor=SimpleNamespace(id=1);conn=object();adapter=SnapshotDatabase(conn,actor)
    async with adapter.transaction(actor,readonly=True) as result: assert result is conn
    with pytest.raises(PermissionError):
        async with adapter.transaction(SimpleNamespace(id=2),readonly=True): pass
    with pytest.raises(PermissionError):
        async with adapter.transaction(actor): pass


@pytest.mark.asyncio
async def test_response_cleanup_on_send_failure_and_cancellation(tmp_path):
    for error in (RuntimeError('transport ended'),asyncio.CancelledError()):
        folder=tmp_path/uuid.uuid4().hex;folder.mkdir();path=folder/'data.zip';path.write_bytes(b'payload')
        package=Package(folder,path,'id',{'workspace_id':'company','record_count':1},'digest')
        released=[]
        async def authorize(): pass
        async def send(message): raise error
        async def receive(): return {'type':'http.disconnect'}
        response=ExportResponse(package,authorize,lambda:released.append(True))
        with pytest.raises(BaseException):
            await response({'type':'http','asgi':{'spec_version':'2.4'}},receive,send)
        assert not folder.exists() and released==[True]
        assert response.body_iterator.ag_frame is None


def test_limits_are_deployment_bounds_not_unlimited(monkeypatch):
    monkeypatch.setenv('SALES_TENANT_EXPORT_MAX_BYTES','-1')
    with pytest.raises(ExportRejected): Limits.configured()


@pytest.mark.asyncio
async def test_stream_stops_after_authorization_revoked_and_deletes_artifact(tmp_path):
    folder=tmp_path/'export';folder.mkdir();path=folder/'data.zip';path.write_bytes(b'x'*(3*1024*1024))
    package=Package(folder,path,'id',{'workspace_id':'company','record_count':1},'digest')
    calls=[];bodies=[];released=[]
    async def authorize():
        calls.append(True)
        if len(calls)>1: raise PermissionError('revoked')
    async def send(message):
        if message['type']=='http.response.body': bodies.append(message['body'])
    async def receive(): return {'type':'http.disconnect'}
    response=ExportResponse(package,authorize,lambda:released.append(True))
    with pytest.raises(ClientDisconnect):
        await response({'type':'http','asgi':{'spec_version':'2.4'}},receive,send)
    assert sum(map(len,bodies))==1024*1024
    assert not folder.exists() and released==[True]
