"""Recover synthetic data on a NEW cluster with the source instance unavailable."""
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import secrets
import subprocess
import sys
import time
import uuid
import zipfile

import asyncpg
import httpx

from instance_backup import inventory
from instance_files import atomic_json, clean_env, read_private
from release_bundle import json_bytes
from sales_backend.security.platform_mfa import authenticator

ROOT = Path(__file__).resolve().parents[3]
EXPORT_PREFIX = '/api/v1/closed-company-export'


def check_closed_export_package(response, fixture, other_workspaces):
    assert response.status_code == 200, response.status_code
    assert hashlib.sha256(response.content).hexdigest() == response.headers['x-export-sha256']
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['workspace_id'] == fixture['workspace']
        rows = list(map(json.loads, archive.read('crm/customer.ndjson').splitlines()))
        assert [row['id'] for row in rows] == [fixture['customer']]
        for item in manifest['files']:
            content = archive.read(item['path'])
            assert hashlib.sha256(content).hexdigest() == item['sha256']
            assert all(other not in content.decode() for other in other_workspaces)


async def prepare_closed_export_recovery(maintenance, platform, origin, password, other_workspaces):
    """Keep the original two active companies; add one closed company via real APIs.

    The returned cookie/session snapshot stays only in this process, never in the
    public test receipt. Issuing a session via SQL would miss the deployed cookie
    contract and must not substitute for this regression.
    """
    assert (await maintenance.fetchval('SELECT current_database()')).startswith('sb_product_test_')
    assert await maintenance.fetchval("SELECT to_regclass('security.closed_export_session') IS NOT NULL")
    company = 'installer-closed-recovery'
    response = await platform.post('/api/v1/platform/tenants', headers={'Idempotency-Key':str(uuid.uuid4())}, json={
        'company_code':company, 'name':'Synthetic closed recovery company', 'purpose':'customer',
        'administrator_account':'CLOSEDADMIN', 'administrator_name':'Synthetic closed admin'})
    assert response.status_code == 201, response.status_code
    created = response.json()
    response = await platform.post('/api/v1/platform/activate', json={'company_code':company,
        'activation_token':created['activation_token'], 'new_password':password})
    assert response.status_code == 200
    workspace = created['id']
    for status in ('active', 'closed'):
        tenants = (await platform.get('/api/v1/platform/tenants')).json()['items']
        tenant = next(row for row in tenants if row['id'] == workspace)
        response = await platform.post('/api/v1/platform/tenants/'+workspace+'/state', json={
            'version_no':tenant['version_no'], 'status':status, 'reason':'Synthetic detached recovery acceptance'})
        assert response.status_code == 200, response.status_code
        if status == 'active':
            person = await maintenance.fetchval('SELECT id FROM platform.user_ref WHERE workspace_id=$1', uuid.UUID(workspace))
            customer = uuid.uuid4()
            await maintenance.execute('''INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id)
                VALUES($1,$2,'Synthetic closed recovery customer','synthetic-closed-recovery',$3)''',
                customer, uuid.UUID(workspace), person)
    fixture = {'workspace':workspace, 'company_code':company, 'account_code':'CLOSEDADMIN',
               'customer':str(customer), 'request':{'sections':['customers'], 'include_originals':False,
                    'reason':'Synthetic detached recovery closed-company export'}}
    async with httpx.AsyncClient(base_url=origin, headers={'Origin':origin}, trust_env=False, timeout=30) as browser:
        login = await browser.post(EXPORT_PREFIX+'/login', json={'company_code':company,
            'account_code':fixture['account_code'], 'password':password})
        assert login.status_code == 200, login.status_code
        assert login.json()['workspace_id'] == workspace
        fixture['session_id'] = login.json()['session_id']
        fixture['cookie'] = login.headers['set-cookie'].split(';', 1)[0]
        assert fixture['cookie'].startswith('sales_closed_export=')
        assert (await browser.get(EXPORT_PREFIX+'/session')).status_code == 200
        assert (await browser.get(EXPORT_PREFIX+'/options')).status_code == 200
        check_closed_export_package(await browser.post(EXPORT_PREFIX+'/export', json=fixture['request']),
                                    fixture, other_workspaces)
        from verify_closed_company_export import recorded_actions
        await recorded_actions(maintenance, fixture['session_id'], {'login':1, 'prepared':1, 'sent':1})
    fixture['session_before'] = dict(await maintenance.fetchrow(
        'SELECT * FROM security.closed_export_session WHERE id=$1', uuid.UUID(fixture['session_id'])))
    assert fixture['session_before']['revoked_at'] is None
    assert await maintenance.fetchval('SELECT expires_at>clock_timestamp() FROM security.closed_export_session WHERE id=$1',
                                     uuid.UUID(fixture['session_id']))
    fixture['events_before'] = [dict(row) for row in await maintenance.fetch(
        'SELECT * FROM security.closed_export_event WHERE workspace_id=$1 ORDER BY id', uuid.UUID(workspace))]
    return fixture


async def check_closed_export_recovery_rows(connection, fixture):
    if fixture is None:
        assert await connection.fetchval("SELECT to_regclass('security.closed_export_session') IS NULL")
        return
    row = await connection.fetchrow('SELECT * FROM security.closed_export_session WHERE id=$1',
                                    uuid.UUID(fixture['session_id']))
    assert row is not None and row['revoked_at'] is not None
    assert {**dict(row), 'revoked_at':None} == fixture['session_before']
    assert await connection.fetchval('SELECT count(*) FROM security.closed_export_session WHERE revoked_at IS NULL') == 0
    events = [dict(item) for item in await connection.fetch(
        'SELECT * FROM security.closed_export_event WHERE workspace_id=$1 ORDER BY id', uuid.UUID(fixture['workspace']))]
    assert events == fixture['events_before']


async def check_closed_export_recovery_http(origin, connection, fixture, password, other_workspaces):
    if fixture is None:
        return {'status':'not_applicable', 'reason':'Historical source schema has no closed-export session table',
                'legacy_table_absence_checked':True}
    # Prove revocation, not an accidental 15-minute expiry during a slow test.
    assert await connection.fetchval('SELECT expires_at>clock_timestamp() FROM security.closed_export_session WHERE id=$1',
                                    uuid.UUID(fixture['session_id']))
    async with httpx.AsyncClient(base_url=origin, headers={'Origin':origin, 'Cookie':fixture['cookie']},
                                 trust_env=False, timeout=30) as old:
        for path in ('session', 'options'):
            assert (await old.get(EXPORT_PREFIX+'/'+path)).status_code == 401
        assert (await old.post(EXPORT_PREFIX+'/export', json=fixture['request'])).status_code == 401
    async with httpx.AsyncClient(base_url=origin, headers={'Origin':origin}, trust_env=False, timeout=30) as browser:
        login = await browser.post(EXPORT_PREFIX+'/login', json={'company_code':fixture['company_code'],
            'account_code':fixture['account_code'], 'password':password})
        assert login.status_code == 200, login.status_code
        assert login.json()['workspace_id'] == fixture['workspace']
        assert login.json()['session_id'] != fixture['session_id']
        assert (await browser.get(EXPORT_PREFIX+'/session')).status_code == 200
        assert (await browser.get(EXPORT_PREFIX+'/options')).status_code == 200
        check_closed_export_package(await browser.post(EXPORT_PREFIX+'/export', json=fixture['request']),
                                    fixture, other_workspaces)
        from verify_closed_company_export import recorded_actions
        await recorded_actions(connection, login.json()['session_id'], {'login':1, 'prepared':1, 'sent':1})
        assert (await browser.post(EXPORT_PREFIX+'/logout')).status_code == 200
    assert await connection.fetchval('SELECT status FROM platform.workspace WHERE id=$1',
                                     uuid.UUID(fixture['workspace'])) == 'closed'
    return {'status':'passed', 'source_session_issued_by_real_api':True,
            'source_cookie_could_export_before_backup':True, 'old_unexpired_cookie_rejected':True,
            'session_options_and_export_rejected':True, 'session_and_old_audit_rows_preserved':True,
            'new_password_login_and_export':True, 'customer_and_archive_hash_verified':True,
            'company_remains_closed':True}


def run(root,command,*,expect=True,password=None,contains=None):
    env = clean_env()
    env['PYTHONPYCACHEPREFIX'] = str(root.parent/'recovery-pycache')
    if password is not None: env['SALESBUDDY_INSTALL_DB_PASSWORD'] = password
    result = subprocess.run(command,capture_output=True,text=True,env=env,timeout=600)
    descriptor = os.open(root.parent/'recovery-driver.log',os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
    with os.fdopen(descriptor,'a') as stream:
        stream.write('exit='+str(result.returncode)+'\n'+result.stdout+result.stderr+'\n')
    if expect and result.returncode: raise AssertionError('Recovery fixture failed; private diagnostics: '+str(root.parent))
    if not expect:
        assert result.returncode,'Unsafe recovery unexpectedly accepted'
        if contains: assert contains in result.stderr,'Recovery refused for the wrong reason'
        return None
    return result.stdout


async def exercise(root,pg_bin,command,backup,tokens,refresh_tokens,customers,password,platform_password,mfa_seed,platform_cookie,
                   *,closed_export=None):
    command(root,'stop','--root',str(root))
    detached = root.parent/'detached-backup'
    shutil.copytree(Path(backup['backup']),detached)
    trusted = hashlib.sha256((detached/'BACKUP.json').read_bytes()).hexdigest()
    hidden = root.with_name('source-unavailable')
    root.rename(hidden)
    recovered = root.parent/'recovered-instance'
    cluster = root.parent/'recovery-cluster'
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0)); port = probe.getsockname()[1]
    assert port not in {15432,15433}
    owner = 'recovery_test_owner'
    maintenance_password = secrets.token_urlsafe(40)
    original_maintenance = os.environ.get('SALESBUDDY_INSTALL_DB_PASSWORD')
    password_file = root.parent/'recovery-maintenance-password'
    descriptor = os.open(password_file,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(descriptor,'w') as stream: stream.write(maintenance_password+'\n')
    database = 'sb_recovered_'+uuid.uuid4().hex[:16]
    admin = conn = None; started = False; restored = False
    try:
        run(recovered,[str(pg_bin/'initdb'),'-D',str(cluster),'-U',owner,'--auth=scram-sha-256',
                       '--pwfile',str(password_file),'--no-locale','--encoding=UTF8'])
        run(recovered,[str(pg_bin/'pg_ctl'),'-D',str(cluster),'-l',str(root.parent/'recovery-postgres.log'),
            '-o',f"-h 127.0.0.1 -p {port} -c unix_socket_directories=''",'-w','start'])
        started = True
        admin = await asyncpg.connect(host='127.0.0.1',port=port,user=owner,database='postgres',password=maintenance_password)
        restore = [sys.executable,str(ROOT/'tools/installation/instance_restore.py'),'restore',
            '--root',str(recovered),'--backup',str(detached),'--backup-sha256',trusted,
            '--db-host','127.0.0.1','--db-port',str(port),'--db-user',owner,'--db-sslmode','disable',
            '--database',database,'--pg-bin',str(pg_bin)]
        wrong = restore.copy(); wrong[wrong.index('--backup-sha256')+1] = 'f'*64
        run(recovered,wrong,expect=False,password=maintenance_password); assert not recovered.exists()
        # Existing empty DB is never adopted. Keep the resulting private journal
        # as refusal evidence; use a different fresh root for the real recovery.
        await admin.execute('CREATE DATABASE '+database)
        run(recovered,restore,expect=False,password=maintenance_password)
        assert await admin.fetchval("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=$1",database) is None
        await admin.execute('DROP DATABASE '+database)
        # Historical package source_check also rejects the recovery sentinel;
        # it cannot start an unverified instance even before recovery existed.
        refused = recovered
        guard = run(refused,[sys.executable,'-c',
            'import sys;sys.path.insert(0,sys.argv[1]);from instance_files import source_check;source_check(__import__("pathlib").Path(sys.argv[2]))',
            str(refused/'release/tools/installation'),str(refused)],expect=False,contains='Installed source differs')
        assert guard is None
        recovered.rename(root.parent/'refused-existing-db')
        result = json.loads(run(recovered,restore,password=maintenance_password)); restored = True
        assert result['status'] == 'passed' and result['services_started'] is False
        assert result['source_sessions_revoked'] is True and not root.exists()
        for service in ('api','worker','feishu'): assert not (recovered/'run'/f'{service}.json').exists()
        metadata = read_private(recovered/'INSTANCE.json')
        original = read_private(detached/'INSTANCE.json')
        assert metadata['instance_id'] != original['instance_id']
        assert not set(metadata['roles'].values()) & set(original['roles'].values())
        interruptions = []
        legacy_closed_refused = None
        for phase in ('restoring_data','database_verified'):
            interrupted = root.parent/('interrupted-'+phase)
            parameters = restore[2:]
            parameters[parameters.index('--root')+1] = str(interrupted)
            parameters[parameters.index('--database')+1] = database+'_'+('a' if phase == 'restoring_data' else 'b')
            # Fault injection exists only in this test process, not the product.
            wrapper = '''import os,sys
sys.path.insert(0,sys.argv[1])
import instance_restore as restore
phase=sys.argv[2]; original=restore.atomic_json
def checkpoint(path,value,**kwargs):
 original(path,value,**kwargs)
 if path.name=='recovery.json' and value.get('stage')==phase: os._exit(75)
restore.atomic_json=checkpoint
sys.argv=[sys.argv[0],*sys.argv[3:]]
restore.main()
'''
            run(interrupted,[sys.executable,'-c',wrapper,str(ROOT/'tools/installation'),phase,*parameters],
                expect=False,password=maintenance_password)
            state = read_private(interrupted/'run/recovery.json')
            assert state['stage'] == phase and not (interrupted/'run/initialized.json').exists()
            identity_before = read_private(interrupted/'INSTANCE.json')
            restarted = json.loads(run(interrupted,[sys.executable,str(ROOT/'tools/installation/instance_restore.py'),
                'resume','--root',str(interrupted),'--pg-bin',str(pg_bin)],password=maintenance_password))
            assert restarted['status'] == 'passed' and not restarted['services_started']
            assert read_private(interrupted/'INSTANCE.json') == identity_before
            assert not (interrupted/'run/recovery.json').exists()
            interrupted_connection = await asyncpg.connect(host='127.0.0.1', port=port, user=owner,
                database=identity_before['database']['name'], password=maintenance_password)
            try:
                await check_closed_export_recovery_rows(interrupted_connection, closed_export)
            finally:
                await interrupted_connection.close()
            interruptions.append(phase)
        if closed_export is not None:
            # Separate negative fixture: emulate a pre-fix tool which committed
            # a correct inventory while leaving its closed-export capability
            # active. Never alter the successful or ordinary crash-resume DBs.
            legacy = root.parent/'interrupted-legacy-closed-session'
            parameters = restore[2:]
            parameters[parameters.index('--root')+1] = str(legacy)
            legacy_database = database+'_legacy'
            parameters[parameters.index('--database')+1] = legacy_database
            run(legacy,[sys.executable,'-c',wrapper,str(ROOT/'tools/installation'),'database_verified',*parameters],
                expect=False,password=maintenance_password)
            legacy_state = read_private(legacy/'run/recovery.json')
            assert legacy_state['stage'] == 'database_verified' and not (legacy/'run/initialized.json').exists()
            legacy_connection = await asyncpg.connect(host='127.0.0.1',port=port,user=owner,
                database=legacy_database,password=maintenance_password)
            try:
                await check_closed_export_recovery_rows(legacy_connection, closed_export)
                async with legacy_connection.transaction(isolation='repeatable_read'):
                    assert await legacy_connection.execute('UPDATE security.closed_export_session SET revoked_at=NULL WHERE id=$1',
                        uuid.UUID(closed_export['session_id'])) == 'UPDATE 1'
                    legacy_inventory = await inventory(legacy_connection)
                legacy_state['post_inventory_sha256'] = hashlib.sha256(json_bytes(legacy_inventory)).hexdigest()
                atomic_json(legacy/'run/recovery.json', legacy_state)
                marker_before = await admin.fetchrow("SELECT oid,shobj_description(oid,'pg_database') AS marker FROM pg_database WHERE datname=$1",
                                                     legacy_database)
                run(legacy,[sys.executable,str(ROOT/'tools/installation/instance_restore.py'),
                    'resume','--root',str(legacy),'--pg-bin',str(pg_bin)],expect=False,password=maintenance_password,
                    contains='Restored source sessions were not revoked (security.closed_export_session)')
                assert await admin.fetchrow("SELECT oid,shobj_description(oid,'pg_database') AS marker FROM pg_database WHERE datname=$1",
                                            legacy_database) == marker_before
                async with legacy_connection.transaction(isolation='repeatable_read',readonly=True):
                    assert await inventory(legacy_connection) == legacy_inventory
                refused_state = read_private(legacy/'run/recovery.json')
                assert refused_state['post_inventory_sha256'] == legacy_state['post_inventory_sha256']
                assert refused_state['last_attempt_failed'] is True
                assert not (legacy/'run/initialized.json').exists() and (legacy/'release/RECOVERY_PENDING').is_file()
                for service in ('api','worker','feishu'): assert not (legacy/'run'/f'{service}.json').exists()
                legacy_closed_refused = True
            finally:
                await legacy_connection.close()
        conn = await asyncpg.connect(host='127.0.0.1',port=port,user=owner,database=database,password=maintenance_password)
        assert await conn.fetchval("SELECT count(*) FROM platform.auth_session WHERE status<>'revoked'") == 0
        assert await conn.fetchval('SELECT count(*) FROM control.session WHERE revoked_at IS NULL') == 0
        assert await conn.fetchval('SELECT count(*) FROM platform.workspace') == 2 + bool(closed_export)
        await check_closed_export_recovery_rows(conn, closed_export)
        pending = await conn.fetchrow("SELECT * FROM control.storage_allocation WHERE state='reserved' AND driver='local'")
        assert pending and pending['storage_key'] == pending['staging_path']
        pending_path = Path(pending['staging_path'])
        assert pending_path.parent == recovered/'materials'
        assert hashlib.sha256(pending_path.read_bytes()).hexdigest() == pending['content_sha256']
        os.environ['SALESBUDDY_INSTALL_DB_PASSWORD'] = maintenance_password
        assert command(recovered,'database-check','--root',str(recovered))['database_verified']
        # Known isolated fixture has no externally configured jobs/connections.
        # Recovery itself did not start any service; explicit acceptance does.
        command(recovered,'start','--root',str(recovered),'--feishu')
        origin = 'http://127.0.0.1:'+str(metadata['api_port'])
        closed_result = await check_closed_export_recovery_http(origin, conn, closed_export, password,
                                                               [workspace for workspace,_ in tokens])
        async with httpx.AsyncClient(base_url=origin,trust_env=False,headers={'Origin':origin},timeout=30) as client:
            for (_,token),refresh in zip(tokens,refresh_tokens,strict=True):
                assert (await client.get('/api/v1/console/customers',headers={'Authorization':'Bearer '+token})).status_code == 401
                assert (await client.post('/api/v1/auth/refresh',json={'refresh_token':refresh})).status_code == 401
            for index,(workspace,_) in enumerate(tokens):
                login = await client.post('/api/v1/auth/password/login',json={'account_code':'FIRSTADMIN',
                    'password':password,'workspace':'installer-'+('a' if index == 0 else 'b'),'role':'administrator'})
                assert login.status_code == 200
                auth = {'Authorization':'Bearer '+login.json()['access_token']}
                rows = (await client.get('/api/v1/console/customers',headers=auth)).json()['items']
                assert [row['id'] for row in rows] == [customers[workspace]]
                other = next(value for key,value in customers.items() if key != workspace)
                assert (await client.get('/api/v1/console/customers/'+other,headers=auth)).status_code == 404
            # Avoid reusing the same TOTP step used by source enrollment.
            last_step = await conn.fetchval('SELECT mfa_last_step FROM control.operator WHERE account_code=\'INSTALLTEST\'')
            while int(time.time())//30 <= last_step: await asyncio.sleep(.5)
            code = authenticator(mfa_seed).generate(time.time()).decode()
            login = await client.post('/api/v1/platform/auth/login',json={'account_code':'INSTALLTEST','password':platform_password,'otp':code})
            assert login.status_code == 200 and login.json()['operator']['mfa_enrolled'] is True
            if platform_cookie:
                name = login.headers['set-cookie'].split('=',1)[0]
                old = await client.get('/api/v1/platform/tenants',headers={'Cookie':name+'='+platform_cookie})
                assert old.status_code == 401
            assert len((await client.get('/api/v1/platform/tenants')).json()['items']) == 2 + bool(closed_export)
        return {**result,'source_path_unavailable':True,'new_postgresql_cluster':True,
            'different_maintenance_owner':True,'existing_database_refused':True,'manifest_tamper_refused':True,
            'runtime_connections_authenticated_with_scram':True,'old_artifact_recovery_guard':'passed',
            'interruption_resume':interruptions,'pending_upload_path_and_bytes_preserved':True,
            'old_access_refresh_and_platform_sessions_refused':True,'real_login_and_mfa':'passed',
            'closed_company_export_recovery':closed_result,
            'legacy_committed_active_closed_session_refused':legacy_closed_refused,
            'bidirectional_tenant_isolation':'passed','service_start_was_explicit_after_recovery':True}
    finally:
        if restored:
            command(recovered,'stop','--root',str(recovered))
        if conn: await conn.close()
        if admin: await admin.close()
        if started:
            run(recovered,[str(pg_bin/'pg_ctl'),'-D',str(cluster),'-m','fast','-w','stop'])
        if original_maintenance is None: os.environ.pop('SALESBUDDY_INSTALL_DB_PASSWORD',None)
        else: os.environ['SALESBUDDY_INSTALL_DB_PASSWORD'] = original_maintenance
        hidden.rename(root)
