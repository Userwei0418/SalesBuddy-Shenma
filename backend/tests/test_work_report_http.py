"""Offline ASGI -> real persistence -> real leased handlers; fake SQL/model only."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from sales_backend.api import work_reports as api
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.domain.work_report import WorkReportError, default_scheme
from sales_backend.job_context import JobLease, current_job_lease
from sales_backend.services.idempotency import IdempotencyConflict
from sales_backend.services.work_report_generation import GenerationProviders, generate_work_report
from sales_backend.services.agent_platform.routing import RoutingPolicy
from sales_backend.services.work_report_jobs import WorkReportGenerationHandler, WorkReportSnapshotHandler
from tests.test_work_report_repository import ACTOR, NOW, READER, FakeReportConnection, result_for


def test_report_surface_has_typed_responses_and_required_mutation_keys():
    app = FastAPI()
    app.include_router(api.router)
    spec = app.openapi()
    operations = [(verb, operation) for path in spec['paths'].values()
                  for verb, operation in path.items() if verb in ('get', 'post', 'put')]
    assert len(operations) == 27
    for verb, operation in operations:
        successes = [value for status, value in operation['responses'].items() if status.startswith('2')]
        assert len(successes) == 1 and successes[0]['content']['application/json']['schema']
        if verb in ('post', 'put'):
            key = next(p for p in operation['parameters'] if p['name'] == 'Idempotency-Key')
            assert key['required'] and key['in'] == 'header' and key['schema']['format'] == 'uuid'
    thin = spec['components']['schemas']['DocumentSchemeReference']['properties']
    assert set(thin) == {'scheme_id', 'version_no', 'name', 'config_hash'}


class SQL(FakeReportConnection):
    def __init__(self):
        super().__init__();self.receipts={};self.readonly=False;self.isolation='read_committed'

    async def execute(self, sql, *args):
        if 'pg_advisory_xact_lock' in sql:
            self.query(sql,args);return 'SELECT 1'
        if sql.strip().startswith('INSERT INTO ops.mutation_receipt'):
            self.receipts[tuple(args[:4])] = dict(request_digest=args[4],response=deepcopy(args[5]));return 'INSERT 0 1'
        if sql.strip().startswith('INSERT INTO ops.job('):
            ident,w,kind,aggregate,payload=args
            self.jobs[ident]=dict(workspace_id=w,job_type=kind,aggregate_id=aggregate,payload=payload,token=str(uuid4()))
            return 'INSERT 0 1'
        return await super().execute(sql,*args)

    async def fetchrow(self, sql, *args):
        if 'SELECT request_digest,response FROM ops.mutation_receipt' in sql:
            return deepcopy(self.receipts.get(tuple(args)))
        if "current_setting('transaction_isolation')" in sql:
            return dict(isolation=self.isolation.replace('_',' '),readonly='on' if self.readonly else 'off',rls='on')
        return await super().fetchrow(sql,*args)

    async def fetchval(self, sql, *args):
        if sql=='SELECT transaction_timestamp()':return NOW
        return await super().fetchval(sql,*args)

    def cursor(self, sql, *args, **kwargs):
        assert self.readonly and self.isolation=='repeatable_read'
        assert kwargs['prefetch']==128 and 'security.authorization_visit' in sql
        async def rows():
            yield dict(id=str(UUID(int=100)),version_no=1,interaction_at=NOW.replace(day=6),
                       follow_up_record='与客户确认演示时间',contributors=[])
        return rows()


class DB:
    def __init__(self):self.connection=SQL();self.settings=SimpleNamespace()
    @asynccontextmanager
    async def transaction(self, actor, *, readonly=False, isolation='read_committed'):
        c=self.connection;c.actor=actor;c.readonly=readonly;c.isolation=isolation
        old=deepcopy((c.rows,c.jobs,c.effects,c.events,c.receipts))
        try:yield c
        except BaseException:
            c.rows,c.jobs,c.effects,c.events,c.receipts=old
            raise


@pytest_asyncio.fixture
async def client(monkeypatch):
    import socket,asyncpg
    def denied(*a,**kw):raise AssertionError('offline test opened network')
    monkeypatch.setattr(socket.socket,'connect',denied);monkeypatch.setattr(socket.socket,'connect_ex',denied)
    monkeypatch.setattr(asyncpg,'connect',denied);monkeypatch.setattr(asyncpg,'create_pool',denied)
    monkeypatch.setattr(api,'datetime',SimpleNamespace(now=lambda _:NOW))
    db=DB();state=SimpleNamespace(actor=ACTOR)
    app=FastAPI();app.include_router(api.router)
    app.dependency_overrides[get_database]=lambda:db
    app.dependency_overrides[get_identity]=lambda:SimpleNamespace(actor=state.actor)
    @app.exception_handler(WorkReportError)
    async def report_error(_,exc):
        return JSONResponse(status_code=exc.status,content=dict(detail=exc.detail,code=exc.code,retryable=exc.retryable))
    @app.exception_handler(IdempotencyConflict)
    async def idem_error(_,exc):
        return JSONResponse(status_code=409,content=dict(detail=str(exc),code='IDEMPOTENCY_CONFLICT'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline.invalid') as http:
        yield http,db,state


async def post(http,path,body,*,key=None,expected=200):
    response=await http.post('/api/v1/work-reports'+path,json=body,headers={'Idempotency-Key':key or str(uuid4())})
    assert response.status_code==expected,response.text
    return response.json()


async def pipeline(http,db):
    config=default_scheme().model_dump(mode='json')
    config['reference_texts']=[dict(id=str(UUID(int=99)),version_no=1,text='PRIVATE REFERENCE')]
    scheme=await post(http,'/schemes',dict(name='我的周报方案',config=config),expected=201)
    snapshot=await post(http,'/snapshots',dict(sources=['visit']),expected=202)
    c=db.connection
    async def handle(kind,ident,handler):
        job_id=next(k for k,v in c.jobs.items() if v['job_type']=='work_report.'+kind and v['aggregate_id']==ident)
        token=current_job_lease.set(JobLease(job_id,c.jobs[job_id]['token']))
        try:await handler.handle(ident,ACTOR)
        finally:current_job_lease.reset(token)
        assert job_id in c.effects
    await handle('snapshot',snapshot['id'],WorkReportSnapshotHandler(db))
    snapshot_response=await http.get('/api/v1/work-reports/snapshots/'+snapshot['id'])
    assert snapshot_response.status_code==200;snapshot=snapshot_response.json();assert snapshot['status']=='ready'
    run=await post(http,'/runs',dict(snapshot_id=snapshot['id'],snapshot_hash=snapshot['snapshot_hash'],
                                   scheme=dict(scheme_id=scheme['id'],version_no=scheme['version_no'])),expected=202)
    assert run['version_no']==2  # attach_job is a persisted transition, not stale initial version.
    async def generate(request,*,assert_active,**kwargs):
        async def model(envelope):
            # Only the model transport is synthetic; full real generator guards run.
            return result_for(request,title='合成周报').content.model_dump(mode='json')
        return await generate_work_report(request,providers=GenerationProviders(direct=model),
            assert_active=assert_active,routing_policy=RoutingPolicy(platform_enabled=False))
    await handle('generate',run['id'],WorkReportGenerationHandler(db,generate=generate))
    saved=(await http.get('/api/v1/work-reports/runs/'+run['id'])).json()
    assert saved['status']=='succeeded' and saved['provider']=='direct'
    doc=(await http.get('/api/v1/work-reports/documents/'+saved['document_id'])).json()
    return scheme,snapshot,saved,doc


@pytest.mark.asyncio
async def test_http_worker_persistence_refresh_share_and_private_revisions(client):
    http,db,state=client
    scheme,snapshot,run,doc=await pipeline(http,db)
    assert doc['status']=='private' and 'PRIVATE REFERENCE' not in str(doc)
    original=doc['latest_revision_id'];ident=doc['id']
    content=deepcopy(doc['revision']['content']);content['title']='发布标题%_'
    content['sections'][0]['items']=[dict(kind='manual',text='人工确认的发布段落')]
    doc=await post(http,f'/documents/{ident}/revisions',dict(version_no=doc['version_no'],content=content))
    doc=await post(http,f'/documents/{ident}/share',dict(version_no=doc['version_no'],confirm=True,
                                                    audience=dict(kind='members',member_ids=[READER.user_id])))
    published=doc['shared_revision_id']
    content['title']='尚未分享的草稿标题'
    doc=await post(http,f'/documents/{ident}/revisions',dict(version_no=doc['version_no'],content=content))
    assert doc['latest_revision_id']!=published and doc['shared_revision_id']==published
    state.actor=READER
    received=await http.get('/api/v1/work-reports/documents/'+ident)
    assert received.status_code==200
    assert received.json()['title']=='发布标题%_' and received.json()['latest_revision_id']==published
    assert not received.json()['can_edit'] and 'PRIVATE REFERENCE' not in received.text
    assert (await http.get(f'/api/v1/work-reports/documents/{ident}/revisions/{original}')).status_code==404
    filtered=(await http.get('/api/v1/work-reports/documents',params={'q':'%_','status':'shared'})).json()
    assert filtered['total']==1 and filtered['items'][0]['title']=='发布标题%_'
    exported=await http.get(f'/api/v1/work-reports/documents/{ident}/export')
    assert exported.status_code==200 and '人工编辑：人工确认的发布段落' in exported.json()['text']
    state.actor=ACTOR
    await post(http,f'/documents/{ident}/unshare',dict(version_no=doc['version_no']))
    state.actor=READER
    assert (await http.get('/api/v1/work-reports/documents/'+ident)).status_code==404


@pytest.mark.asyncio
async def test_required_key_replay_conflict_and_revoked_source_do_not_leak_receipt(client):
    http,db,_=client
    body=dict(sources=['visit']);key=str(uuid4())
    assert (await http.post('/api/v1/work-reports/snapshots',json=body)).status_code==422
    first=await post(http,'/snapshots',body,key=key,expected=202)
    replay=await post(http,'/snapshots',body,key=key,expected=202)
    assert first==replay and len(db.connection.jobs)==1 and len(db.connection.rows['snapshot'])==1
    conflict=await http.post('/api/v1/work-reports/snapshots',json=dict(sources=['risk']),headers={'Idempotency-Key':key})
    assert conflict.status_code==409 and conflict.json()['code']=='IDEMPOTENCY_CONFLICT'
    db.connection.denied.add('visit.read')
    denied=await http.post('/api/v1/work-reports/snapshots',json=body,headers={'Idempotency-Key':key})
    assert denied.status_code==403 and 'metrics' not in denied.json()


@pytest.mark.asyncio
async def test_recovered_reads_deny_revoked_off_page_source_and_cancel_is_final(client):
    http,db,_=client
    scheme,snapshot,run,doc=await pipeline(http,db)
    db.connection.inaccessible.add(str(UUID(int=100)))
    for path in [f"/documents/{doc['id']}",f"/documents/{doc['id']}/export",f"/snapshots/{snapshot['id']}/sources",f"/runs/{run['id']}"]:
        assert (await http.get('/api/v1/work-reports'+path)).status_code==403
    db.connection.inaccessible.clear()
    body=dict(snapshot_id=snapshot['id'],snapshot_hash=snapshot['snapshot_hash'],scheme=dict(scheme_id=scheme['id'],version_no=scheme['version_no']))
    queued=await post(http,'/runs',body,expected=202)
    cancelled=await post(http,f"/runs/{queued['id']}/cancel",dict(version_no=queued['version_no']))
    assert cancelled['status']=='cancelled'
    stale=await http.post(f"/api/v1/work-reports/runs/{queued['id']}/cancel",json=dict(version_no=queued['version_no']),headers={'Idempotency-Key':str(uuid4())})
    assert stale.status_code==409
