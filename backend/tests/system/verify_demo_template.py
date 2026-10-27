"""Exercise installed demo tooling and real business HTTP with synthetic tenants."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import uuid
from datetime import datetime

import asyncpg
import httpx

from instance_files import read_private


def invoke(root, action, workspace, company, *, expect=True):
    env={k:os.environ[k] for k in ('PATH','HOME','LANG','LC_ALL','TMPDIR','SALESBUDDY_INSTALL_DB_PASSWORD') if k in os.environ}
    env['PYTHONPYCACHEPREFIX']=str(root/'cache/pycache')
    command=[str(root/'runtime/bin/python'),str(root/'release/tools/installation/demo_template.py'),action,
             '--root',str(root),'--workspace',str(workspace),'--company-code',company]
    if action=='apply': command+=['--reason','Synthetic demo installation acceptance']
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=120)
    fd=os.open(root.parent/'demo-driver.log',os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    with os.fdopen(fd,'a') as f:f.write(action+' exit='+str(result.returncode)+'\n'+result.stdout+result.stderr+'\n')
    if not expect:
        assert result.returncode,'Unsafe template operation unexpectedly accepted'
        return result.stderr
    assert result.returncode==0,'Demo template command failed; private fixture: '+str(root)
    return json.loads(result.stdout)


async def exercise(root, conn, client, origin, command, args, original_customers):
    data=read_private(root/'INSTANCE.json')
    async def connect():
        return await asyncpg.connect(host='127.0.0.1',port=args.port,user=args.user,database=data['database']['name'],
            password=os.environ.get('SALESBUDDY_INSTALL_DB_PASSWORD'),command_timeout=300)
    demos=[]
    for suffix in ('a','b'):
        code='synthetic-demo-'+suffix
        r=await client.post('/api/v1/platform/tenants',headers={'Idempotency-Key':str(uuid.uuid4())},json={
            'company_code':code,'name':'Synthetic demo '+suffix,'purpose':'demo',
            'administrator_account':'DEMOADMIN','administrator_name':'Synthetic demo administrator'})
        assert r.status_code==201,r.status_code
        tenant=r.json();ws=uuid.UUID(tenant['id']);demos.append((ws,code))
        r=await client.post('/api/v1/platform/activate',json={'company_code':code,
            'activation_token':tenant['activation_token'],'new_password':secrets.token_urlsafe(32)})
        assert r.status_code==200
        row=next(t for t in (await client.get('/api/v1/platform/tenants')).json()['items'] if t['id']==str(ws))
        assert (await client.post('/api/v1/platform/tenants/'+str(ws)+'/state',json={
            'version_no':row['version_no'],'status':'active','reason':'Synthetic template test'})).status_code==200
    invoke(root,'plan',*demos[0],expect=False) # owned services still running
    command(root,'stop','--root',str(root))
    invoke(root,'plan',*demos[0],expect=False) # independent maintenance connection
    admin_hashes=dict(await conn.fetch('SELECT user_ref_id,password_hash FROM platform.password_credential WHERE workspace_id=ANY($1::uuid[])',[ws for ws,_ in demos]))
    await conn.close()
    invoke(root,'plan',uuid.UUID(next(iter(original_customers))),'installer-a',expect=False)
    invoke(root,'plan',demos[0][0],'wrong-company-code',expect=False)
    preview=invoke(root,'plan',*demos[0]);assert preview['status']=='ready' and preview['database_modified'] is False
    # A retained/soft-deleted business record still makes a company non-empty.
    conn=await connect()
    retained=await conn.fetchval("INSERT INTO crm.customer(workspace_id,name,normalized_name,deleted_at,created_by_user_ref_id) VALUES($1,'Synthetic retained history','synthetic retained history',clock_timestamp(),(SELECT administrator_id FROM control.tenant_setup WHERE workspace_id=$1)) RETURNING id",demos[0][0])
    await conn.close()
    assert 'already contains business data or history' in invoke(root,'plan',*demos[0],expect=False)
    conn=await connect()
    await conn.execute('DELETE FROM crm.customer_ownership WHERE customer_id=$1',retained)
    await conn.execute('DELETE FROM crm.customer WHERE id=$1',retained)
    await conn.execute("UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code='task'",demos[0][0])
    await conn.close()
    assert 'requires enabled business modules: task' in invoke(root,'plan',*demos[0],expect=False)
    conn=await connect()
    await conn.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1 AND module_code='task'",demos[0][0])
    await conn.close()
    conn=await connect()
    await conn.execute('UPDATE control.tenant_account_quota SET account_limit=2 WHERE workspace_id=$1',demos[0][0])
    await conn.close();invoke(root,'apply',*demos[0],expect=False)
    conn=await connect()
    assert await conn.fetchval('SELECT count(*) FROM platform.user_ref WHERE workspace_id=$1',demos[0][0])==1
    await conn.execute('UPDATE control.tenant_account_quota SET account_limit=NULL WHERE workspace_id=$1',demos[0][0])
    # Trigger exists only in the disposable fixture. It proves account/team/
    # customer writes before a midway failure roll back, including quota counts.
    await conn.execute("""CREATE FUNCTION public.demo_fail_probe() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.name LIKE '%2' THEN RAISE EXCEPTION 'SYNTHETIC_DEMO_MIDWAY_FAILURE' USING ERRCODE='DM001'; END IF; RETURN NEW; END $$;
        CREATE TRIGGER demo_fail_probe BEFORE INSERT ON crm.opportunity FOR EACH ROW EXECUTE FUNCTION public.demo_fail_probe()""")
    await conn.close()
    assert 'sqlstate=DM001' in invoke(root,'apply',*demos[0],expect=False), 'Failure did not reach the midway rollback probe'
    conn=await connect()
    assert await conn.fetchval('SELECT count(*) FROM platform.user_ref WHERE workspace_id=$1',demos[0][0])==1
    assert await conn.fetchval('SELECT active_accounts FROM control.tenant_account_quota WHERE workspace_id=$1',demos[0][0])==1
    assert await conn.fetchval('SELECT count(*) FROM crm.customer WHERE workspace_id=$1',demos[0][0])==0
    assert await conn.fetchval('SELECT count(*) FROM control.demo_template_receipt')==0
    await conn.execute('DROP TRIGGER demo_fail_probe ON crm.opportunity; DROP FUNCTION public.demo_fail_probe()')
    await conn.close()
    receipts=[]
    for ws,code in demos:
        result=invoke(root,'apply',ws,code);assert result['status']=='initialized'
        receipts.append(read_private(Path(result['onboarding_credentials_file'])))
        repeated=invoke(root,'apply',ws,code)
        assert repeated['status']=='already_initialized' and not repeated['credentials_reset'] and not repeated['business_rows_changed']
    conn=await connect()
    try:
        assert admin_hashes==dict(await conn.fetch('SELECT user_ref_id,password_hash FROM platform.password_credential WHERE user_ref_id=ANY($1::uuid[])',list(admin_hashes)))
        for ws,code in demos:
            assert await conn.fetchval('SELECT count(*) FROM crm.customer WHERE workspace_id=$1',ws)==4
            assert await conn.fetchval('SELECT active_accounts FROM control.tenant_account_quota WHERE workspace_id=$1',ws)==5
            assert not await conn.fetchval("SELECT enabled FROM control.tenant_module WHERE workspace_id=$1 AND module_code='feishu'",ws)
        assert set(str(r['id']) for r in await conn.fetch('SELECT id FROM crm.customer WHERE workspace_id=ANY($1::uuid[])',[uuid.UUID(w) for w in original_customers]))==set(original_customers.values())
        assert await conn.fetchval('SELECT count(*) FROM control.demo_template_receipt')==2
        assert await conn.fetchval('SELECT count(*) FROM ops.job WHERE workspace_id=ANY($1::uuid[])',[ws for ws,_ in demos])==0
        # Runtime roles cannot read or forge the maintenance-only receipt.
        for role in data['roles'].values():
            assert not await conn.fetchval("SELECT has_table_privilege($1,'control.demo_template_receipt','SELECT,INSERT,UPDATE,DELETE')",role)
    finally: await conn.close()
    command(root,'start','--root',str(root),'--feishu')
    login_count=0;business_checks=[];login_bodies=[]
    async with httpx.AsyncClient(base_url=origin,trust_env=False,timeout=30) as business:
        for (ws,code),receipt in zip(demos,receipts):
            sessions={}
            for account in receipt['accounts']:
                role='manager' if account['account_code']=='DEMO_MANAGER' else 'fde' if account['account_code']=='DEMO_FDE' else 'sales'
                body={'workspace':code,'account_code':account['account_code'],'password':account['password'],'role':role}
                r=await business.post('/api/v1/auth/password/login',json=body);assert r.status_code==200,(code,role,r.status_code)
                session=r.json();assert session['must_change_password']
                headers={'Authorization':'Bearer '+session['access_token']}
                assert (await business.get('/api/v1/customer-assets/map',headers=headers)).status_code==403
                new_password=secrets.token_urlsafe(32)
                assert (await business.post('/api/v1/auth/password',headers=headers,json={'old_password':account['password'],'new_password':new_password})).status_code==200
                r=await business.post('/api/v1/auth/password/login',json={**body,'password':new_password});assert r.status_code==200
                assert r.json()['must_change_password'] is False
                login_bodies.append({**body,'password':new_password})
                sessions[account['account_code']]={'Authorization':'Bearer '+r.json()['access_token']};login_count+=1
            sales=sessions['DEMO_SALES_A'];manager=sessions['DEMO_MANAGER'];fde=sessions['DEMO_FDE']
            r=await business.get('/api/v1/customer-assets/map',headers=manager);assert r.status_code==200,r.status_code
            items=r.json()['items'];assert len(items)==4,(code,'manager map count',len(items))
            assert {v['quadrant_code'] for v in items}=={'main_attack','customer_asset','order_driven','customer_resource'},[(v['name'],v['quadrant_code']) for v in items]
            for h,expected in ((sales,2),(fde,4)):
                r=await business.get('/api/v1/customer-assets/map',headers=h);assert r.status_code==200
                assert len(r.json()['items'])==expected,(code,'role map count',len(r.json()['items']))
            r=await business.get('/api/v1/tasks',headers=sales);assert r.status_code==200
            tasks=r.json()['items'];assert len(tasks)==3,(code,'sales task count',len(tasks))
            assert {t['association_kind'] for t in tasks}=={'customer','daily'}
            daily=next(t for t in tasks if t['association_kind']=='daily')
            accepted=await business.post('/api/v1/tasks/'+daily['id']+'/events',headers={**sales,'Idempotency-Key':str(uuid.uuid4())},
                json={'event_type':'accept','version_no':daily['version_no']})
            assert accepted.status_code==200,('accept task',accepted.status_code)
            now=datetime.now()
            r=await business.get('/api/v1/dashboard/rankings',headers=manager,params={'year':now.year,'quarters':(now.month-1)//3+1})
            assert r.status_code==200,('rankings',r.status_code)
            business_checks.append({'company':code,'manager_map':4,'sales_map':2,'fde_assigned_map':4,'sales_tasks':3,'accept_task':True,'rankings':True})
    # Changed data and changed passwords survive later replays; no reset action.
    command(root,'stop','--root',str(root))
    conn=await connect()
    changed_passwords=dict(await conn.fetch('SELECT user_ref_id,password_hash FROM platform.password_credential WHERE workspace_id=$1',demos[0][0]))
    changed_tasks=[dict(r) for r in await conn.fetch('SELECT id,status,version_no FROM workflow.task WHERE workspace_id=$1 ORDER BY id',demos[0][0])]
    await conn.close()
    repeated=invoke(root,'apply',*demos[0]);assert repeated['status']=='already_initialized'
    conn=await connect()
    assert changed_passwords==dict(await conn.fetch('SELECT user_ref_id,password_hash FROM platform.password_credential WHERE workspace_id=$1',demos[0][0]))
    assert changed_tasks==[dict(r) for r in await conn.fetch('SELECT id,status,version_no FROM workflow.task WHERE workspace_id=$1 ORDER BY id',demos[0][0])]
    await conn.close()
    reset_result=None
    if args.demo_reset_pg_bin:
        from verify_demo_reset import exercise as reset_exercise
        reset_result=await reset_exercise(root,connect,command,args,demos,login_bodies,origin)
    command(root,'start','--root',str(root),'--feishu')
    return {'demo_business_reset':reset_result,'status':'passed','synthetic_tenants':2,'new_accounts':8,'real_logins_and_first_password_changes':login_count,
            'purpose_and_company_code_refused':True,'live_process_and_connection_refused':True,'quota_refused':True,
            'retained_history_and_disabled_module_refused':True,'modified_passwords_and_tasks_preserved':True,
            'midway_rollback':True,'idempotent_replay_no_password_reset':True,'initial_admins_preserved':True,
            'unrelated_tenants_preserved':True,'maintenance_receipts_private':True,'no_external_jobs_generated':True,
            'business_http':business_checks,'production_changed':False}
