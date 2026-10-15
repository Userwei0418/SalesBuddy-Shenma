"""Opt-in disposable Review M1 proof. Explicit unique Unix socket; no saved DSN."""
import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import date
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'database/tests'), str(ROOT/'database/scripts')]
from migrate import apply, item
from verify_runtime_grants_postgres import migrate_through
from sales_backend.api.review import router
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.services.operations import OperationsError
from sales_backend.services.idempotency import IdempotencyConflict
from sales_backend.contracts.targets import TargetSave
from sales_backend.services.targets import save_target as legacy_save
from sales_backend.repositories.targets import TargetRepository
from sales_backend.contracts.targets import TargetDecision
from sales_backend.services.review import target_rows
sys.path.insert(0, str(ROOT/'tools/installation'))
from runtime_roles import SCHEMAS


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


async def rls_contract(connection):
    tables = [dict(r) for r in await connection.fetch('''SELECT n.nspname||'.'||c.relname AS name,
        c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p') ORDER BY 1''', SCHEMAS)]
    policies = [dict(r) for r in await connection.fetch('''SELECT schemaname,tablename,policyname,
        permissive,roles::text[],cmd,qual,with_check FROM pg_policies
        WHERE schemaname=ANY($1::text[]) ORDER BY 1,2,3''', SCHEMAS)]
    for row in policies:
        row['roles'] = sorted(row['roles'])
    return {'tables': tables, 'policies': policies}


async def grants(c, role):
    policy=json.loads((ROOT/'tools/installation/runtime_permissions.json').read_text())['services']['api']
    for schema in policy['schemas']: await c.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
    for table, privileges in policy['tables'].items():
        if await c.fetchval('SELECT to_regclass($1) IS NOT NULL',table):
            await c.execute(f'GRANT {",".join(privileges)} ON {table} TO "{role}"')
    for table, permissions in policy['columns'].items():
        for privilege, columns in permissions.items():
            await c.execute(f'GRANT {privilege}({",".join(columns)}) ON {table} TO "{role}"')
    for signature in policy['functions']:
        if await c.fetchval('SELECT to_regprocedure($1) IS NOT NULL',signature):
            await c.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO "{role}"')
    await c.execute('SELECT security.reconcile_runtime_grants()')


async def verify(args):
    socket=Path(args.socket).resolve()
    assert str(socket).startswith('/private/tmp/sb-review-pg.') or str(socket).startswith('/tmp/sb-review-pg.')
    config=dict(host=str(socket),port=args.port,user='postgres',password='',passfile='/dev/null',ssl=False)
    admin=await asyncpg.connect(database='postgres',**config)
    db='sb_review_m1_'+uuid4().hex[:12]; role='sb_review_api_'+uuid4().hex[:12]
    c=None; checks=[]
    try:
        await admin.execute(f'CREATE DATABASE "{db}" TEMPLATE template0')
        c=await asyncpg.connect(database=db,**config)
        await migrate_through(c,239)
        old_ledger={r['migration_key']:r['checksum_sha256'] for r in await c.fetch('SELECT * FROM ops.migration_checksum')}
        old_rls=[dict(r) for r in await c.fetch("SELECT schemaname,tablename,policyname,permissive,roles,cmd,qual,with_check FROM pg_policies ORDER BY 1,2,3")]
        rls_before=await rls_contract(c)
        assert digest(rls_before)=='7c76778c9b7290293abbb9df03b6d31ddc0a6568b405faf8d9e316be09cf8686'
        await c.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await grants(c,role)
        actors={}; workspaces={}; users={}
        for label in ('own','foreign'):
            ws=uuid4();workspaces[label]=ws
            await c.execute('INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$2)',ws,'review-'+label+str(ws))
            await c.execute('UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1',ws)
            team=uuid4()
            await c.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,'合成销售团队')",team,ws,'review_'+label)
            for code,role_code in [('ops','operations'),('sales','sales')]:
                uid=uuid4();users[label+'_'+code]=uid
                await c.execute('INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)',uid,ws,code+uuid4().hex[:8])
                await c.execute('INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,$3,$4)',ws,uid,role_code,'workspace' if code=='ops' else 'self')
                if code=='sales':
                    await c.execute("UPDATE platform.role_binding SET team_id=$2 WHERE user_ref_id=$1",uid,team)
                    await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')",ws,uid,team)
                actors[label+'_'+code]=ActorContext(workspace_id=str(ws),user_id=str(uid),role=role_code,data_scope='workspace' if code=='ops' else 'self',team_ids=[str(team)] if code=='sales' else [])
        anchor=await c.fetchval("SELECT timezone('Asia/Shanghai',clock_timestamp())::date")
        start=date(anchor.year,(anchor.month-1)//3*3+1,1)
        prior=await c.fetchval("""INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,user_ref_id,kind,amount,updated_by_user_ref_id,period_type,period_start,period_end)
        VALUES($1,$2,'person',$3,'recognized',123.45,$4,'quarter',$5,($5::date+interval '3 months - 1 day')::date) RETURNING to_jsonb(sales_target)""",workspaces['own'],start.year,users['own_sales'],users['own_ops'],start)
        sql=ROOT/'database/migrations/V240__review_metric_dictionary.sql'
        await apply(c,item('V240',sql),ROOT/'database')
        assert prior==await c.fetchval("SELECT to_jsonb(t) FROM crm.sales_target t WHERE kind='recognized'")
        checks.append('V240 preserves all previous target fields and amounts')
        new_ledger={r['migration_key']:r['checksum_sha256'] for r in await c.fetch('SELECT * FROM ops.migration_checksum')}
        assert all(new_ledger[k]==v for k,v in old_ledger.items()) and set(new_ledger)-set(old_ledger)=={'V240'}
        policies=[dict(r) for r in await c.fetch("SELECT schemaname,tablename,policyname,permissive,roles,cmd,qual,with_check FROM pg_policies ORDER BY 1,2,3")]
        assert [r for r in policies if r['tablename'] not in ('review_metric','review_metric_history')]==old_rls
        checks.append('old ledger and RLS policies unchanged; only V240 and two new tenant policies')
        rls_after=await rls_contract(c)
        samples={}
        for typ in ('json','jsonb'):await c.set_type_codec(typ,schema='pg_catalog',encoder=json.dumps,decoder=json.loads,format='text')
        await c.execute('SET row_security=on')
        active=actors['own_ops']
        class DB:
            @asynccontextmanager
            async def transaction(self, actor, readonly=False):
                async with c.transaction(readonly=readonly):
                    await c.execute(f'SET LOCAL ROLE "{role}"')
                    await set_request_context(c,actor)
                    assert not await c.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
                    yield c
        dbobj=DB();app=FastAPI();app.include_router(router)
        app.dependency_overrides[get_database]=lambda:dbobj
        app.dependency_overrides[get_identity]=lambda:SimpleNamespace(actor=active)
        @app.exception_handler(OperationsError)
        async def operation_error(request,exc):return JSONResponse({'detail':str(exc)},status_code=exc.status)
        @app.exception_handler(IdempotencyConflict)
        async def idem_error(request,exc):return JSONResponse({'detail':str(exc)},status_code=409)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic') as client:
            async def post(path,body,status=200,key=None):
                r=await client.post(path,json=body,headers={'Idempotency-Key':key} if key else {})
                assert r.status_code==status,(path,r.status_code,r.text)
                return r.json()
            metrics=(await client.get('/api/v1/review/metrics')).json()['items']
            assert len(metrics)==10 and {m['workspace_id'] for m in metrics}=={str(workspaces['own'])}
            mapping={m['code']:m for m in metrics}
            samples['metrics']={'items':metrics,'data_source':'database'}
            options=await client.get('/api/v1/review/targets/options')
            assert options.status_code==200,options.text
            samples['options']=options.json()
            assert mapping['forecast_recognized']['target_kind']=='forecast_recognized' and mapping['recognized']['target_kind']=='recognized'
            checks.append('tenant dictionary and forecast gross target separated from legacy recognized')
            custom=await post('/api/v1/review/metrics',{'code':'custom_calls','name':'有效沟通','unit':'次','decimal_places':0,'definition':'已确认的沟通次数'})
            assert custom['source_kind']=='manual'
            body=dict(scope='person',user_id=str(users['own_sales']),period_type='quarter',anchor_date=str(anchor),kind='collection',amount='9999999999999999.99',reason='合成精度验证')
            key=str(uuid4());saved=await post('/api/v1/review/targets',body,key=key)
            assert saved['target']['amount']=='9999999999999999.99'
            assert await post('/api/v1/review/targets',body,key=key)==saved
            await post('/api/v1/review/targets',{**body,'amount':'123.45'},409,key=key)
            await post('/api/v1/review/targets',body,409)
            checks.append('exact decimal cents, idempotent replay, payload conflict and duplicate identity rejected')
            for bad in ('0','-1','NaN','1.001'):
                await post('/api/v1/review/targets',{**body,'kind':'custom_calls','amount':bad},422)
            await post('/api/v1/review/targets',{**body,'kind':'custom_calls','amount':'1.5'},422)
            active=actors['own_sales']
            await post('/api/v1/review/metrics',{'code':'custom_denied','name':'越权','unit':'次','decimal_places':0,'definition':'不应写入'},403)
            body.update(amount='2000.25',version_no=saved['target']['version_no'])
            pending=await post('/api/v1/review/targets',body)
            assert pending['status']=='pending' and pending['target']['amount']=='9999999999999999.99'
            rows=(await client.get('/api/v1/review/targets',params={'scope':'self','anchor_date':str(anchor)})).json()
            row=next(r for r in rows['items'] if r['kind']=='collection')
            assert row['effective_amount']=='9999999999999999.99' and row['pending_change']['proposed_amount']=='2000.25'
            await post('/api/v1/review/targets',{**body,'amount':'3000.25'},409)
            checks.append('ordinary actor pending amount separate from effective; conflicting pending rejected')
            active=actors['own_ops']
            async with dbobj.transaction(active) as cc:
                approved=await TargetRepository().decide_batch(cc,pending['request']['id'],TargetDecision(decision='approved'))
            assert approved['status']=='approved'
            await post('/api/v1/review/targets',body,409)
            rows=(await client.get('/api/v1/review/targets',params={'scope':'person','user_id':str(users['own_sales']),'anchor_date':str(anchor)})).json()
            row=next(r for r in rows['items'] if r['kind']=='collection')
            assert row['effective_amount']=='2000.25' and row['pending_change'] is None
            checks.append('original approval commits same target and invalidates old version')
            active=actors['own_sales']
            async with dbobj.transaction(active) as cc:
                # Mixed original batches can contain an existing change plus a first target.
                from sales_backend.contracts.targets import TargetBatchSave
                from sales_backend.services.targets import save_target_batch
                mixed=await save_target_batch(cc,active,TargetBatchSave(scope='self',anchor_date=anchor,reason='混合待审',items=[{'kind':'collection','amount':'3000','version_no':row['version_no']},{'kind':'demo_count','amount':'3'}]))
                assert mixed['status']=='pending'
            rows=(await client.get('/api/v1/review/targets',params={'scope':'self','anchor_date':str(anchor)})).json()
            new=next(r for r in rows['items'] if r['kind']=='demo_count')
            assert new['id'] is None and new['state']=='pending_new' and new['effective_amount'] is None and new['pending_change']['proposed_amount']=='3.00'
            samples['targets_mixed_pending']=rows
            samples['save_pending']=pending
            samples['save_effective']=saved
            checks.append('first pending row survives refresh in merged pagination without invented effective zero')
            forbidden=await client.get('/api/v1/review/targets',params={'scope':'person','user_id':str(users['foreign_sales'])})
            assert forbidden.status_code==403
            active=actors['foreign_ops']
            assert all(m['code']!='custom_calls' for m in (await client.get('/api/v1/review/metrics')).json()['items'])
            await post('/api/v1/review/targets',{**body,'version_no':None},403)
            checks.append('cross tenant target and dictionary access denied')
            active=actors['own_ops']
            r=await client.patch('/api/v1/review/metrics/custom_calls',json={'active':False,'version_no':1});assert r.status_code==200,r.text
            r=await client.patch('/api/v1/review/metrics/custom_calls',json={'active':True,'version_no':1});assert r.status_code==409,r.text
            await post('/api/v1/review/targets',{**body,'kind':'custom_calls','version_no':None,'amount':'2'},422)
            async with dbobj.transaction(active) as cc:
                assert not await cc.fetchval("SELECT has_table_privilege(current_user,'crm.review_metric','UPDATE')")
                assert not await cc.fetchval("SELECT has_function_privilege(current_user,'security.seed_review_metrics(uuid)','EXECUTE')")
            assert await c.fetchval("SELECT count(*) FROM crm.review_metric_history WHERE workspace_id=$1 AND code='custom_calls'",workspaces['own'])==2
            checks.append('dictionary optimistic state/history and no direct mutation/seed grants')
            active=actors['own_sales']
            async with dbobj.transaction(active) as cc:
                legacy=await legacy_save(cc,active,TargetSave(scope='self',anchor_date=anchor,period_type='quarter',kind='visit_count',amount='2',reason='旧Mini无新字段'))
                assert legacy['status']=='effective'
            checks.append('legacy Mini TargetSave unchanged and old save path operational')
        result={'status':'passed_isolated_postgres_and_asgi','checks':checks,'check_count':len(checks),'sql_sha256':hashlib.sha256(sql.read_bytes()).hexdigest(),'ledger_before':old_ledger,'ledger_after':new_ledger,'ledger_before_sha256':digest(old_ledger),'ledger_after_sha256':digest(new_ledger),'policies':policies,'registered_versions':[r['version'] for r in await c.fetch('SELECT version FROM ops.schema_migration ORDER BY version')],'scope':'synthetic private socket database only; ASGI dependency supplies trusted actor; database RLS and scoped functions are real','cleanup':'own database and role dropped in finally'}
        result.update(rls_before=rls_before,rls_after=rls_after,
                      rls_before_sha256=digest(rls_before),rls_after_sha256=digest(rls_after),
                      response_samples=samples,samples_are_synthetic=True)
        Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'status':result['status'],'check_count':len(checks),'output':args.output,'ledger_after_sha256':result['ledger_after_sha256']},ensure_ascii=False))
    finally:
        if c:await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--socket',required=True);p.add_argument('--port',type=int,required=True);p.add_argument('--output',required=True)
    asyncio.run(verify(p.parse_args()))
