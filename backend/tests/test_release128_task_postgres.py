"""Optional one-shot catalog/ACL check in a disposable Unix-only PG16 database.

PG_BIN=/path/to/pg16/bin python backend/tests/test_release128_task_postgres.py
No application DSN, credentials, production connection, model or worker calls.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'backend/deploy'), str(ROOT/'database/scripts'), str(ROOT/'tools/installation')]
import migrate
import release128_task_security as security
from runtime_roles import load_policy, fingerprint, SCHEMAS
from upgrade_permissions import reconcile_service_roles


async def verify(socket):
    conn = await asyncpg.connect(host=str(socket), port=56489, user='postgres', database='postgres')
    await conn.set_type_codec('jsonb', encoder=json.dumps, decoder=json.loads, schema='pg_catalog')
    for name in ('salegent_feishu_worker','salegent_api','salegent_worker','salegent_platform'):
        await conn.execute(f'CREATE ROLE {name} NOLOGIN')
    discover = migrate.discover
    migrate.discover = lambda root: [m for m in discover(root) if int(m.key[1:]) <= 286]
    try:
        await migrate.migrate(conn, ROOT/'database', adopt_existing=True)
    finally:
        migrate.discover = discover
    await conn.execute('SET row_security=on')
    async with conn.transaction():
        policy = load_policy()
        policy['schema'] = 'V286'
        policy['migration_fingerprint'] = fingerprint(dict(await conn.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum')))
        # Use the exact previous policy matrix even when invoked after integration.
        for service in policy['services'].values():
            service['functions'] = [sig for sig in service['functions'] if sig not in security.FUNCTION_GRANTS and sig not in security.OLD_EXTRA_GRANTS]
            service['columns'] = {k:v for k,v in service['columns'].items() if k not in security.TABLE_GRANTS}
        for role in security.ROLES.values():
            await conn.execute(f'CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS')
        for svc, role in security.ROLES.items():
            for member in policy['services'][svc]['memberships']:
                await conn.execute(f'GRANT {member} TO {role} WITH INHERIT FALSE, SET FALSE')
        tables = [dict(r) for r in await conn.fetch("SELECT n.nspname||'.'||c.relname AS name,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p') ORDER BY 1", SCHEMAS)]
        policies = [dict(r) for r in await conn.fetch('SELECT schemaname,tablename,policyname,permissive,roles::text[],cmd,qual,with_check FROM pg_policies WHERE schemaname=ANY($1::text[]) ORDER BY 1,2,3', SCHEMAS)]
        for row in policies:
            row['roles'] = sorted(role for role in row['roles'] if role not in security.ROLES.values())
        policy['rls_fingerprint'] = fingerprint({'tables':tables,'policies':policies})
        await reconcile_service_roles(conn, security.ROLES, policy)
    # Production's historical task table is owned by sales_owner. New indexes
    # inherit the table owner even when postgres runs CREATE INDEX.
    await conn.execute('CREATE ROLE sales_owner NOLOGIN')
    await conn.execute('GRANT USAGE ON SCHEMA workflow,common TO sales_owner')
    await conn.execute('GRANT EXECUTE ON FUNCTION common.current_workspace_id() TO sales_owner')
    await conn.execute('ALTER TABLE workflow.task OWNER TO sales_owner')
    await conn.execute('ALTER FUNCTION workflow.task_execution_command(uuid,text,jsonb,uuid) OWNER TO sales_owner')
    ws, user, task = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await conn.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'synthetic release')", ws, str(ws))
    await conn.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,'synthetic','synthetic','synthetic')", user, ws)
    await conn.execute("INSERT INTO workflow.task(id,workspace_id,creator_user_ref_id,title,description,due_at,association_kind,source_code) VALUES($1,$2,$3,'synthetic legacy','preserve',clock_timestamp()+interval '1 day','daily','mini_program')", task, ws, user)
    async with conn.transaction(readonly=True):
        before, data = await security.snapshot(conn), await security.business_state(conn)
    identity = dict(await conn.fetchrow("SELECT current_database() AS database,(SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS oid"))
    # Fresh baseline schema omits the historical pre-V035 registration rows.
    # Add only synthetic fixture history to match the already-frozen production
    # prefix. The release helper itself never adopts or changes old ledgers.
    await conn.executemany("INSERT INTO ops.schema_migration(version,description) VALUES($1,'synthetic historical fixture')",
        [(f'V{i:03}',) for i in range(1,35) if i != 14])
    result = await security.rehearse(conn, expected_database=identity, synthetic=True)
    assert result['evidence']['restored_from_production_backup'] is False
    try:
        security.contract.validate_restore_evidence(result['evidence'],result['new_objects'],digest=security.digest,require=security.require)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Synthetic evidence accepted for production')
    async with conn.transaction(readonly=True):
        assert await security.snapshot(conn) == before
        assert await security.business_state(conn) == data
        await security.verify_effective_privileges(conn)
        await security.verify_no_seed(conn)
    assert result['new_objects']['functions'][security.COMMAND]['properties']['owner']=='sales_owner'
    assert result['new_objects']['functions'][security.ARCHIVE]['properties']['owner']=='sales_owner'
    # Reject privilege expansion with no persistent changes even in this fixture.
    async def rejects(sql,check):
        tx=conn.transaction(); await tx.start()
        try:
            await conn.execute(sql)
            try: await check()
            except RuntimeError: pass
            else: raise AssertionError('Unsafe mutation was accepted: '+sql)
        finally: await tx.rollback()
    async def acl_check(): security.validate_new_acl(await security.new_objects(conn))
    async def preserved_check(): security.require(await security.snapshot(conn)==before,'old security changed')
    await rejects('GRANT EXECUTE ON FUNCTION security.demo_readonly_register(uuid,text,text) TO sales_api_saas',acl_check)
    await rejects('GRANT SELECT (reviewed_by) ON security.demo_readonly_text TO sales_api_saas',acl_check)
    await rejects('ALTER ROLE sales_worker_saas BYPASSRLS',preserved_check)
    await rejects('GRANT SELECT ON platform.password_credential TO sales_feishu_saas',preserved_check)
    await rejects('ALTER POLICY permission_read ON crm.customer USING (true)',preserved_check)
    # Exercise actual V3 writes with a historical command owner distinct from
    # the migration owner. Keep these UI-like checks in a rolled-back fixture.
    tx=conn.transaction(); await tx.start()
    try:
        team=uuid.uuid4(); other=uuid.uuid4()
        await conn.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'check','Check')",team,ws)
        await conn.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,'other','OTHER','Other')",other,ws)
        for actor in (user,other):
            await conn.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'manager','workspace',$3)",ws,actor,team)
            await conn.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'manager')",ws,actor,team)
        await conn.execute('SET LOCAL ROLE sales_api_saas')
        await conn.execute("SELECT set_config('app.workspace_id',$1,true),set_config('app.user_ref_id',$2,true),set_config('app.role_code','manager',true)",str(ws),str(user))
        new_task=uuid.uuid4()
        await conn.execute("INSERT INTO workflow.task(id,workspace_id,creator_user_ref_id,creator_team_id,title,description,due_at,association_kind,source_code,status) VALUES($1,$2,$3,$4,'Owner check','Owner check description',clock_timestamp()+interval '7 days','daily','mini_program','in_progress')",new_task,ws,user,team)
        await conn.execute("INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility) VALUES($1,$2,$3,$4,'manager','owner')",new_task,ws,other,team)
        state=await conn.fetchval('SELECT workflow.task_execution_initialize_v3($1,$2)',new_task,{})
        candidates=await conn.fetchval("SELECT workflow.task_execution_candidates($1,'executor','',50,0)",new_task)
        assert str(user) in [r['user_id'] for r in candidates['items']]
        versions={k:state[k] for k in ('version_no','requirements_version','responsibility_version')}
        command_result=await conn.fetchval("SELECT workflow.task_execution_command_v3($1,'revise',$2,$3)",new_task,{**versions,'executor_user_id':str(user)},uuid.uuid4())
        state=command_result['execution']; assert state['self_managed'] is True
        versions={k:state[k] for k in ('version_no','requirements_version','responsibility_version')}
        command_result=await conn.fetchval("SELECT workflow.task_execution_command_v3($1,'append_feedback',$2,$3)",new_task,{**versions,'text':'Real progress','next_step':'Next action'},uuid.uuid4())
        assert command_result['execution']['status']=='in_progress'
        print('PASS: different legacy/new definer owners: V3 initialize, creator candidate, reassign back to self and feedback')
        # Make only synthetic identities/tasks available to the two-session race.
        race_task=uuid.uuid4()
        await conn.execute("INSERT INTO workflow.task(id,workspace_id,creator_user_ref_id,creator_team_id,title,description,due_at,association_kind,source_code,status) VALUES($1,$2,$3,$4,'Race check','Race check description',clock_timestamp()+interval '7 days','daily','mini_program','in_progress')",race_task,ws,user,team)
        await conn.execute("INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility) VALUES($1,$2,$3,$4,'manager','owner')",race_task,ws,user,team)
        await tx.commit()
    except BaseException:
        await tx.rollback(); raise
    second=await asyncpg.connect(host=str(socket),port=56489,user='sales_api_saas',database='postgres')
    await second.set_type_codec('jsonb',encoder=json.dumps,decoder=json.loads,schema='pg_catalog')
    await second.execute("SELECT set_config('app.workspace_id',$1,false),set_config('app.user_ref_id',$2,false),set_config('app.role_code','manager',false)",str(ws),str(user))
    locked=conn.transaction(); await locked.start()
    try:
        await conn.execute('SELECT id FROM workflow.task WHERE id=$1 FOR UPDATE',race_task)
        waiter=asyncio.create_task(second.fetchval("SELECT workflow.task_execution_command($1,'initialize_execution',$2,$3)",race_task,
            {'version_no':2,'requirements_version':1,'responsibility_version':1,'checklist_revision':1,'reason':'Race legacy request'},uuid.uuid4()))
        # Wait for the actual lock wait, not a timing assumption.
        for _ in range(100):
            if waiter.done(): await waiter; raise AssertionError('Legacy admission did not wait on task lock')
            waiting=await conn.fetchval("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1",second.get_server_pid())
            if waiting: break
            await asyncio.sleep(0.01)
        else: raise AssertionError('Legacy command lock wait was not observed')
        await conn.execute('SET LOCAL ROLE sales_api_saas')
        await conn.execute("SELECT set_config('app.workspace_id',$1,true),set_config('app.user_ref_id',$2,true),set_config('app.role_code','manager',true)",str(ws),str(user))
        adoption=await conn.fetchval('SELECT workflow.task_execution_adoption_v3($1)',race_task)
        body={k:adoption[k] for k in ('version_no','requirements_version','responsibility_version')}
        await conn.fetchval('SELECT workflow.task_execution_adopt_v3($1,$2,$3)',race_task,body,uuid.uuid4())
        await locked.commit()
        try: await waiter
        except asyncpg.PostgresError as error: assert 'TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED' in str(error)
        else: raise AssertionError('Old command downgraded an adopted task')
        assert await conn.fetchval('SELECT execution_mode FROM workflow.task WHERE id=$1',race_task)=='execution_v3'
        print('PASS: concurrent legacy initialize waits for adoption and cannot downgrade V3')
    except BaseException:
        if conn.is_in_transaction(): await locked.rollback()
        raise
    finally: await second.close()
    tables=[dict(r) for r in await conn.fetch("SELECT n.nspname||'.'||c.relname AS name,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p') ORDER BY 1",SCHEMAS)]
    policies=[dict(r) for r in await conn.fetch('SELECT schemaname,tablename,policyname,permissive,roles::text[],cmd,qual,with_check FROM pg_policies WHERE schemaname=ANY($1::text[]) ORDER BY 1,2,3',SCHEMAS)]
    for row in policies: row['roles']=sorted(role for role in row['roles'] if role not in security.ROLES.values())
    print('PASS: V286→V287→V288 preserved old data/security, exact grants, wrapper ownership, no identity seed, synthetic evidence rejected for production; five unsafe changes refused')
    print(json.dumps({'migration_fingerprint':fingerprint(dict(await conn.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))),'rls_fingerprint':fingerprint({'tables':tables,'policies':policies}),'new_objects_sha256':security.digest(result['new_objects'])},indent=2))
    await conn.close()


if __name__ == '__main__':
    import shutil
    binaries=Path(os.environ['PG_BIN'])
    root=Path(tempfile.mkdtemp(prefix='sb-release128-'))
    keep=os.environ.get('SB_KEEP_TEST_CLUSTER')=='1'
    subprocess.run([str(binaries/'initdb'),'-D',str(root/'pg'),'-U','postgres','--auth=trust','--encoding=UTF8','--no-locale'],check=True,stdout=subprocess.DEVNULL)
    subprocess.run([str(binaries/'pg_ctl'),'-D',str(root/'pg'),'-l',str(root/'postgres.log'),'-o',f"-k {root} -p 56489 -c listen_addresses=''",'-w','start'],check=True,stdout=subprocess.DEVNULL)
    passed=False
    try:
        asyncio.run(verify(root)); passed=True
    finally:
        if keep and passed: print('Synthetic cluster retained:',root,'port=56489 database=postgres operator=postgres api=sales_api_saas (Unix trust only)')
        else:
            subprocess.run([str(binaries/'pg_ctl'),'-D',str(root/'pg'),'-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL)
            shutil.rmtree(root)
