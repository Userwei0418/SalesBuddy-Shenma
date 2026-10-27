"""Synthetic installed-instance reset acceptance, including real backup and CLI."""
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

import httpx

from demo_business import identity
from instance_backup import inventory
from demo_reset_catalog import sha,structure
from instance_files import atomic_json


def invoke(root,action,ws,code,version,*extra,expect=True,wrapper=None,expected_exit=None):
    env={k:os.environ[k] for k in ('PATH','HOME','LANG','LC_ALL','TMPDIR','SALESBUDDY_INSTALL_DB_PASSWORD') if k in os.environ}
    env['PYTHONDONTWRITEBYTECODE']='1'
    script=root/'release/tools/installation/demo_template.py'
    command=[str(root/'runtime/bin/python'),str(wrapper or script),action,'--root',str(root),
             '--workspace',str(ws),'--company-code',code,'--version',str(version),*map(str,extra)]
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=180)
    fd=os.open(root.parent/'demo-reset-driver.log',os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    with os.fdopen(fd,'a') as f:f.write(action+' exit='+str(result.returncode)+'\n'+result.stdout+result.stderr+'\n')
    if not expect:
        if expected_exit is not None: assert result.returncode==expected_exit,'Expected crash phase was not reached'
        assert result.returncode,'Unsafe reset accepted';return result.stderr
    # The container may be removed after CI. Keep only the safe gate/table name
    # in its public result; SQL details, credentials and snapshots stay private.
    gate=re.search(r'Unfinished external/async work must be reconciled before reset: ([a-z_]+\.[a-z_]+)',result.stderr)
    summary='; unfinished='+gate.group(1) if gate else ''
    assert result.returncode==0,'Demo reset failed'+summary+'; private log: '+str(root.parent/'demo-reset-driver.log')
    return json.loads(result.stdout)


def backup(root,pg_bin):
    env={k:os.environ[k] for k in ('PATH','HOME','LANG','LC_ALL','TMPDIR','SALESBUDDY_INSTALL_DB_PASSWORD') if k in os.environ}
    result=subprocess.run([str(root/'runtime/bin/python'),str(root/'release/tools/installation/instance_backup.py'),
       'backup','--root',str(root),'--pg-bin',str(pg_bin)],env=env,capture_output=True,text=True,timeout=180)
    assert result.returncode==0,'Synthetic reset backup failed'
    directory=Path(json.loads(result.stdout)['backup'])
    return directory,hashlib.sha256((directory/'BACKUP.json').read_bytes()).hexdigest()


async def history_fixture(conn,workspace,admin):
    """Immutable business history, imported task sources and the real task cycle."""
    await conn.execute("SELECT set_config('app.workspace_id',$1,true)",str(workspace))
    await conn.execute("SELECT set_config('app.user_ref_id',$1,true)",str(admin))
    await conn.execute("SELECT set_config('app.role_code','administrator',true)")
    customer=await conn.fetchval('SELECT id FROM crm.customer WHERE workspace_id=$1 ORDER BY id LIMIT 1',workspace)
    opportunity=await conn.fetchval('SELECT id FROM crm.opportunity WHERE workspace_id=$1 AND customer_id=$2 LIMIT 1',workspace,customer)
    task=await conn.fetchval('SELECT id FROM workflow.task WHERE workspace_id=$1 AND customer_id=$2 LIMIT 1',workspace,customer)
    await conn.execute("INSERT INTO crm.customer_ownership_event(workspace_id,customer_id,event_type,actor_user_ref_id,reason) VALUES($1,$2,'legacy_resolved',$3,'Synthetic reset history')",workspace,customer,admin)
    target=await conn.fetchval("""INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,user_ref_id,kind,amount,
      period_type,period_start,period_end,updated_by_user_ref_id) VALUES($1,2026,'person',$2,'acv',100,'year','2026-01-01','2026-12-31',$2) RETURNING id""",workspace,admin)
    await conn.execute('''INSERT INTO crm.sales_target_change_request(workspace_id,target_id,applicant_user_ref_id,base_version,
      previous_amount,proposed_amount) VALUES($1,$2,$3,1,100,200)''',workspace,target,admin)
    await conn.execute("""INSERT INTO crm.sales_target_batch_request(workspace_id,scope_type,user_ref_id,period_type,period_start,
      period_end,applicant_user_ref_id,reason,items) VALUES($1,'person',$2,'year','2026-01-01','2026-12-31',$2,'Synthetic reset','[{}]')""",workspace,admin)
    batch=await conn.fetchval("""INSERT INTO ops.crm_import_batch(workspace_id,source_system,source_base_id,manifest_sha256,status,
      source_snapshot_at,created_by_user_ref_id) VALUES($1,'synthetic','demo-reset',$2,'applied',clock_timestamp(),$3) RETURNING id""",workspace,'a'*64,admin)
    record=await conn.fetchval("""INSERT INTO ops.crm_import_record(workspace_id,batch_id,source_table_id,source_record_id,object_kind,
      source_sha256,raw_fields,status,target_id,applied_at) VALUES($1,$2,'synthetic','synthetic','period_actual_snapshot',$3,'{}','applied',$4,clock_timestamp()) RETURNING id""",workspace,batch,'a'*64,uuid.uuid4())
    await conn.execute("""INSERT INTO crm.opportunity_period_actual_snapshot(workspace_id,opportunity_id,year,quarter,kind,source_field,
      raw_amount,source_unit,source_record_id,import_batch_id) VALUES($1,$2,2026,1,'recognized','synthetic',1,'cny',$3,$4)""",workspace,opportunity,record,batch)
    advice=await conn.fetchval("""INSERT INTO insight.business_advice(workspace_id,actor_user_ref_id,actor_role_code,subject_kind,
      subject_id,customer_id,section,cache_key,facts_fingerprint,configuration_fingerprint,identity_snapshot,facts_snapshot,
      configuration_snapshot,status) VALUES($1,$2,'administrator','customer',$3,$3,'tasks',$4,$4,$4,'{}','{}','{}','succeeded') RETURNING id""",workspace,admin,customer,'a'*64)
    suggestion=await conn.fetchval("""INSERT INTO insight.business_suggestion(workspace_id,advice_id,ordinal,title,evidence,action,
      evidence_refs,decision,decided_by_user_ref_id,decided_at,task_id) VALUES($1,$2,1,'Synthetic','Synthetic','Synthetic','[]','adopted',$3,clock_timestamp(),$4) RETURNING id""",workspace,advice,admin,task)
    await conn.execute('UPDATE workflow.task SET source_suggestion_id=$1 WHERE id=$2',suggestion,task)
    # A confirmed import and an unconfirmed draft both belong to reset business.
    # The generic reset must remove these before recreating ordinary demo tasks,
    # retaining their hashed audit evidence and every other tenant's sources.
    manager=identity(workspace,'user:DEMO_MANAGER');seller=identity(workspace,'user:DEMO_SALES_A')
    team=identity(workspace,'team:east');import_task=uuid.uuid4();item=uuid.uuid4()
    await conn.execute("SELECT set_config('app.user_ref_id',$1,true)",str(manager))
    await conn.execute("SELECT set_config('app.role_code','manager',true)")
    source='Synthetic meeting task source'
    item_body={'item_id':str(item),'source_line':1,'description':source}
    import_batch=await conn.fetchval('''INSERT INTO workflow.task_import_batch(workspace_id,creator_user_ref_id,
      batch_name,meeting_date,source_text,source_hash,parse_method,items) VALUES($1,$2,'Synthetic meeting',
      current_date,$3,$4,'rules',$5::jsonb) RETURNING id''',workspace,manager,source,
      hashlib.sha256(source.encode()).hexdigest(),json.dumps([item_body]))
    await conn.execute('''INSERT INTO workflow.task(id,workspace_id,title,description,creator_user_ref_id,
      creator_team_id,due_at,association_kind,status,source_code) VALUES($1,$2,$3,$3,$4,$5,
      clock_timestamp()+interval '1 day','daily','pending_confirm','meeting_import')''',
      import_task,workspace,source,manager,team)
    await conn.execute('''INSERT INTO workflow.task_assignee(workspace_id,task_id,assignee_user_ref_id,
      assignee_team_id,assignee_role) VALUES($1,$2,$3,$4,'sales')''',workspace,import_task,seller,team)
    await conn.execute('''INSERT INTO workflow.task_import_item(batch_id,item_id,workspace_id,task_id,
      source_line,original_assignee_user_ref_id,confirmed_item) VALUES($1,$2,$3,$4,1,$5,$6::jsonb)''',
      import_batch,item,workspace,import_task,seller,json.dumps(item_body))
    await conn.execute('''UPDATE workflow.task_import_batch SET status='confirmed',version_no=2,
      confirmed_at=clock_timestamp(),confirmed_payload_hash=$2 WHERE id=$1''',import_batch,'b'*64)
    draft_source='Synthetic untouched meeting draft'
    await conn.execute('''INSERT INTO workflow.task_import_batch(workspace_id,creator_user_ref_id,batch_name,
      meeting_date,source_text,source_hash,parse_method,items) VALUES($1,$2,'Synthetic draft',
      current_date,$3,$4,'rules',$5::jsonb)''',workspace,manager,draft_source,
      hashlib.sha256(draft_source.encode()).hexdigest(),json.dumps([item_body]))
    await conn.execute("SELECT set_config('app.user_ref_id',$1,true)",str(admin))
    await conn.execute("SELECT set_config('app.role_code','administrator',true)")
    original=uuid.uuid4();body=b'Synthetic original retained through demo reset';digest=hashlib.sha256(body).hexdigest()
    await conn.execute("""INSERT INTO activity.visit_import(id,workspace_id,created_by_user_ref_id,filename,file_path,file_size,status,
      storage_driver,storage_profile,storage_key,content_sha256) VALUES($1,$2,$3,'synthetic.txt','postgres',$4,'succeeded','postgres','database',$5,$6)""",original,workspace,admin,len(body),str(original),digest)
    await conn.execute('INSERT INTO activity.visit_import_content(import_id,workspace_id,chunk_no,content) VALUES($1,$2,0,$3)',original,workspace,body)
    await conn.execute("INSERT INTO control.call_event(workspace_id,resource,operation_code,event_id,actor_id,state) VALUES($1,'ai_calls','synthetic-history',$2,$3,'used')",workspace,uuid.uuid4(),admin)
    connection=uuid.uuid4()
    await conn.execute('''INSERT INTO config.feishu_connection(id,workspace_id,revision,enabled,settings,updated_by)
      VALUES($1,$2,1,false,$3::jsonb,$4)''',connection,workspace,json.dumps({'workspace_id':str(workspace),'connection_id':str(connection),'synthetic':True}),admin)
    return original


async def exercise(root,connect,command,args,demos,login_bodies,origin):
    ws,code=demos[0];conn=await connect()
    try:
        async with conn.transaction():
            admin=await conn.fetchval('SELECT administrator_id FROM control.tenant_setup WHERE workspace_id=$1',ws)
            await history_fixture(conn,ws,admin)
            # V180 sessions are security evidence, outside the business reset
            # catalog. Use an expired, revoked synthetic capability; never issue
            # an active export credential or fabricate a completed export.
            export_session=await conn.fetchval('''INSERT INTO security.closed_export_session(
              workspace_id,user_id,token_hash,credential_fingerprint,created_at,expires_at,revoked_at)
              VALUES($1,$2,$3,$4,clock_timestamp()-interval '20 minutes',
                clock_timestamp()-interval '10 minutes',clock_timestamp()) RETURNING id''',
              ws,admin,hashlib.sha256(uuid.uuid4().bytes).hexdigest(),'0'*64)
            await conn.execute('''INSERT INTO security.closed_export_event(session_id,workspace_id,user_id,action,detail)
              VALUES($1,$2,$3,'logout','{"synthetic":true}')''',export_session,ws,admin)
        export_security={table:[dict(r) for r in await conn.fetch('SELECT * FROM '+table+' WHERE workspace_id=$1 ORDER BY id',ws)]
                         for table in ('security.closed_export_session','security.closed_export_event')}
        version=await conn.fetchval('SELECT version_no FROM platform.workspace WHERE id=$1',ws)
        form=await conn.fetchval('SELECT v.id FROM config.form_version v JOIN config.form_definition d ON d.id=v.form_definition_id WHERE d.workspace_id=$1',ws)
        mapping={'administrator':str(admin),'form_version':str(form),'accounts':{key:str(identity(ws,'user:'+key)) for key in
            ('DEMO_SALES_A','DEMO_SALES_B','DEMO_MANAGER','DEMO_FDE')},'teams':{key:str(identity(ws,'team:'+key)) for key in ('east','west','fde')}}
        other_company=await conn.fetchrow("SELECT w.id,w.external_workspace_id,w.version_no,s.administrator_id FROM platform.workspace w JOIN control.tenant_setup s ON s.workspace_id=w.id WHERE s.purpose='customer' ORDER BY w.id LIMIT 1")
        old_ids={r['id'] for r in await conn.fetch('SELECT id FROM crm.customer WHERE workspace_id=$1',ws)}
        old_passwords=dict(await conn.fetch('SELECT user_ref_id,password_hash FROM platform.password_credential WHERE workspace_id=$1',ws))
    finally:await conn.close()
    operation=uuid.uuid4();mapping_file=root/'private/reset-mapping.json';atomic_json(mapping_file,mapping)
    common=['--mapping',mapping_file,'--operation',operation,'--as-of',date.today().isoformat()]
    assert 'registration' in invoke(root,'plan-reset',ws,code,version,*common,expect=False)
    invoke(root,'register-demo',ws,code,version,'--administrator',admin,'--reason','Synthetic existing demo registration')
    assert 'cannot' in invoke(root,'register-demo',other_company['id'],other_company['external_workspace_id'],other_company['version_no'],
        '--administrator',other_company['administrator_id'],'--reason','Must reject customer purpose',expect=False)
    assert 'Exact' in invoke(root,'plan-reset',ws,code,version+1,*common,expect=False)
    assert 'Exact' in invoke(root,'plan-reset',ws,'wrong-company-code',version,*common,expect=False)
    wrong_mapping=json.loads(json.dumps(mapping));wrong_mapping['accounts']['DEMO_SALES_A']=str(identity(demos[1][0],'user:DEMO_SALES_A'))
    atomic_json(mapping_file,wrong_mapping)
    assert 'same-company' in invoke(root,'plan-reset',ws,code,version,*common,expect=False)
    atomic_json(mapping_file,mapping)
    # Read-only plan also refuses a live independent connection.
    conn=await connect()
    assert 'connections' in invoke(root,'plan-reset',ws,code,version,*common,expect=False)
    await conn.close()
    conn=await connect()
    seller=uuid.UUID(mapping['accounts']['DEMO_SALES_A'])
    await conn.execute('INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2) ON CONFLICT DO NOTHING',ws,seller)
    await conn.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,'opportunity.create','deny')",ws,seller)
    await conn.close()
    assert 'effective permissions' in invoke(root,'plan-reset',ws,code,version,*common,expect=False)
    conn=await connect()
    await conn.execute("DELETE FROM config.account_permission_override WHERE workspace_id=$1 AND user_ref_id=$2 AND permission_code='opportunity.create'",ws,seller)
    # The real worker's periodic maintenance enqueues daily competency reviews.
    # Ensure this fixture exercises that pending state even if the scheduler did
    # not run before shutdown. Models remain unconfigured; no provider is called.
    await conn.fetchval('SELECT ops.enqueue_daily_sales_competency_reviews()')
    job=await conn.fetchval("INSERT INTO ops.job(workspace_id,job_type,payload) VALUES($1,'synthetic-reset-pending','{}') RETURNING id",ws)
    await conn.close()
    assert 'before reset: ops.job' in invoke(root,'plan-reset',ws,code,version,*common,expect=False)
    conn=await connect();await conn.execute("UPDATE ops.job SET status='cancelled' WHERE id=$1",job);await conn.close()
    assert 'before reset: ops.job' in invoke(root,'plan-reset',ws,code,version,*common,expect=False)
    conn=await connect()
    async with conn.transaction():
        pending=await conn.fetch('''SELECT j.id,j.aggregate_id,j.aggregate_type,j.status,j.attempts,
          j.locked_by,j.locked_until,j.lease_token,r.status AS review_status,r.subject_user_ref_id
          FROM ops.job j LEFT JOIN insight.sales_competency_review r
          ON r.workspace_id=j.workspace_id AND r.id=j.aggregate_id
          WHERE j.workspace_id=$1 AND j.job_type='sales_competency.review' FOR UPDATE OF j''',ws)
        expected={uuid.UUID(mapping['accounts'][key]) for key in ('DEMO_SALES_A','DEMO_SALES_B')}
        assert pending and {r['subject_user_ref_id'] for r in pending}==expected
        for row in pending:
            assert (row['aggregate_type'],row['status'],row['attempts'],row['review_status'])==('sales_competency_review','queued',0,'queued')
            assert all(row[key] is None for key in ('locked_by','locked_until','lease_token'))
            assert not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM ops.job_effect WHERE job_id=$1)',row['id'])
            assert not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM agent.inference_operation WHERE job_id=$1)',row['id'])
            assert not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM control.call_event WHERE workspace_id=$1 AND event_id=$2)',ws,row['aggregate_id'])
            await conn.execute("""UPDATE ops.job SET status='cancelled',completed_at=clock_timestamp(),
              last_error_code='SYNTHETIC_FIXTURE_CANCELLED',updated_at=clock_timestamp() WHERE id=$1""",row['id'])
    await conn.close()
    # Cancelling jobs alone cannot pretend their queued business results ended.
    assert 'before reset: insight.sales_competency_review' in invoke(root,'plan-reset',ws,code,version,*common,expect=False)
    conn=await connect()
    async with conn.transaction():
        for row in pending:
            result=await conn.execute("""UPDATE insight.sales_competency_review SET status='failed',
              error_code='SYNTHETIC_FIXTURE_CANCELLED',error_detail='Never executed; isolated reset fixture only',
              updated_at=clock_timestamp() WHERE id=$1 AND workspace_id=$2 AND status='queued'""",row['aggregate_id'],ws)
            assert result=='UPDATE 1'
    await conn.close()
    plan=invoke(root,'plan-reset',ws,code,version,*common)
    assert all(plan['plan']['clear_counts'][name]>0 for name in ('crm.customer_ownership_event','crm.sales_target_change_request',
        'crm.sales_target_batch_request','crm.opportunity_period_actual_snapshot',
        'workflow.task_import_batch','workflow.task_import_item'))
    backup_dir,backup_sha=backup(root,args.demo_reset_pg_bin)
    apply_args=[*common,'--reason','Synthetic reset acceptance','--plan-sha256',plan['plan_sha256'],
        '--backup',backup_dir,'--backup-sha256',backup_sha]
    bad=[*apply_args];bad[-1]='0'*64
    assert 'Backup' in invoke(root,'apply-reset',ws,code,version,*bad,expect=False)
    # Unknown trigger is rejected by the exact dependency catalog before data work.
    conn=await connect()
    await conn.execute("CREATE FUNCTION public.reset_unreviewed() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$; CREATE TRIGGER reset_unreviewed BEFORE INSERT ON crm.customer FOR EACH ROW EXECUTE FUNCTION public.reset_unreviewed()")
    await conn.close()
    assert 'Unreviewed' in invoke(root,'plan-reset',ws,code,version,*common,expect=False)
    conn=await connect();await conn.execute('DROP TRIGGER reset_unreviewed ON crm.customer; DROP FUNCTION public.reset_unreviewed()');await conn.close()
    # os._exit closes the database connection without a Python finally block,
    # proving PostgreSQL rolls back temporary trigger DDL AND partial deletion.
    wrapper=root.parent/'crash-demo-reset.py'
    wrapper.write_text('import os,sys\nsys.path.insert(0,'+repr(str(root/'release/tools/installation'))+')\nimport demo_reset,demo_template\nasync def fail(*args,**kwargs): os._exit(91)\ndemo_reset.seed_business=fail\ndemo_template.main()\n')
    invoke(root,'apply-reset',ws,code,version,*apply_args,expect=False,wrapper=wrapper,expected_exit=91)
    conn=await connect()
    async with conn.transaction():
        assert await inventory(conn)==plan_inventory(backup_dir),'Crash altered database'
        assert sha(await structure(conn))==plan['plan']['catalog_sha256'],'Crash left trigger DDL changed'
    await conn.close()
    result=invoke(root,'apply-reset',ws,code,version,*apply_args);assert result['status']=='reset'
    assert invoke(root,'apply-reset',ws,code,version,*apply_args)['status']=='already_reset'
    altered=json.loads(json.dumps(mapping));altered['accounts']['DEMO_SALES_A'],altered['accounts']['DEMO_SALES_B']=altered['accounts']['DEMO_SALES_B'],altered['accounts']['DEMO_SALES_A']
    atomic_json(mapping_file,altered)
    assert 'different inputs' in invoke(root,'apply-reset',ws,code,version,*apply_args,expect=False)
    atomic_json(mapping_file,mapping)
    conn=await connect()
    try:
        assert old_passwords==dict(await conn.fetch('SELECT user_ref_id,password_hash FROM platform.password_credential WHERE workspace_id=$1',ws))
        assert old_ids.isdisjoint({r['id'] for r in await conn.fetch('SELECT id FROM crm.customer WHERE workspace_id=$1',ws)})
        assert await conn.fetchval('SELECT count(*) FROM ops.feishu_event WHERE workspace_id=$1',ws)==0
        assert await conn.fetchval('SELECT count(*) FROM control.demo_reset_receipt WHERE workspace_id=$1',ws)==1
        for table in ('workflow.task_import_batch','workflow.task_import_item'):
            assert await conn.fetchval('SELECT count(*) FROM '+table+' WHERE workspace_id=$1',ws)==0
        for table,rows in export_security.items():
            assert rows==[dict(r) for r in await conn.fetch('SELECT * FROM '+table+' WHERE workspace_id=$1 ORDER BY id',ws)]
        for role in json.loads((root/'INSTANCE.json').read_text())['roles'].values():
            for table in ('control.demo_reset_registration','control.demo_reset_receipt'):
                assert not await conn.fetchval('SELECT has_table_privilege($1,$2,\'SELECT,INSERT,UPDATE,DELETE\')',role,table)
    finally:await conn.close()
    command(root,'start','--root',str(root))
    async with httpx.AsyncClient(base_url=origin,trust_env=False,timeout=30) as client:
        for entry in login_bodies:
            response=await client.post('/api/v1/auth/password/login',json=entry);assert response.status_code==200
            headers={'Authorization':'Bearer '+response.json()['access_token']}
            response=await client.get('/api/v1/customer-assets/map',headers=headers);assert response.status_code==200
            assert len(response.json()['items'])==({'sales':2,'manager':4,'fde':4}[entry['role']])
    command(root,'stop','--root',str(root))
    return {'status':'passed','real_backup':True,'immutable_business_history_and_fk_cycle':True,'original_quota_and_usage_retained':True,
       'company_config_and_all_passwords_preserved':True,'crash_rollback_data_and_trigger_ddl':True,'foreign_tenant_preserved':True,
       'unreviewed_schema_and_changed_backup_refused':True,'exact_replay_idempotent':True,'all_eight_existing_passwords_login':True,
       'new_business_ids':True,'no_feishu_event_generated':True,
       'pending_job_and_aggregate_both_block_reset':True,'synthetic_unattempted_reviews_explicitly_cancelled':len(pending),
       'closed_export_session_and_immutable_audit_preserved':True}


def plan_inventory(backup_dir): return json.loads((backup_dir/'BACKUP.json').read_text())['inventory']
