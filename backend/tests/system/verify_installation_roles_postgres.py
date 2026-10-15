"""Exercise NEW installation roles on a disposable loopback database only."""
import argparse
import asyncio
import base64
import copy
from dataclasses import replace
import json
from pathlib import Path
import secrets
import sys
import tempfile
import time
from unittest.mock import patch
import uuid

import asyncpg
import httpx

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'tools/installation'), str(ROOT/'tools/local'),
               str(ROOT/'database/scripts'), str(ROOT/'backend/src')]
from migrate import migrate
from runtime_roles import RolePolicyError, load_policy, provision_new, verify
from bootstrap import seed, identity
from sales_backend.config import get_settings
from sales_backend.db import Database
from sales_backend.feishu_worker import FeishuWorker, verify_worker_role
from sales_backend.services.platform_control import verify_platform_pool
from sales_backend.repositories.jobs import JobRepository
from sales_backend.worker import Worker
from sales_backend.auth.passwords import encode_password
from sales_backend.security.platform_mfa import PlatformMFACipher, authenticator


async def rejected(awaitable):
    try:
        await awaitable
    except RolePolicyError:
        return
    raise AssertionError('Unsafe install/permission drift was accepted')


async def permission_denied(connection, sql):
    try:
        await connection.execute(sql)
    except asyncpg.InsufficientPrivilegeError:
        return
    raise AssertionError('Restricted login unexpectedly executed prohibited SQL')


async def meeting_import_http(client, headers, account, previous_batch=None):
    """Use the exact installed API login, never the integration suite's broad role."""
    source = account + '：\n逐项核对安装验收周会事项'
    preview_body = {'source_text': source, 'meeting_date': '2032-01-01', 'batch_name': '安装角色周会'}
    response = await client.post('/api/v1/task-imports/preview', headers=headers, json=preview_body)
    assert response.status_code == 200, (response.status_code, response.text)
    batch = response.json()
    assert batch['parse_method'] == 'rules' and batch['tasks'] == []
    item = batch['editable_items'][0]
    item.update(assignee_account_code=account, due_at='2032-01-02T09:00:00+08:00')
    allowed = {'item_id','selected','description','assignee_account_code','due_at','priority_code',
               'association_kind','customer_id','opportunity_id','duplicate_acknowledged'}
    draft = {'version_no':batch['version_no'],'items':[{k:v for k,v in item.items() if k in allowed}]}
    response = await client.patch('/api/v1/task-imports/'+batch['id']+'/draft', headers=headers, json=draft)
    assert response.status_code == 200, (response.status_code, response.text)
    confirm = {'version_no':response.json()['version_no'],
               'items':[{k:v for k,v in draft['items'][0].items() if k != 'selected'}]}
    mutation_headers = {**headers,'Idempotency-Key':str(uuid.uuid4())}
    response = await client.post('/api/v1/task-imports/'+batch['id']+'/confirm', headers=mutation_headers, json=confirm)
    assert response.status_code == 200, (response.status_code, response.text)
    confirmed = response.json()
    assert confirmed['status'] == 'confirmed' and len(confirmed['tasks']) == 1
    task = confirmed['tasks'][0]
    assert task['source_code'] == 'meeting_import' and task['original_assignee']['account_code'] == account
    repeated = await client.post('/api/v1/task-imports/'+batch['id']+'/confirm', headers=mutation_headers, json=confirm)
    assert repeated.status_code == 200 and repeated.json()['tasks'][0]['id'] == task['id'], repeated.text
    pasted = await client.post('/api/v1/task-imports/preview', headers=headers, json=preview_body)
    assert pasted.status_code == 200 and pasted.json()['id'] == batch['id'], pasted.text
    listing = await client.get('/api/v1/task-imports?status=confirmed&page_size=1', headers=headers)
    assert listing.status_code == 200 and listing.json()['items'][0]['id'] == batch['id'], listing.text
    assert listing.json()['items'][0]['summary']['visible_task_count'] == 1
    if previous_batch:
        assert (await client.get('/api/v1/task-imports/'+previous_batch, headers=headers)).status_code == 404
    return batch['id']


async def runtime_http(config, database, roles, passwords, owner):
    """Real ASGI HTTP and real login connections; no request/DB dependency mocks."""
    from sales_backend.main import app
    account_password = secrets.token_urlsafe(30)
    fixtures = await seed(owner, {'test_password': account_password})
    # Exercise management routes whose modules are opt-in on a fresh tenant.
    # Synthetic module entitlement is not a provider credential or an AI call.
    await owner.execute("UPDATE control.tenant_module SET enabled=true,source='installation-test-fixture'")
    # Only this test seeds synthetic identities; product provisioning leaves them empty.
    operator = await owner.fetchval('''INSERT INTO control.operator(account_code,display_name,password_hash)
        VALUES('INSTALLTEST','安装验证管理员',$1) RETURNING id''', encode_password(account_password))
    def dsn(service):
        return f'postgresql://{roles[service]}:{passwords[service]}@127.0.0.1:{config["port"]}/{database}'
    settings = replace(get_settings(), app_env='production', auth_mode='password',
        database_url=dsn('api'), platform_database_url=dsn('platform'),
        access_token_secret=secrets.token_urlsafe(48), database_min_pool_size=1,
        database_max_pool_size=3, senseaudio_api_key='', agent_platform_bindings_json='{}')
    pools = []
    try:
        platform = Database(replace(settings, database_url=dsn('platform')))
        await platform.connect(); pools.append(platform)
        await verify_platform_pool(platform)
        target_scope_write = 'security.target_scope_write(text,uuid,uuid,text)'
        for service, role in roles.items():
            assert await owner.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')',
                                        role, target_scope_write) is (service == 'api'), service
        async with platform.connection() as platform_connection:
            await permission_denied(platform_connection,
                                    "SELECT security.target_scope_write('person',NULL,NULL,'sales')")
        for service in ('api', 'worker', 'feishu'):
            c = await asyncpg.connect(database=database, host='127.0.0.1', port=config['port'],
                                      user=roles[service], password=passwords[service])
            try:
                for sql in ('SELECT * FROM platform.password_credential',
                            'SELECT * FROM control.operator',
                            'SELECT * FROM control.support_grant',
                            'SELECT control.support_assignments(NULL)',
                            'SELECT * FROM ops.migration_checksum',
                            'SELECT security.reconcile_runtime_grants()'):
                    await permission_denied(c, sql)
                if service != 'feishu':
                    await permission_denied(c, 'SELECT * FROM security.feishu_credential')
                    assert await c.fetchval('SELECT count(*) FROM crm.customer') == 0
                if service == 'worker':
                    await permission_denied(c, "SELECT security.target_scope_write('person',NULL,NULL,'sales')")
                    await permission_denied(c, 'SELECT security.support_grants()')
                    await permission_denied(c, "SELECT security.password_candidate('x','x','x')")
                    await permission_denied(c, "UPDATE platform.user_ref SET display_name='unexpected'")
                    assert await JobRepository().claim(c, worker_id='installation-smoke', lock_seconds=30) is None
                if service == 'api':
                    await permission_denied(c, "SELECT * FROM ops.claim_job('unexpected',30)")
                if service == 'feishu':
                    await permission_denied(c, "SELECT security.target_scope_write('person',NULL,NULL,'sales')")
                    await verify_worker_role(c)
                    await permission_denied(c, 'SET ROLE salegent_feishu_worker')
            finally:
                await c.close()
        worker = Database(replace(settings, database_url=dsn('worker')))
        await worker.connect(); pools.append(worker)
        assert await Worker(worker).run_once() is False
        job = await owner.fetchval('''INSERT INTO ops.job(workspace_id,job_type,aggregate_type,aggregate_id,payload)
            VALUES($1,'installation.unknown','installation',$2,$3::jsonb) RETURNING id''',
            identity('A'), uuid.uuid4(), json.dumps({'workspace_id':str(identity('A')),
                'user_id':str(identity('ASALES')), 'role':'sales', 'data_scope':'self',
                'team_ids':[str(identity('A-sales'))]}))
        assert await Worker(worker).run_once() is True
        assert await owner.fetchval('SELECT status FROM ops.job WHERE id=$1', job) == 'dead_letter'
        sync_database = Database(replace(settings, database_url=dsn('feishu')))
        await sync_database.connect(); pools.append(sync_database)
        sync = FeishuWorker(sync_database)
        try: assert await sync.run_once() is False
        finally: await sync.close()
        with tempfile.TemporaryDirectory(prefix='salesbuddy-install-http-') as temporary:
            keyfile = Path(temporary)/'platform-keyring.json'
            keyfile.touch(mode=0o600)
            key = secrets.token_bytes(32); otp_seed = secrets.token_bytes(20)
            keyfile.write_text(json.dumps({'keys': {'test': base64.b64encode(key).decode()}}))
            cipher = PlatformMFACipher('test', {'test': key})
            await owner.execute('UPDATE control.operator SET mfa_secret=$2::jsonb WHERE id=$1',
                                operator, json.dumps(cipher.seal(str(operator), otp_seed)))
            settings = replace(settings, platform_mfa_keyring_file=str(keyfile), platform_mfa_key_id='test')
            with patch('sales_backend.main.get_settings', return_value=settings):
                async with app.router.lifespan_context(app):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://installation.local') as client:
                        sessions = []
                        previous_meeting_batch = None
                        for prefix in ('A', 'B'):
                            r = await client.post('/api/v1/auth/password/login', json={
                                'account_code': prefix+'SALES', 'password': account_password,
                                'workspace': 'local-'+prefix.lower()})
                            assert r.status_code == 200, (r.status_code, r.text)
                            headers = {'Authorization': 'Bearer '+r.json()['access_token']}
                            me = await client.get('/api/v1/auth/me', headers=headers)
                            assert me.status_code == 200, me.text
                            r = await client.get('/api/v1/customers', headers=headers)
                            assert r.status_code == 200, r.text
                            own = fixtures['companies'][0 if prefix == 'A' else 1]['customer_id']
                            other = fixtures['companies'][1 if prefix == 'A' else 0]['customer_id']
                            assert own in r.text and other not in r.text
                            r = await client.post('/api/v1/tasks', headers=headers, json={
                                'association_kind':'daily','description':'安装验证独立日常待办',
                                'target_position':'self','due_at':'2027-01-01T09:00:00+08:00'})
                            assert r.status_code == 201, (r.status_code, r.text)
                            task_id = r.json()['id']
                            choices = await client.get(f'/api/v1/tasks/{task_id}/reassignment-options', headers=headers)
                            assert choices.status_code == 200, (choices.status_code, choices.text)
                            recipients = choices.json()['items']
                            assert any(row['account_code'] == prefix+'SALES' for row in recipients)
                            assert all(row['account_code'].startswith(prefix) for row in recipients)
                            assert all(set(row) == {'id','account_code','name','role','role_name',
                                                    'team_id','team','team_ids'} for row in recipients)
                            # This installation fixture has no terminology override or product-sales department.
                            # Keep public role codes stable while checking the explicit default display contract.
                            role_names = {'sales':'一线销售','manager':'销售总经理','fde':'FDE',
                                          'operations':'运营','administrator':'系统管理员'}
                            assert all(row['role_name'] == role_names[row['role']] for row in recipients)
                            assert all(isinstance(row['team_ids'], list) and
                                       (row['team_id'] is None or row['team_id'] in row['team_ids']) for row in recipients)
                            r = await client.get('/api/v1/tasks', headers=headers)
                            assert r.status_code == 200 and '安装验证独立日常待办' in r.text, r.status_code
                            previous_meeting_batch = await meeting_import_http(
                                client, headers, prefix+'SALES', previous_meeting_batch)
                            sessions.append(headers)
                        for headers in sessions:
                            r = await client.post('/api/v1/auth/logout', headers=headers)
                            assert r.status_code == 204, r.text
                            r = await client.get('/api/v1/auth/me', headers=headers)
                            assert r.status_code == 401, r.text
                        r = await client.post('/api/v1/auth/password/login', json={
                            'account_code':'AADMIN','workspace':'local-a','password':account_password})
                        assert r.status_code == 200, r.status_code
                        headers = {'Authorization':'Bearer '+r.json()['access_token']}
                        admin_paths = [
                            'summary','companies','organization','password-policy','permissions/catalog',
                            'permissions/roles','permissions/options','permissions/me','permissions/audit',
                            'permissions/modules','permissions/delegations','support-access','support-access/directory','support-access/audit','customers','customers/create-options','claims','targets','targets/options',
                            'target-requests','target-batches','ai/overview','ai/calls','ai/rules','audit',
                            'system-events','feishu-sync','feishu-sync/catalog','feishu-sync/status',
                        ]
                        for path in admin_paths:
                            r = await client.get('/api/v1/console/'+path, headers=headers)
                            assert r.status_code == 200, (path, r.status_code, r.text)
                        customer_options = (await client.get('/api/v1/console/customers/create-options', headers=headers)).json()
                        assert customer_options['teams'] and all(set(row) == {'id','name'} for row in customer_options['teams'])
                        target_options = (await client.get('/api/v1/console/targets/options', headers=headers)).json()
                        assert any(row['id'] == str(identity('ASALES')) for row in target_options['people'])
                        assert all(row['id'] != str(identity('BSALES')) for row in target_options['people'])
                        created_target = await client.post('/api/v1/console/targets/batch', headers=headers, json={
                            'scope':'person', 'user_id':str(identity('ASALES')), 'department_code':'sales',
                            'period_type':'year', 'anchor_date':'2032-03-01', 'reason':'实际 API 权限安装验证',
                            'items':[{'kind':'collection','amount':'1234.56'}]})
                        assert created_target.status_code == 200, (created_target.status_code, created_target.text)
                        target_id = created_target.json()['items'][0]['id']
                        listed_targets = await client.get('/api/v1/console/targets?period_type=year&anchor_date=2032-03-01', headers=headers)
                        assert listed_targets.status_code == 200, (listed_targets.status_code, listed_targets.text)
                        target, = [row for row in listed_targets.json()['items'] if row['id'] == target_id]
                        assert target['can_manage'] and target['can_history']
                        target_history = await client.get(f'/api/v1/console/targets/{target_id}/history', headers=headers)
                        assert target_history.status_code == 200, (target_history.status_code, target_history.text)
                        assert target_history.json()['items']
                        r = await client.post('/api/v1/console/departments', headers=headers,
                                              json={'code':'installation','name':'安装验证部门','kind':'sales'})
                        assert r.status_code == 201, (r.status_code, r.text)
                        department = r.json()['id']
                        r = await client.post('/api/v1/console/accounts', headers=headers, json={
                            'account_code':'INSTALLUSER','display_name':'安装验证销售','temporary_password':account_password,
                            'roles':['sales'],'team_id':department})
                        assert r.status_code == 201, (r.status_code, r.text)
                        own = fixtures['companies'][0]['customer_id']
                        r = await client.get('/api/v1/console/customers/'+own, headers=headers)
                        assert r.status_code == 200, r.status_code
                        r = await client.patch('/api/v1/console/customers/'+own, headers=headers,
                            json={'version_no':r.json()['version_no'],'next_action':'安装验证后人工确认'})
                        assert r.status_code == 200, (r.status_code, r.text)
                        assert (await client.get('/api/v1/platform/tenants', headers=headers)).status_code == 401
                        assert (await client.get('/api/v1/platform/runtime', headers=headers)).status_code == 401
                        r = await client.post('/api/v1/platform/auth/login', headers={'Origin':'http://installation.local'},
                            json={'account_code':'INSTALLTEST','password':account_password,
                                  'otp':authenticator(otp_seed).generate(time.time()).decode()})
                        assert r.status_code == 200, r.status_code
                        assert (await client.get('/api/v1/platform/tenants')).status_code == 200
                        overview = await client.get('/api/v1/platform/runtime')
                        assert overview.status_code == 200, overview.text
                        assert len(overview.json()['items']) == 2
                        assert all(set(row)=={'workspace_id','name','company_code','status','jobs','sync','originals','calls'} for row in overview.json()['items'])
                        # Company-authorized support crosses only the explicit function boundary.
                        assert (await client.get('/api/v1/platform/support-access')).json()=={'items':[]}
                        from datetime import datetime, timedelta, timezone
                        support_body={'id':str(uuid.uuid4()),'operator_id':str(operator),'scopes':['customers'],
                            'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),'reason':'隔离安装验收'}
                        support_headers={**headers,'Origin':'http://installation.local'}
                        approved=await client.post('/api/v1/console/support-access',headers=support_headers,json=support_body)
                        assert approved.status_code==201,approved.text
                        path='/api/v1/platform/support-access/'+support_body['id']+'/records/customers'
                        read=await client.get(path)
                        assert read.status_code==200,read.text
                        assert fixtures['companies'][0]['customer_id'] in read.text and fixtures['companies'][1]['customer_id'] not in read.text
                        revoked=await client.post('/api/v1/console/support-access/'+support_body['id']+'/revoke',headers=support_headers,json={'reason':'验收结束'})
                        assert revoked.status_code==200,revoked.text
                        assert (await client.get(path)).status_code==403
                        r = await client.post('/api/v1/platform/tenants', headers={
                            'Origin':'http://installation.local', 'Idempotency-Key':str(uuid.uuid4())}, json={
                            'company_code':'install-third','name':'安装验证第三租户','purpose':'customer',
                            'administrator_account':'FIRSTADMIN','administrator_name':'安装验证首位管理员'})
                        assert r.status_code == 201, r.status_code
                        activation = r.json()['activation_token']
                        r = await client.post('/api/v1/platform/activate', headers={'Origin':'http://installation.local'}, json={
                            'company_code':'install-third','activation_token':activation,'new_password':account_password})
                        assert r.status_code == 200, r.status_code
                        # A platform cookie alone still has no company identity.
                        assert (await client.get('/api/v1/console/organization')).status_code == 401
        return {'password_login': 'passed', 'two_way_tenant_isolation': 'passed',
                'logout_revocation': 'passed', 'worker_empty_claim': 'passed',
                'temporary_support_read_revoke':'passed', 'platform_pool': 'passed', 'feishu_pool': 'passed', 'admin_read_routes': len(admin_paths),
                'department_account_customer_task_writes':'passed', 'platform_mfa_login':'passed',
                'tenant_provision_activation':'passed','worker_failure_receipt':'passed','feishu_empty_queue':'passed',
                'platform_runtime_metadata':'passed', 'business_form_options_actual_api':'passed',
                'target_list_manage_history_actual_api':'passed', 'task_reassignment_options_actual_api':'passed',
                'meeting_import_preview_patch_confirm_replay_exact_api':'passed',
                'target_scope_write_non_api_denied':3}
    finally:
        for pool in pools: await pool.close()


async def main(args):
    if args.port == 15432 or not 1 <= args.port <= 65535:
        raise ValueError('Use a disposable loopback PostgreSQL port, not the acceptance database')
    config = dict(host='127.0.0.1', port=args.port, user=args.user, command_timeout=300)
    admin = await asyncpg.connect(database='postgres', **config)
    suffix = uuid.uuid4().hex[:12]
    database = 'salesbuddy_install_roles_'+suffix
    neighbor = 'salesbuddy_install_neighbor_'+suffix
    roles = {s:'sb_install_'+suffix+'_'+s for s in ('api','worker','platform','feishu')}
    connection = None
    created = False
    neighbor_created = False
    try:
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0'); created = True
        connection = await asyncpg.connect(database=database, **config)
        await migrate(connection)
        await rejected(provision_new(connection, database='wrong_database', roles=roles))
        damaged = copy.deepcopy(load_policy())
        damaged['migration_fingerprint'] = '0'*64
        await rejected(provision_new(connection, database=database, roles=roles, policy=damaged))
        # A late SQL failure must undo created roles and ambient-ACL changes too.
        damaged = copy.deepcopy(load_policy())
        damaged['services']['feishu']['functions'].append('security.installation_missing_function()')
        try:
            await provision_new(connection, database=database, roles=roles, policy=damaged)
        except asyncpg.UndefinedFunctionError:
            pass
        else:
            raise AssertionError('Invalid grant unexpectedly provisioned')
        assert not await connection.fetchval('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=ANY($1::text[]))', list(roles.values()))
        passwords = await provision_new(connection, database=database, roles=roles)
        await verify(connection, roles)
        # PostgreSQL group names are cluster-wide. Identity-only membership must
        # not grant data access or SET ROLE even in another database with PUBLIC CONNECT.
        await admin.execute(f'CREATE DATABASE "{neighbor}" TEMPLATE template0'); neighbor_created = True
        other = await asyncpg.connect(database=neighbor, **config)
        try:
            await other.execute('CREATE TABLE public.synthetic_private(id integer); GRANT SELECT ON public.synthetic_private TO salegent_feishu_worker')
        finally: await other.close()
        login = await asyncpg.connect(host='127.0.0.1',port=args.port,database=neighbor,
                                      user=roles['feishu'],password=passwords['feishu'])
        try:
            await permission_denied(login, 'SELECT * FROM public.synthetic_private')
            await permission_denied(login, 'SET ROLE salegent_feishu_worker')
        finally: await login.close()
        await rejected(provision_new(connection, database=database, roles=roles))
        drifts = [
            f'GRANT SELECT ON platform.password_credential TO "{roles["api"]}"',
            f'GRANT UPDATE(display_name) ON platform.user_ref TO "{roles["worker"]}"',
            f'GRANT pg_read_all_data TO "{roles["api"]}"',
            f'ALTER ROLE "{roles["worker"]}" BYPASSRLS',
            'GRANT EXECUTE ON FUNCTION security.reconcile_runtime_grants() TO PUBLIC',
            f'GRANT CREATE ON SCHEMA public TO "{roles["api"]}"',
            f'REVOKE EXECUTE ON FUNCTION security.authorization_refresh() FROM "{roles["api"]}"',
            f'ALTER TABLE crm.customer OWNER TO "{roles["api"]}"',
            f'GRANT salegent_feishu_worker TO "{roles["feishu"]}" WITH INHERIT TRUE',
            f'GRANT salegent_feishu_worker TO "{roles["feishu"]}" WITH SET TRUE',
            'CREATE FUNCTION public.installation_drift() RETURNS integer LANGUAGE sql SECURITY DEFINER AS $$SELECT 1$$; GRANT EXECUTE ON FUNCTION public.installation_drift() TO PUBLIC',
            'ALTER TABLE crm.customer DISABLE ROW LEVEL SECURITY',
            'CREATE POLICY unexpected_install_access ON crm.customer USING (true)',
        ]
        for statement in drifts:
            tx = connection.transaction(); await tx.start()
            try:
                await connection.execute(statement)
                await rejected(verify(connection, roles))
            finally: await tx.rollback()
        result = await runtime_http(config, database, roles, passwords, connection)
        unused = {s: 'sb_unused_'+suffix+'_'+s for s in roles}
        await rejected(provision_new(connection, database=database, roles=unused))
        assert not await connection.fetchval('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=ANY($1::text[]))', list(unused.values()))
        await verify(connection, roles)
        print(json.dumps({'status':'passed', 'effective_runtime_roles':4, 'permission_drift_cases':len(drifts),
                          'fresh_install_guards':'passed', 'http':result,
                          'cross_database_worker_group':'denied',
                          'complete_installation_verified':False, 'production_changed':False}))
    finally:
        if connection: await connection.close()
        if created: await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        if neighbor_created: await admin.execute(f'DROP DATABASE "{neighbor}" WITH (FORCE)')
        for role in roles.values(): await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--user', required=True)
    asyncio.run(main(parser.parse_args()))
