"""Dedicated export cookies, projection adapters and failure cleanup boundaries."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid
import zipfile

import httpx
import pytest

from sales_backend.api import closed_company_export as api
from sales_backend.api import tenant_export as active_api
from sales_backend.main import app
from sales_backend.services.closed_company_export import ClosedExportService, ClosedExportSnapshot, digest
from sales_backend.services.material_storage import MaterialStorage
from sales_backend.services.tenant_export import ExportRejected, Limits, Package, build_package
from sales_backend.services.tenant_export_catalog import SECTIONS, required_permissions


TOKEN = 'a'*64
WORKSPACE, USER = str(uuid.uuid4()), str(uuid.uuid4())


def profile():
    return {'session_id':str(uuid.uuid4()), 'workspace_id':WORKSPACE, 'user_id':USER,
            'company_code':'closed-fixture', 'company_name':'关闭公司合成验收', 'account_code':'ADMIN',
            'display_name':'合成管理员', 'expires_at':'2099-01-01T00:00:00+00:00',
            'grants':[{'permission_code':p, 'scope_code':'workspace', 'team_ids':[],
                       'source_code':'test', 'source_id':'fixture', 'effect':'allow'}
                      for p in required_permissions(list(SECTIONS), True)]}


@pytest.fixture
def exports(monkeypatch):
    value = profile()
    service = SimpleNamespace(database=object(), login=AsyncMock(return_value=(TOKEN, value)),
                              call=AsyncMock(return_value=value))
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[api.service] = lambda:service
    monkeypatch.setattr(active_api, '_active', set())
    yield service
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


def client(*, cookie=True):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://export.invalid',
                            headers={'Origin':'https://export.invalid'},
                            cookies={api.COOKIE:TOKEN} if cookie else {})


@pytest.mark.asyncio
async def test_dedicated_login_returns_only_public_profile_and_purpose_cookie(exports):
    async with client(cookie=False) as browser:
        response = await browser.post(api.PREFIX+'/login', json={
            'company_code':'closed-fixture', 'account_code':'ADMIN', 'password':'Synthetic-Password'})
    assert response.status_code == 200
    assert set(response.json()) == {*api.PROFILE_FIELDS, 'company_status'}
    assert 'grants' not in response.json() and 'access_token' not in response.json()
    cookie = response.headers['set-cookie']
    assert api.COOKIE in cookie and 'HttpOnly' in cookie and 'Secure' in cookie
    assert 'SameSite=strict' in cookie and 'Max-Age=900' in cookie and 'Path='+api.PREFIX in cookie
    assert response.headers['cache-control'] == 'private, no-store'
    assert exports.login.call_args.kwargs['previous_hash'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('headers', [{'Authorization':'Bearer ordinary-or-platform-token'}, {'X-Company-ID':WORKSPACE}])
async def test_existing_tokens_and_company_switch_headers_cannot_grant_export(exports, headers):
    async with client() as browser:
        response = await browser.get(api.PREFIX+'/session', headers=headers)
    assert response.status_code == 403
    exports.call.assert_not_called()


@pytest.mark.asyncio
async def test_missing_cookie_cannot_be_overridden_by_query_or_external_origin(exports):
    async with client(cookie=False) as browser:
        assert (await browser.get(api.PREFIX+'/session?optional=true')).status_code == 401
        response = await browser.post(api.PREFIX+'/login', headers={'Origin':'https://other.invalid'},
                                      json={'company_code':'c','account_code':'a','password':'synthetic'})
        assert response.status_code == 403
        response = await browser.post(api.PREFIX+'/login',
                                      json={'company_code':'c','account_code':'a','password':'synthetic','role':'administrator'})
        assert response.status_code == 422
    exports.call.assert_not_called()
    exports.login.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    {'company_code':'  '}, {'account_code':'  '},
    {'password':{'invalid':'SyntheticSecret123'}},
    {'unexpected':'SyntheticSecret123'},
])
async def test_invalid_login_never_echoes_raw_credentials(exports, change):
    async with client(cookie=False) as browser:
        result = await browser.post(api.PREFIX+'/login', json={
            'company_code':'closed-fixture','account_code':'ADMIN',
            'password':'SyntheticSecret123', **change})
    assert result.status_code == 422
    assert 'SyntheticSecret123' not in result.text and 'input' not in result.text
    assert result.headers['cache-control'] == 'no-store'
    exports.login.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint,operation', [('session','status'),('options','status'),('export','authorize')])
async def test_database_timeout_is_not_internal_error_or_missing_original(exports, endpoint, operation):
    async def timeout(selected, *args):
        if selected == operation:
            raise TimeoutError('synthetic database timeout')
    exports.call.side_effect = timeout
    async with client() as browser:
        if endpoint == 'export':
            result = await browser.post(api.PREFIX+'/export', json={'sections':['customers'],'reason':'合成导出验收'})
        else:
            result = await browser.get(api.PREFIX+'/'+endpoint)
    assert result.status_code == 504 and result.headers['cache-control'] == 'no-store'
    assert 'synthetic database timeout' not in result.text
    assert not active_api._active


@pytest.mark.asyncio
async def test_options_respect_denied_scope_and_requery_every_request(exports):
    value = profile()
    value['grants'].append({'permission_code':'customer.read','effect':'deny'})
    exports.call.return_value = value
    async with client() as browser:
        for _ in range(2):
            response = await browser.get(api.PREFIX+'/options')
            assert response.status_code == 200
            items = {row['code']:row['available'] for row in response.json()['sections']}
            assert not items['customers'] and items['opportunities']
    assert exports.call.call_count == 2
    assert exports.call.call_args.args == ('status', digest(TOKEN))


@pytest.mark.asyncio
async def test_expired_session_and_logout_have_no_normal_auth_side_effects(exports):
    exports.call.side_effect = ExportRejected('expired', 401)
    async with client() as browser:
        response = await browser.get(api.PREFIX+'/session')
        assert response.status_code == 401 and response.headers['cache-control'] == 'no-store'
        exports.call.side_effect = None
        response = await browser.post(api.PREFIX+'/logout')
        assert response.status_code == 200 and 'Max-Age=0' in response.headers['set-cookie']
        exports.call.assert_awaited_with('logout', digest(TOKEN))


@pytest.mark.asyncio
async def test_export_prepared_before_bytes_and_receipt_after_complete_stream(exports, monkeypatch, tmp_path):
    folder = tmp_path/'export'; folder.mkdir(); path = folder/'business.zip'; path.write_bytes(b'zip-content')
    package = Package(folder,path,uuid.uuid4().hex,{'workspace_id':WORKSPACE,'record_count':3},'hash')
    build = AsyncMock(return_value=package)
    monkeypatch.setattr(api, 'build_package', build)
    async with client() as browser:
        response = await browser.post(api.PREFIX+'/export', json={'sections':['customers'],'reason':'验收导出用途'})
    assert response.status_code == 200 and response.content == b'zip-content'
    calls = exports.call.call_args_list
    assert [call.args[3] for call in calls if call.args[0]=='event'] == ['prepared','sent']
    assert isinstance(build.call_args.kwargs['source'], api.ClosedExportSource)
    assert not folder.exists() and not active_api._active


@pytest.mark.asyncio
async def test_prepared_audit_failure_prevents_download_and_releases_package(exports, monkeypatch, tmp_path):
    folder = tmp_path/'failed'; folder.mkdir(); path = folder/'business.zip'; path.write_bytes(b'secret-rows')
    package = Package(folder,path,uuid.uuid4().hex,{'workspace_id':WORKSPACE,'record_count':1},'hash')
    monkeypatch.setattr(api, 'build_package', AsyncMock(return_value=package))
    value = profile()
    async def call(operation, *args):
        if operation == 'event' and args[2] == 'prepared':
            raise ExportRejected('auditing unavailable', 503)
        return value
    exports.call.side_effect = call
    async with client() as browser:
        response = await browser.post(api.PREFIX+'/export',json={'sections':['customers'],'reason':'验收导出用途'})
    assert response.status_code == 503 and b'secret-rows' not in response.content
    assert not folder.exists() and not active_api._active


@pytest.mark.asyncio
async def test_active_and_closed_exports_share_admission_limit(exports):
    release = active_api.reserve_export(WORKSPACE)
    try:
        async with client() as browser:
            response = await browser.post(api.PREFIX+'/export',json={'sections':['customers'],'reason':'验收导出用途'})
        assert response.status_code == 429
    finally:
        release()
    assert exports.call.call_args.args[3] == 'failed'


@pytest.mark.asyncio
async def test_initial_category_denial_is_audited_without_build_or_fake_actor(exports, monkeypatch):
    async def call(operation, *args):
        if operation=='authorize':
            raise ExportRejected('category denied',403)
    exports.call.side_effect=call
    build=AsyncMock()
    monkeypatch.setattr(api,'build_package',build)
    async with client() as browser:
        response=await browser.post(api.PREFIX+'/export',json={'sections':['customers'],'reason':'验收导出用途'})
    assert response.status_code==403
    build.assert_not_awaited()
    event=exports.call.call_args.args
    assert event[0]=='event' and event[1]==digest(TOKEN) and event[3]=='denied'
    assert event[4]['error_code']=='ACCESS_DENIED' and 'workspace_id' not in event[4]


@pytest.mark.asyncio
async def test_shared_builder_checks_projection_and_import_associations(tmp_path):
    actor = SimpleNamespace(workspace_id=WORKSPACE, user_id=USER)
    class Source:
        snapshot_at = '2026-09-27T00:00:00+00:00'
        @asynccontextmanager
        async def snapshot(self): yield self
        async def rows(self, table):
            if table.name == 'crm.partner':
                yield {column:None for column in table.columns.split(',')} | {
                    'id':str(uuid.uuid4()),'workspace_id':WORKSPACE,'name':'合成伙伴'}
    package = await build_package(None, actor, ['partners'], False, source=Source(),
                                  limits=Limits(max_bytes=100000,max_rows=5))
    try:
        with zipfile.ZipFile(package.path) as archive:
            row = json.loads(archive.read('crm/partner.ndjson'))
            assert row['workspace_id'] == WORKSPACE and row['name'] == '合成伙伴'
            assert json.loads(archive.read('manifest.json'))['record_count'] == 1
    finally:
        package.cleanup()


@pytest.mark.asyncio
async def test_closed_pagination_rejects_nonadvancing_offsets_and_unreviewed_fields():
    table = SECTIONS['partners'].tables[0]
    for result in ({'table':table.name, 'items':[{'unexpected':1}], 'next_offset':None},
                   {'table':table.name, 'items':[], 'next_offset':100}):
        conn = SimpleNamespace(fetchval=AsyncMock(return_value=result))
        snapshot = ClosedExportSnapshot(conn,'hash',None,False,None,'time')
        with pytest.raises(ExportRejected):
            _ = [row async for row in snapshot.rows(table)]


@pytest.mark.asyncio
@pytest.mark.parametrize('parts', [[(0,b'ab'),(1,b'cd')], [(0,b'a'),(1,b'bc'),(2,b'd')]])
async def test_closed_material_reader_preserves_historical_chunk_sizes(parts):
    rows = [{'chunk_no':index,'content':content} for index, content in parts]+[None]
    conn = SimpleNamespace(fetchrow=AsyncMock(side_effect=rows))
    actor = SimpleNamespace(workspace_id=WORKSPACE)
    snapshot = ClosedExportSnapshot(conn,'hash',actor,True,
        MaterialStorage({'default':'postgres','profiles':{'postgres':{'driver':'postgres'}}}),'time')
    row={'id':str(uuid.uuid4()),'file_size':4,'file_path':'','storage_profile':'postgres',
         'storage_driver':'postgres','storage_key':'fixture','content_sha256':hashlib.sha256(b'abcd').hexdigest()}
    assert await snapshot.read_original(row) == b'abcd'


@pytest.mark.asyncio
async def test_unknown_login_uses_dummy_and_never_issues_token(monkeypatch):
    conn = SimpleNamespace(fetchval=AsyncMock(side_effect=[True,None,True]))
    class Database:
        @asynccontextmanager
        async def connection(self): yield conn
    password = AsyncMock(return_value=False)
    monkeypatch.setattr(asyncio, 'to_thread', password)
    with pytest.raises(ExportRejected) as error:
        await ClosedExportService(Database()).login('company','unknown','fixture','local')
    assert error.value.status == 401
    assert 'DUMMY_HASH' not in str(error.value)
    assert conn.fetchval.call_count == 3
