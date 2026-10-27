"""Focused identity/RLS checks in a disposable Unix-only synthetic PostgreSQL 16.

PG_BIN=/path/to/pg16/bin python database/tests/verify_mini_readonly_v287_postgres.py
SB_KEEP_TEST_CLUSTER=1 retains this synthetic cluster for integration review.
Never accepts DATABASE_URL, reads a secret file or contacts a real environment.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'database/scripts'), str(ROOT / 'tools/installation')]
import migrate
from runtime_roles import SCHEMAS, fingerprint, load_policy
from upgrade_permissions import reconcile_service_roles

API_FUNCTIONS = [
    'security.demo_readonly_candidate(text,text,text)',
    'security.demo_readonly_create_session(uuid,uuid,uuid,integer,text,text)',
    'security.demo_readonly_session(uuid,uuid,uuid,integer)',
    'security.demo_readonly_revoke_session(uuid,uuid,uuid)',
    'security.demo_readonly_admit(uuid,uuid,uuid,integer)',
    'security.demo_readonly_read(uuid,uuid,uuid,integer,text,uuid,text,integer,integer)',
    'security.is_demo_readonly_identity(uuid,uuid)',
    'workflow.task_execution_progress(jsonb)',
]


async def verify(socket):
    db = await asyncpg.connect(host=str(socket), port=56487, user='mini_test_owner', database='postgres')
    await db.set_type_codec('jsonb', encoder=json.dumps, decoder=json.loads, schema='pg_catalog')
    for role in ['salegent_feishu_worker', 'salegent_api', 'salegent_worker', 'salegent_platform']:
        await db.execute(f'CREATE ROLE {role} NOLOGIN')
    await migrate.migrate(db, ROOT / 'database', adopt_existing=True)
    await db.execute('SET row_security=on')
    policy = load_policy()
    policy['schema'] = 'V287'
    policy['migration_fingerprint'] = fingerprint(dict(await db.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum')))
    for service in policy['services'].values():
        if 'security' in service['schemas'] and 'security.demo_readonly_context(uuid)' not in service['functions']:
            service['functions'].append('security.demo_readonly_context(uuid)')
        if 'security' in service['schemas'] and 'security.is_demo_readonly_identity(uuid,uuid)' not in service['functions']:
            service['functions'].append('security.is_demo_readonly_identity(uuid,uuid)')
    policy['services']['api']['functions'] = sorted(set(policy['services']['api']['functions'] + API_FUNCTIONS))
    policy['services']['api']['columns']['security.demo_readonly_text'] = {
        'SELECT': ['workspace_id', 'resource', 'resource_id', 'source_updated_at', 'display_text']}
    roles = {kind: 'mini_test_' + kind for kind in policy['services']}
    for service, role in roles.items():
        await db.execute(f'CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS')
        for member in policy['services'][service]['memberships']:
            await db.execute(f'GRANT {member} TO {role} WITH INHERIT FALSE, SET FALSE')
    tables = [dict(x) for x in await db.fetch("SELECT n.nspname||'.'||c.relname name,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p') ORDER BY 1", SCHEMAS)]
    policies = [dict(x) for x in await db.fetch("SELECT schemaname,tablename,policyname,permissive,roles::text[],cmd,qual,with_check FROM pg_policies WHERE schemaname=ANY($1::text[]) ORDER BY 1,2,3", SCHEMAS)]
    for row in policies:
        row['roles'] = sorted(v for v in row['roles'] if v not in roles.values())
    policy['rls_fingerprint'] = fingerprint({'tables': tables, 'policies': policies})
    async with db.transaction():
        await reconcile_service_roles(db, roles, policy)
    print(json.dumps({'schema': 'V287', 'migration_fingerprint': policy['migration_fingerprint'], 'rls_fingerprint': policy['rls_fingerprint'], 'four_service_permissions': 'passed'}), flush=True)

    ws = await db.fetchval("INSERT INTO platform.workspace(external_workspace_id,name) VALUES('demo-sales-workspace','Synthetic demonstration company') RETURNING id")
    other = await db.fetchval("INSERT INTO platform.workspace(external_workspace_id,name) VALUES('synthetic-other','Other synthetic company') RETURNING id")
    uid = await db.fetchval("INSERT INTO platform.user_ref(workspace_id,external_user_id,account_code,display_name) VALUES($1,'synthetic-user','NORMAL001','Synthetic colleague') RETURNING id", ws)
    await db.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,'manager','workspace')", ws, uid)
    other_uid = await db.fetchval("INSERT INTO platform.user_ref(workspace_id,external_user_id,account_code,display_name) VALUES($1,'synthetic-other-user','OTHER001','Other colleague') RETURNING id", other)
    hash_value = hashlib.sha256(uuid4().bytes).hexdigest()
    identity = await db.fetchval("SELECT security.demo_readonly_register($1,'fxs001',$2)", ws, hash_value)
    assert not await db.fetchval('SELECT EXISTS(SELECT 1 FROM platform.user_ref WHERE id=$1)', identity)
    first, second = uuid4(), uuid4()

    async def api_call(sql, *params, readonly=False):
        async with db.transaction(readonly=readonly):
            await db.execute('SET LOCAL ROLE mini_test_api')
            return await db.fetchval(sql, *params)

    async def denied(sql, *params, code='42501'):
        try:
            await api_call(sql, *params)
        except asyncpg.PostgresError as exc:
            assert exc.sqlstate == code, (exc.sqlstate, str(exc))
        else:
            raise AssertionError('Expected denial')

    candidate = await api_call("SELECT security.demo_readonly_candidate(NULL,'fXs001','account')")
    assert candidate['identity_id'] == str(identity) and candidate['enabled']
    assert await api_call("SELECT security.demo_readonly_candidate('synthetic-other','fxs001','account')") is None
    assert await api_call("SELECT security.demo_readonly_candidate(NULL,'fxs001','phone')") is None
    assert await api_call('SELECT security.demo_readonly_create_session($1,$2,$3,1,$4,$5)', first, ws, identity, 'wrong-hash', 'a' * 64) is None
    for sid in [first, second]:
        session = await api_call('SELECT security.demo_readonly_create_session($1,$2,$3,1,$4,$5)', sid, ws, identity, hash_value, 'a' * 64)
        assert session['session_kind'] == 'demo_readonly' and 'role' not in session and 'password_hash' not in session
    await denied('SELECT password_hash FROM security.demo_readonly_identity')
    await denied("SELECT security.demo_readonly_register($1,'OTHERX',$2)", ws, hash_value)
    await denied("SELECT security.demo_readonly_read($1,$2,$3,1,'customers',NULL,'',0,20)", first, ws, identity)
    await denied("SELECT security.demo_readonly_read($1,$2,$3,1,'not-allowed',NULL,'',0,20)", first, ws, identity)

    customer = await db.fetchval("INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id,demand_summary) VALUES($1,'Visible company','visible company',$2,'PRIVATE AI SUMMARY') RETURNING id", ws, uid)
    hidden = await db.fetchval("INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id) VALUES($1,'Hidden company','hidden company',$2) RETURNING id", other, other_uid)
    await db.execute("SELECT set_config('app.workspace_id',$1,false),set_config('app.user_ref_id',$2,false),set_config('app.role_code','manager',false)", str(ws), str(uid))
    opportunity = await db.fetchval("INSERT INTO crm.opportunity(workspace_id,customer_id,name,created_by_user_ref_id,amount) VALUES($1,$2,'Visible opportunity',$3,12000) RETURNING id", ws, customer, uid)
    task = await db.fetchval("INSERT INTO workflow.task(workspace_id,creator_user_ref_id,title,description,due_at,customer_id,opportunity_id,association_kind) VALUES($1,$2,'Visible task','PRIVATE AI DESCRIPTION',clock_timestamp()+interval '7 days',$3,$4,'customer') RETURNING id", ws, uid, customer, opportunity)
    form = await db.fetchval("SELECT id FROM config.form_version WHERE status='active' LIMIT 1")
    if form is None:
        definition = await db.fetchval("INSERT INTO config.form_definition(form_code,name,object_type) VALUES('synthetic-mini-test','Synthetic','visit') RETURNING id")
        form = await db.fetchval("INSERT INTO config.form_version(form_definition_id,version_no,status) VALUES($1,1,'active') RETURNING id", definition)
    visit = await db.fetchval("INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,recorder_user_ref_id,created_by_user_ref_id,form_version_id,status,interaction_at,archived_at,follow_up_record) VALUES($1,$2,$3,$4,$4,$5,'archived','2026-10-23 16:00+08',clock_timestamp(),'PRIVATE AI FOLLOWUP') RETURNING id", ws, customer, opportunity, uid, form)

    async def read(resource, record=None, session=first, workspace=ws, query=''):
        return await api_call('SELECT security.demo_readonly_read($1,$2,$3,1,$4,$5,$6,0,20)', session, workspace, identity, resource, record, query, readonly=True)

    result = await read('customers')
    assert result['total'] == 1 and result['items'][0]['id'] == str(customer)
    assert 'PRIVATE' not in json.dumps(result) and result['items'][0]['description'] is None
    assert await read('customers', hidden) is None and await read('customers', workspace=other) is None
    assert (await read('customers', query='Hidden'))['total'] == 0
    assert (await read('opportunities', opportunity))['customer']['id'] == str(customer)
    await db.execute("UPDATE crm.opportunity SET status='won',stage_code='identified' WHERE id=$1", opportunity)
    assert (await read('opportunities', opportunity))['stage'] == 'won'
    task_view = await read('tasks', task)
    assert task_view['progress_percent'] is None and task_view['description'] is None
    visit_view = await read('follow-ups', visit)
    assert visit_view['follow_up_date'] == '2026-10-23' and visit_view['display_text'] is None
    assert 'PRIVATE' not in json.dumps(visit_view)
    await db.execute("INSERT INTO workflow.task_execution(task_id,workspace_id,state) VALUES($1,$2,$3)", task, ws, {'requirements_version': 1, 'items': [{'state': 'completed'}, {'state': 'in_progress'}]})
    assert (await read('tasks', task))['progress_percent'] == 50
    counts = await read('overview')
    assert counts['customer_count'] == 1 and counts['task_count'] == 1 and counts['follow_up_count'] == 1
    assert await db.fetchval("SELECT security.demo_readonly_review_text($1,'customers',$2,'Reviewed business facts','synthetic operator')", ws, customer)
    assert (await read('customers', customer))['description'] == 'Reviewed business facts'
    await db.execute("UPDATE crm.customer SET industry_code='synthetic-change' WHERE id=$1", customer)
    assert (await read('customers', customer))['description'] is None
    assert (await read('overview'))['data_version'] != counts['data_version']
    # The ordinary manager still uses its existing authorization context.
    async with db.transaction(readonly=True):
        await db.execute('SET LOCAL ROLE mini_test_api')
        await db.execute("SELECT set_config('app.workspace_id',$1,true),set_config('app.user_ref_id',$2,true),set_config('app.role_code','manager',true),set_config('app.demo_readonly_context','',true)", str(ws), str(uid))
        await db.execute('SELECT security.authorization_refresh()')
        assert await db.fetchval('SELECT count(*) FROM crm.customer WHERE id=$1', customer) == 1
        assert await db.fetchval('SELECT count(*) FROM crm.customer WHERE id=$1', hidden) == 0
    print('Fixed tenant lists/details/search/counts, safe DTOs, reviewed-text invalidation and real missing progress passed', flush=True)

    # Ambiguous unscoped names must not select either password candidate.
    duplicate = await db.fetchval("INSERT INTO platform.user_ref(workspace_id,external_user_id,account_code,display_name) VALUES($1,'duplicate','FXS001','Normal other company') RETURNING id", other)
    await db.execute('INSERT INTO platform.password_credential(workspace_id,user_ref_id,password_hash) VALUES($1,$2,$3)', other, duplicate, hash_value)
    assert await api_call("SELECT security.demo_readonly_candidate(NULL,'FXS001','account')") is None
    assert await api_call("SELECT security.demo_readonly_candidate('demo-sales-workspace','FXS001','account')") is not None
    assert await api_call("SELECT count(*) FROM security.password_login_identifier(NULL,'FXS001','account')") == 0
    # Per-session logout preserves the other device; reset revokes it.
    assert await api_call('SELECT security.demo_readonly_revoke_session($1,$2,$3)', first, ws, identity)
    assert await read('customers') is None and await read('customers', session=second) is not None
    for n in range(60):
        assert await api_call('SELECT security.demo_readonly_admit($1,$2,$3,1)', second, ws, identity)
    assert not await api_call('SELECT security.demo_readonly_admit($1,$2,$3,1)', second, ws, identity)
    assert await db.fetchval('SELECT security.demo_readonly_update_identity($1,$2,NULL,false)', identity, hashlib.sha256(uuid4().bytes).hexdigest())
    assert await read('customers', session=second) is None
    print('Combined name ambiguity, independent logout, password revocation, private credentials and fixed request budget passed', flush=True)
    await db.close()


def main():
    binaries = Path(os.environ['PG_BIN'])
    socket = Path(tempfile.mkdtemp(prefix='sb-mini-v287-'))
    subprocess.run([str(binaries / 'initdb'), '-D', str(socket / 'pg'), '-U', 'mini_test_owner', '--auth=trust', '--encoding=UTF8', '--no-locale'], check=True, stdout=subprocess.DEVNULL)
    subprocess.run([str(binaries / 'pg_ctl'), '-D', str(socket / 'pg'), '-l', str(socket / 'postgres.log'), '-o', f"-k {socket} -p 56487 -c listen_addresses=''", '-w', 'start'], check=True, stdout=subprocess.DEVNULL)
    try:
        asyncio.run(verify(socket))
    finally:
        if os.environ.get('SB_KEEP_TEST_CLUSTER') == '1':
            print('SYNTHETIC_CLUSTER=' + str(socket), flush=True)
        else:
            subprocess.run([str(binaries / 'pg_ctl'), '-D', str(socket / 'pg'), '-m', 'fast', '-w', 'stop'], check=True, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
