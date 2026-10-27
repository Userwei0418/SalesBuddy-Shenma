"""Focused V285 task protocol verification in a disposable, Unix-only PG16 cluster.

PG_BIN=/path/to/pg16/bin python database/tests/verify_task_execution_v285_postgres.py
Uses synthetic tenants and exact restricted roles; never connects to real data.
"""
import asyncio,sys,json,uuid,re,hashlib,os,subprocess,tempfile
from pathlib import Path
import asyncpg
r=Path(__file__).resolve().parents[2]
s=None
sys.path[:0]=[str(r/'database/scripts'),str(r/'tools/installation')]
import migrate
from runtime_roles import load_policy,fingerprint,SCHEMAS
from upgrade_permissions import reconcile_service_roles
async def main(focus=None):
 c=await asyncpg.connect(host=str(s),port=56485,user='task_test_owner',database='postgres',command_timeout=90)
 await c.set_type_codec('jsonb',encoder=json.dumps,decoder=json.loads,schema='pg_catalog')
 for role in ['salegent_feishu_worker','salegent_api','salegent_worker','salegent_platform']:
  await c.execute(f'CREATE ROLE {role} NOLOGIN')
 await migrate.migrate(c,r/'database',adopt_existing=True)
 await c.execute('SET row_security=on')
 async with c.transaction():
  for name in ['task_test_api','task_test_worker','task_test_feishu','task_test_readonly']:
   if not await c.fetchval('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)',name): await c.execute(f'CREATE ROLE {name} LOGIN NOSUPERUSER NOBYPASSRLS')
  p=load_policy();p['migration_fingerprint']=fingerprint(dict(await c.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum')))
  p['schema']=migrate.discover(r/'database')[-1].key
  if 'workflow.task_execution_initialize_v2(uuid,jsonb,boolean)' not in p['services']['api']['functions']: p['services']['api']['functions'].append('workflow.task_execution_initialize_v2(uuid,jsonb,boolean)')
  if 'workflow.task_execution_enqueue_coordination_checks(integer)' not in p['services']['worker']['functions']: p['services']['worker']['functions'].append('workflow.task_execution_enqueue_coordination_checks(integer)')
  roles={k:'task_test_'+k for k in p['services']}
  for role in roles.values():
   if not await c.fetchval('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)',role): await c.execute(f'CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS')
  for svc,role in roles.items():
   for member in p['services'][svc]['memberships']: await c.execute(f'GRANT {member} TO {role} WITH INHERIT FALSE, SET FALSE')
  tables=[dict(x) for x in await c.fetch("SELECT n.nspname||'.'||c.relname AS name,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p') ORDER BY 1",SCHEMAS)]
  policies=[dict(x) for x in await c.fetch("SELECT schemaname,tablename,policyname,permissive,roles::text[],cmd,qual,with_check FROM pg_policies WHERE schemaname=ANY($1::text[]) ORDER BY 1,2,3",SCHEMAS)]
  for row in policies: row['roles']=sorted(v for v in row['roles'] if v not in roles.values())
  p['rls_fingerprint']=fingerprint({'tables':tables,'policies':policies})
  print('RLS fingerprint',p['rls_fingerprint'],flush=True)
  await reconcile_service_roles(c,roles,p)

 print('Exact four-service privilege checks passed',flush=True)
 ws=uuid.uuid4(); team=uuid.uuid4(); users=[uuid.uuid4() for _ in range(3)]
 async with c.transaction():
  await c.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'synthetic task v285')",ws,'task-v285-'+ws.hex)
  await c.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'test','synthetic')",team,ws)
  for n,u in enumerate(users):
   await c.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$3,$3)",u,ws,'USER'+str(n))
   await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'manager','workspace',$3)",ws,u,team)
   await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'manager')",ws,u,team)
 async def ctx(u):
  await c.execute('SET LOCAL ROLE task_test_api')
  await c.execute("SELECT set_config('app.workspace_id',$1,true),set_config('app.user_ref_id',$2,true),set_config('app.role_code','manager',true),set_config('app.authorization_context','',true)",str(ws),str(u))
 async def create(u,owner=None,legacy=False):
  owner=owner or u; t=uuid.uuid4()
  async with c.transaction():
   await ctx(u)
   due=await c.fetchval("SELECT clock_timestamp()+interval '7 days'")
   await c.execute("INSERT INTO workflow.task(id,workspace_id,creator_user_ref_id,creator_team_id,title,description,due_at,association_kind,source_code) VALUES($1,$2,$3,$4,'task','description',$5,'daily','mini_program')",t,ws,u,team,due)
   await c.execute("INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility) VALUES($1,$2,$3,$4,'manager','owner')",t,ws,owner,team)
   definition={'requirements':{'title':'task','description':'description','deliverable':'deliver report','acceptance_criteria':'report reviewed','due_at':due.isoformat(),'priority_code':'normal'},'checklist':[]}
   data=None if legacy else await c.fetchval('SELECT workflow.task_execution_initialize_v2($1,$2,$3)',t,definition,u==owner)
  return t,data
 async def read(t,u):
  async with c.transaction():
   await ctx(u);return await c.fetchval('SELECT workflow.task_execution_read($1)',t)
 async def command(t,u,act,payload,key=None,body=None):
  async with c.transaction():
   await ctx(u)
   data=None if body else await c.fetchval('SELECT workflow.task_execution_read($1)',t)
   body=body or {**{k:data[k] for k in ['version_no','requirements_version','responsibility_version','checklist_revision']},**payload}
   result=await c.fetchval('SELECT workflow.task_execution_command($1,$2,$3,$4)',t,act,body,key or uuid.uuid4())
   return result,body
 if focus is not None:
  await focus(c,ws,users,create,read,command)
  print('Migration fingerprint',p['migration_fingerprint'],flush=True)
  await c.close()
  return
 t,d=await create(users[0]);assert d['status']=='pending_execution' and d['first_started_at'] is None and d['progress']['percent'] is None
 defs=[{'item_id':str(uuid.uuid4()),'definition_version':1,'title':'first','acceptance_criteria':'evidence'},{'item_id':str(uuid.uuid4()),'definition_version':1,'title':'second','acceptance_criteria':'evidence'}]
 d=(await command(t,users[0],'save_checklist',{'checklist':defs,'reason':'plan'}))[0]['execution'];assert d['requirements_version']==1 and d['checklist_revision']==2 and d['progress']['percent']==0
 key=uuid.uuid4(); rr,bb=await command(t,users[0],'set_item_progress',{'item_id':defs[0]['item_id'],'definition_version':1,'state':'completed','note':'result'},key);assert rr['execution']['progress']['percent']==50 and rr['execution']['first_started_at'] is None
 replay,_=await command(t,users[0],'set_item_progress',{},key,bb);assert replay==rr
 await command(t,users[0],'pause',{'reason':'waiting','recheck_at':'2030-01-01T00:00:00Z'})
 paused=await read(t,users[0]);assert paused['capabilities']['save_checklist'] and not paused['capabilities']['set_item_progress']
 defs[0]['title']='changed';defs[0]['definition_version']=2
 rr,_=await command(t,users[0],'save_checklist',{'checklist':defs,'reason':'clarify'});assert rr['execution']['progress']['percent'] is None
 await command(t,users[0],'resume',{'pause_id':paused['pause']['id'],'reason':'ready'})
 for item in defs: await command(t,users[0],'set_item_progress',{'item_id':item['item_id'],'definition_version':item['definition_version'],'state':'completed','note':'confirmed evidence'})
 rr,_=await command(t,users[0],'submit_completion',{'note':'finished'});assert rr['execution']['status']=='completed'
 print('Self creation, empty list, autonomous save, independent version, replay, pause/revalidation and direct completion passed',flush=True)
 defs=[{**i,'definition_version':1,'item_id':str(uuid.uuid4())} for i in defs]
 t,d=await create(users[0],users[1]);assert d['status']=='pending_confirm'
 await command(t,users[1],'accept_task',{})
 await command(t,users[1],'save_checklist',{'checklist':defs,'reason':'plan'})
 for item in defs: await command(t,users[1],'set_item_progress',{'item_id':item['item_id'],'definition_version':item['definition_version'],'state':'completed','note':'result'})
 # Reviewer becomes unavailable; executor may still submit evidence and await coordination.
 await c.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1",users[0])
 rr,_=await command(t,users[1],'submit_completion',{'note':'await review'});assert rr['execution']['status']=='pending_review'
 async with c.transaction():
  await c.execute('SET LOCAL ROLE task_test_worker')
  notices=0
  for _ in range(3): notices+=await c.fetchval('SELECT workflow.task_execution_enqueue_coordination_checks(100)')
  assert notices>=1
 await c.execute("UPDATE platform.user_ref SET status='active' WHERE id=$1",users[0])
 rr,_=await command(t,users[0],'review_completion',{'submission_id':rr['execution']['current_submission'],'decision':'reject','note':'missing','missing_deliverables':'include appendix','rework_item_ids':[]})
 assert rr['execution']['status']=='in_progress' and rr['execution']['progress']['percent'] is None
 print('Other-assignee accept, unavailable reviewer submission, worker coordination and omitted-deliverable revalidation passed',flush=True)
 await command(t,users[0],'initiate_reviewer_handover',{'target_user_id':str(users[2]),'reason':'delegate review'})
 # Handover away and back to creator retains in-progress stage and returns to self mode.
 rr,_=await command(t,users[0],'initiate_executor_handover',{'target_user_id':str(users[0]),'reason':'take back'})
 rr,_=await command(t,users[0],'respond_executor_handover',{'handover_id':rr['execution']['execution_handover']['id'],'decision':'accept','reason':'take over'})
 assert rr['execution']['status']=='in_progress' and rr['execution']['self_managed']
 assert rr['execution']['reviewer_handover']['status']=='invalidated'
 await command(t,users[0],'cancel_task',{'reason':'cancel self initiated'})
 print('Handover back to creator and initiator cancellation passed',flush=True)
 # Old pending-review self task retains real historic submission and requires an explicit completion.
 t,_=await create(users[0],legacy=True)
 await c.execute("UPDATE workflow.task SET status='pending_review',accepted_at=clock_timestamp(),completion_submitted_at=clock_timestamp(),completion_note='historic result' WHERE id=$1",t)
 v=await c.fetchval('SELECT version_no FROM workflow.task WHERE id=$1',t)
 rr,_=await command(t,users[0],'initialize_execution',{},body={'version_no':v,'requirements_version':1,'responsibility_version':1,'checklist_revision':1,'reason':'adopt old task'})
 d=rr['execution'];assert d['status']=='pending_review' and d['progress']['percent'] is None
 rr,_=await command(t,users[0],'complete_self',{'submission_id':d['current_submission'],'note':'confirm historic completion'});assert rr['execution']['status']=='completed'
 t,d=await create(users[0]); stale={k:d[k] for k in ['version_no','requirements_version','responsibility_version','checklist_revision']}
 await command(t,users[0],'append_feedback',{'text':'comment'})
 try: await command(t,users[0],'pause',{},body={**stale,'reason':'stale','recheck_at':'2030-01-01T00:00:00Z'})
 except asyncpg.SerializationError: pass
 else: raise AssertionError('stale command unexpectedly accepted')
 async with c.transaction():
  await ctx(users[0]);await c.execute("SELECT set_config('app.workspace_id',$1,true)",str(uuid.uuid4()))
  try:
   async with c.transaction(): assert await c.fetchval('SELECT workflow.task_execution_read($1)',t) is None
  except (asyncpg.InsufficientPrivilegeError,asyncpg.NoDataFoundError): pass
 async with c.transaction():
  await ctx(users[0]);grants=await c.fetchval("SELECT jsonb_agg(to_jsonb(g)) FROM security.authorization_current_grants() g")
  grants=[g for g in grants if g['permission_code']!='task.cancel']+[{'permission_code':'task.cancel','effect':'deny','scope_code':None,'team_ids':[],'source_code':'synthetic','source_id':'test'}]
  await c.execute("SELECT set_config('app.authorization_context',$1,true)",json.dumps({'workspace_id':str(ws),'user_id':str(users[0]),'grants':grants}))
  denied_cap=await c.fetchval('SELECT workflow.task_execution_read($1)',t);assert not denied_cap['capabilities']['cancel_task']
  try:
   async with c.transaction(): await c.fetchval('SELECT workflow.task_execution_command($1,$2,$3,$4)',t,'cancel_task',{**{k:denied_cap[k] for k in stale},'reason':'must reject'},uuid.uuid4())
  except (asyncpg.InsufficientPrivilegeError,asyncpg.NoDataFoundError): pass
  else: raise AssertionError('explicit deny unexpectedly bypassed')
 # Deferred legacy adoption requires an explicit phase, backed by real acceptance.
 t,_=await create(users[0],legacy=True)
 await c.execute("UPDATE workflow.task SET status='deferred' WHERE id=$1",t)
 v=await c.fetchval('SELECT version_no FROM workflow.task WHERE id=$1',t)
 body={'version_no':v,'requirements_version':1,'responsibility_version':1,'checklist_revision':1,'reason':'restore historic deferred'}
 try: await command(t,users[0],'initialize_execution',{},body=body)
 except asyncpg.SerializationError: pass
 else: raise AssertionError('deferred adopted without explicit phase')
 rr,_=await command(t,users[0],'initialize_execution',{},body={**body,'resume_status':'pending_confirm'})
 assert rr['execution']['status']=='pending_confirm' and rr['execution']['capabilities']['accept_task']
 await command(t,users[0],'accept_task',{})
 print('Explicit deferred legacy recovery preserves acceptance boundary',flush=True)
 print('Legacy explicit adoption/completion, stale versions, tenant isolation and explicit cancel deny passed',flush=True)
 names=await c.fetch("SELECT tgname FROM pg_trigger WHERE tgrelid='workflow.notification'::regclass AND NOT tgisinternal")
 print('Notification non-internal triggers:',[x['tgname'] for x in names],flush=True)
 print('Migration fingerprint',p['migration_fingerprint'],flush=True)
 await c.close()
def run(focus=None):
 global s
 # Creates an isolated cluster; deliberately accepts no DATABASE_URL / DSN.
 # Requires PostgreSQL 16 binaries and asyncpg, never reads application secrets.
 b=Path(os.environ['PG_BIN'])
 with tempfile.TemporaryDirectory(prefix='sb-task-v285-') as tmp:
  s=Path(tmp)
  subprocess.run([str(b/'initdb'),'-D',str(s/'pg'),'-U','task_test_owner','--auth=trust','--encoding=UTF8','--no-locale'],check=True,stdout=subprocess.DEVNULL)
  subprocess.run([str(b/'pg_ctl'),'-D',str(s/'pg'),'-l',str(s/'postgres.log'),'-o',f"-k {s} -p 56485 -c listen_addresses=''",'-w','start'],check=True,stdout=subprocess.DEVNULL)
  try: asyncio.run(main(focus))
  finally: subprocess.run([str(b/'pg_ctl'),'-D',str(s/'pg'),'-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL)


if __name__=='__main__':
 run()
