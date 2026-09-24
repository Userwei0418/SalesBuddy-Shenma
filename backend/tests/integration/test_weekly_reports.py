import json
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from sales_backend.config import get_settings
from sales_backend.db import set_request_context
from sales_backend.integrations.supreme_fde import FdeResult, RunIds, FdeError
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.weekly_reports import WeeklyReportService
from sales_backend.services.weekly_source import build_snapshot, decode_snapshot
from tests.integration.test_operations_api import client_for, sign_in

pytestmark=pytest.mark.asyncio


class TestDatabase:
    __test__=False
    def __init__(self,c,actor):
        self.conn=c
        self.settings=replace(get_settings(), weekly_enabled_workspaces=actor.workspace_id,
            weekly_agent_api_key='app-synthetic-weekly', weekly_agent_app_id='synthetic-app',
            weekly_agent_snapshot_id='synthetic-snapshot',
            agent_fde_base_url='https://ops-salesbuddy.shenzhoukuntai.com:18899/v1')
    @asynccontextmanager
    async def transaction(self,actor,readonly=False,isolation=None):
        async with self.conn.transaction():
            await set_request_context(self.conn,actor)
            yield self.conn


class FakeAgent:
    calls=0
    fail=False
    hook=None
    def __init__(self,config):pass
    async def __aenter__(self):return self
    async def __aexit__(self,*_):pass
    async def info(self):return {'name':'神码-销售周报-weekly.v2'}
    async def parameters(self):return {'user_input_form':[]}
    async def chat(self,**kwargs):
        type(self).calls+=1
        assert kwargs['inputs']=={} and kwargs['conversation_id']==''
        assert kwargs['user'].startswith('weekly-')
        if self.fail:raise FdeError('timeout')
        if type(self).hook:await type(self).hook()
        s=decode_snapshot(kwargs['query']);r=s['records'][0]
        output=dict(schema_version='weekly.v2',status='ready',title='周报',period=s['period'],
            body_markdown='## 隔离测试科技\n客户沟通已完成。',statistics=s['statistics'],warnings=[],
            sections=[dict(key='progress',title='进展',items=[dict(text='客户沟通已完成。',source_ids=[r['id']],
                entity_refs=[],customer_id=r['customer_id'],opportunity_id=r['opportunity_id'],needs_confirmation=False)])])
        return FdeResult(json.dumps(output,ensure_ascii=False),RunIds(message_id='synthetic'),50,60)


async def seed(c,actor,n=65):
    cid=str(await c.fetchval('SELECT id FROM crm.customer WHERE owner_user_ref_id=$1::uuid LIMIT 1',actor.user_id))
    # At insertion the archived rows are immutable. Use the actual transaction waterline.
    await c.execute('''INSERT INTO activity.visit(workspace_id,customer_id,recorder_user_ref_id,recorder_team_id,
        created_by_user_ref_id,form_version_id,status,created_at,interaction_at,follow_up_record,next_action)
        SELECT $1::uuid,$2::uuid,$3::uuid,$4::uuid,$3::uuid,
        (SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1),
        'archived',transaction_timestamp()-interval '1 hour',transaction_timestamp()-interval '30 days',
        repeat('完整跟进正文；',300)||n::text,'人工确定的下一步' FROM generate_series(1,$5::int) n''',
        actor.workspace_id,cid,actor.user_id,actor.team_ids[0],n)
    return cid


async def test_source_complete_recorder_created_at_and_fulltext(connection,sales_actor,monkeypatch):
    from sales_backend.services import weekly_source
    from sales_backend.weekly_contract.validate_response import validate_input
    def diagnostic(value):
        errors=validate_input(value)
        assert not errors,errors
        return errors
    monkeypatch.setattr(weekly_source,"validate_input",diagnostic)
    await seed(connection,sales_actor)
    source,raw,digest=await build_snapshot(connection,sales_actor,str(uuid4()))
    assert len(source['records'])==65
    assert all(len(r['follow_up_record'])>600 for r in source['records'])
    assert source['statistics']==dict(record_count=65,customer_count=1,opportunity_count=0)
    assert all(r['visit_date']<source['period']['start_date'] for r in source['records'])
    assert len(digest)==64 and '完整跟进正文' in raw


async def test_generate_idempotence_save_conflict_owner_isolation_cancel(connection,sales_actor):
    await seed(connection,sales_actor,1)
    service=WeeklyReportService(TestDatabase(connection,sales_actor),FakeAgent)
    FakeAgent.calls=0;FakeAgent.fail=False;FakeAgent.hook=None
    request_id=str(uuid4());r=await service.generate(sales_actor,request_id)
    assert r['status']=='queued'
    assert (await service.generate(sales_actor,request_id))['id']==r['id']
    await service.handle(sales_actor,r['id'])
    report=await service.read(sales_actor,r['id'])
    assert report['status']=='succeeded' and report['draft_version']==1,report
    assert FakeAgent.calls==1 and report['runtime_snapshot_verified'] is False
    saved=await service.save(sales_actor,r['id'],1,'## 人工修订\n由销售核对并保存')
    assert saved['draft_version']==2 and saved['draft_source']=='manual' and not saved['draft_references_validated']
    assert saved['original_result']['body_markdown']==report['body_markdown']
    with pytest.raises(HTTPException) as exc:await service.save(sales_actor,r['id'],1,'过期修改')
    assert exc.value.status_code==409
    other=await IdentityRepository().find_actor_by_account(connection,workspace_external_id='demo-sales-workspace',account_code='XS002')
    with pytest.raises(HTTPException) as exc:await service.read(other.context,r['id'])
    assert exc.value.status_code==404
    next_report=await service.generate(sales_actor,str(uuid4()))
    await service.cancel(sales_actor,next_report['id']);await service.handle(sales_actor,next_report['id'])
    assert FakeAgent.calls==1
    assert (await service.read(sales_actor,next_report['id']))['status']=='cancelled'
    assert await connection.fetchval('SELECT count(*) FROM insight.weekly_report_revision WHERE report_id=$1::uuid',r['id'])==2


async def test_timeout_not_retried_and_cancelled_late_result_discarded(connection,sales_actor):
    await seed(connection,sales_actor,1)
    service=WeeklyReportService(TestDatabase(connection,sales_actor),FakeAgent)
    FakeAgent.calls=0;FakeAgent.fail=True;FakeAgent.hook=None
    r=await service.generate(sales_actor,str(uuid4()));await service.handle(sales_actor,r['id'])
    assert (await service.read(sales_actor,r['id']))['status']=='failed'
    await service.handle(sales_actor,r['id']);assert FakeAgent.calls==1
    FakeAgent.fail=False
    r=await service.generate(sales_actor,str(uuid4()))
    async def cancel():await service.cancel(sales_actor,r['id'])
    FakeAgent.hook=cancel
    try:await service.handle(sales_actor,r['id'])
    finally:FakeAgent.hook=None
    out=await service.read(sales_actor,r['id'])
    assert out['status']=='cancelled' and out['body_markdown'] is None


async def test_empty_and_context_limit_and_platform_isolation(connection,sales_actor):
    db=TestDatabase(connection,sales_actor);service=WeeklyReportService(db,FakeAgent)
    r=await service.generate(sales_actor,str(uuid4()))
    assert r['status']=='succeeded' and r['result_status']=='insufficient_data'
    await seed(connection,sales_actor,1)
    db.settings=replace(db.settings,weekly_max_input_bytes=50)
    service=WeeklyReportService(db,FakeAgent)
    with pytest.raises(HTTPException) as exc:await service.generate(sales_actor,str(uuid4()))
    assert exc.value.detail=='WEEKLY_CONTEXT_TOO_LARGE'
    db.settings=replace(db.settings,agent_fde_base_url='https://internal.invalid/v1')
    with pytest.raises(HTTPException) as exc:WeeklyReportService(db).binding(sales_actor)
    assert exc.value.detail=='WEEKLY_CUSTOMER_PLATFORM_REQUIRED'


async def test_business_web_login_keeps_console_and_mini_program_separate(connection):
    async with await client_for(connection) as client:
        await sign_in(client)
        team=(await client.get('/api/v1/console/organization')).json()['departments'][0]['id']
        code='WEEKLY'+uuid4().hex[:8]
        response=await client.post('/api/v1/console/accounts',headers={'Idempotency-Key':str(uuid4())},json={
            'account_code':code,'display_name':'周报隔离测试','team_id':team,'roles':['sales'],
            'temporary_password':'Weekly-Isolated-2026'})
        assert response.status_code==201,response.text
        response=await client.post('/api/v1/web/auth/login',json={'account_code':code,'password':'Weekly-Isolated-2026'})
        assert response.status_code==200,response.text
        assert 'refresh_token' not in response.json() and 'HttpOnly' in response.headers['set-cookie']
        client.headers['Authorization']='Bearer '+response.json()['access_token']
        assert (await client.get('/api/v1/web/auth/me')).status_code==200
        response=await client.post('/api/v1/web/auth/password',json={'old_password':'Weekly-Isolated-2026','new_password':'Weekly-Changed-2026'})
        assert response.status_code==200,response.text
        response=await client.post('/api/v1/web/auth/login',json={'account_code':code,'password':'Weekly-Changed-2026'})
        client.headers['Authorization']='Bearer '+response.json()['access_token']
        assert (await client.get('/api/v1/console/organization')).status_code==403
        response=await client.post('/api/v1/web/auth/refresh');assert response.status_code==200,response.text
        assert (await client.post('/api/v1/web/auth/refresh',headers={'Origin':'https://evil.invalid'})).status_code==403
        native=await client.post('/api/v1/auth/password/login',json={'account_code':code,'password':'Weekly-Changed-2026'})
        client.headers['Authorization']='Bearer '+native.json()['access_token']
        assert (await client.get('/api/v1/web/weekly-reports')).status_code==403


async def test_dates_money_stage_snapshot_and_immutable_input(connection,sales_actor):
    cid=await seed(connection,sales_actor,1)
    oid=str(uuid4())
    await connection.execute('''INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,amount,currency,
        probability,stage_code,owner_user_ref_id,owner_team_id,created_by_user_ref_id,updated_at)
        VALUES($1::uuid,$2::uuid,$3::uuid,'精度测试',9999999999999999.99,'CNY',50,'solution',
        $4::uuid,$5::uuid,$4::uuid,transaction_timestamp()-interval '1 day')''',
        oid,sales_actor.workspace_id,cid,sales_actor.user_id,sales_actor.team_ids[0])
    await connection.execute('''INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,
        recorder_user_ref_id,recorder_team_id,created_by_user_ref_id,form_version_id,status,
        created_at,interaction_at,follow_up_record)
        SELECT $1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$4::uuid,
        (SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1),
        x.status,x.created_at,transaction_timestamp()-interval '30 days','时间窗口测试'
        FROM (VALUES
         ('confirmed',date_trunc('day',transaction_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai'-interval '13 days'),
         ('archived',date_trunc('day',transaction_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai'-interval '13 days 1 second'),
         ('pending_confirm',transaction_timestamp()-interval '1 hour'),
         ('withdrawn',transaction_timestamp()-interval '1 hour'),
         ('archived',transaction_timestamp()+interval '1 hour')) AS x(status,created_at)''',
        sales_actor.workspace_id,cid,oid,sales_actor.user_id,sales_actor.team_ids[0])
    source,raw,_=await build_snapshot(connection,sales_actor,str(uuid4()))
    assert len(source['records'])==2
    o=decode_snapshot(raw)['context']['opportunities'][0]
    assert o['amount']==Decimal('9999999999999999.99') and o['stage_label']=='方案沟通'
    service=WeeklyReportService(TestDatabase(connection,sales_actor),FakeAgent)
    r=await service.generate(sales_actor,str(uuid4()))
    import asyncpg
    with pytest.raises(asyncpg.RaiseError):
        async with connection.transaction():
            await connection.execute("UPDATE insight.weekly_report SET input_snapshot='{}' WHERE id=$1::uuid",r['id'])
