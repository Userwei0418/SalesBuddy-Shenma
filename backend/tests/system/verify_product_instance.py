"""Install a committed artifact into a disposable DB and exercise real TCP services.

All accounts are synthetic. No external model/Feishu calls, production paths or
acceptance databases are allowed. Removes only this run's marked DB and roles.
"""
import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import asyncpg
import httpx

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'tools/installation'), str(ROOT/'backend/src')]
from instance_files import read_private
from release_bundle import committed_files, prepare, write_archive
from sales_backend.security.platform_mfa import authenticator


def assert_status(response, expected):
    """Report the failed boundary without logging passwords, cookies or payloads."""
    if response.status_code == expected:
        return
    try:
        payload = response.json()
        detail = payload.get('detail') if isinstance(payload, dict) else None
    except ValueError:
        detail = None
    code = detail if isinstance(detail, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', detail) else 'unavailable'
    raise AssertionError(f'{response.request.method} {response.request.url.path}: '
                         f'HTTP {response.status_code}, expected {expected}, error_code={code}')


def forward_proxy_platform_cookie(client, response):
    # The loopback HTTP transport represents a trusted TLS proxy. Forward only
    # the platform cookie just issued by this response, including after MFA.
    cookies = [cookie for cookie in response.cookies.jar
               if cookie.name == 'sales_platform_session' and cookie.path == '/api/v1/platform']
    assert len(cookies) == 1 and cookies[0].secure, 'Expected one Secure platform session cookie'
    client.headers['Cookie'] = cookies[0].name+'='+cookies[0].value


def command(root, *args, expect=True):
    result = subprocess.run([sys.executable, str(ROOT/'tools/installation/instance.py'), *args],
        capture_output=True, text=True, timeout=600)
    descriptor = os.open(root.parent/'test-driver.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, 'a') as stream:
        stream.write(args[0]+' exit='+str(result.returncode)+'\n'+result.stdout+result.stderr+'\n')
    # Never echo a private setup log or credentials into the test report.
    if expect and result.returncode:
        raise AssertionError(f'Instance command {args[0]} failed; private fixture retained at {root}')
    if not expect:
        assert result.returncode != 0, 'Unsafe operation unexpectedly succeeded'
        return None
    return json.loads(result.stdout)


async def main(args):
    if args.port == 15432 or not 1 <= args.port <= 65535:
        raise ValueError('Use a disposable loopback PostgreSQL, never the persistent acceptance cluster')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); api_port = probe.getsockname()[1]
    fixture = Path(tempfile.mkdtemp(prefix='salesbuddy-instance-test-')).resolve()
    root = fixture/'instance'
    database = 'sb_product_test_'+uuid.uuid4().hex[:16]
    admin = await asyncpg.connect(host='127.0.0.1', port=args.port, user=args.user, database='postgres',
        password=os.environ.get('SALESBUDDY_INSTALL_DB_PASSWORD'), command_timeout=300)
    conn = None; data = None; passed = False
    try:
        revision, files, modes = committed_files(ROOT, args.upgrade_from or args.recovery_from)
        api_target = 'https://instance.example.test/api/v1' if args.private_proxy else f'http://127.0.0.1:{api_port}/api/v1'
        packaged, modes, manifest = prepare(files, modes, revision=revision,
            api_url=api_target, appid='wx0123456789abcdef',
            profile='private' if args.private_proxy else 'local', product_name='instance-system-test')
        archive = fixture/'candidate.zip'
        digest = write_archive(packaged, modes, archive)
        prepare_args = ['prepare', '--root', str(root), '--archive', str(archive), '--sha256', digest,
            '--database', database, '--db-host', '127.0.0.1', '--db-port', str(args.port), '--db-user', args.user,
            '--db-sslmode', 'disable', '--api-port', str(api_port), '--operator', 'INSTALLTEST',
            '--operator-name', 'Synthetic installation operator']
        if args.private_proxy: prepare_args.append('--trust-loopback-proxy')
        assert command(root, *prepare_args)['database_created'] is False
        data = read_private(root/'INSTANCE.json')
        credentials = (root/'private/bootstrap.json').read_bytes()
        command(root, *prepare_args, expect=False)
        assert (root/'private/bootstrap.json').read_bytes() == credentials
        # Foreign DB is not adopted, even if it is empty. The failed attempt
        # keeps its durable credentials and can resume after the fixture is removed.
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0')
        command(root, 'setup', '--root', str(root), expect=False)
        assert not await admin.fetchval('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=ANY($1::text[]))', list(data['roles'].values()))
        await admin.execute(f'DROP DATABASE "{database}"')
        assert command(root, 'setup', '--root', str(root))['database_verified']
        conn = await asyncpg.connect(host='127.0.0.1', port=args.port, user=args.user, database=database,
            password=os.environ.get('SALESBUDDY_INSTALL_DB_PASSWORD'), command_timeout=300)
        assert await conn.fetchval('SELECT count(*) FROM platform.workspace') == 0
        assert await conn.fetchval('SELECT count(*) FROM control.operator') == 1
        before_operator = dict(await conn.fetchrow('SELECT id,password_hash,mfa_secret FROM control.operator'))
        before_roles = dict(await conn.fetch('SELECT rolname,rolpassword FROM pg_authid WHERE rolname=ANY($1::text[])', list(data['roles'].values())))
        # Lost local completion receipt after a DB commit resumes without resets.
        (root/'run/initialized.json').unlink()
        command(root, 'setup', '--root', str(root))
        assert before_operator == dict(await conn.fetchrow('SELECT id,password_hash,mfa_secret FROM control.operator'))
        assert before_roles == dict(await conn.fetch('SELECT rolname,rolpassword FROM pg_authid WHERE rolname=ANY($1::text[])', list(data['roles'].values())))
        started = command(root, 'start', '--root', str(root), '--feishu')
        assert started['status'] == 'matched'
        assert started['database_ledger_checked'] is True
        assert started['readiness']['checks']['model_gateway_configured'] is False
        pids = {service: read_private(root/'run'/f'{service}.json')['pid'] for service in ('api','worker','feishu')}
        command(root, 'start', '--root', str(root), '--feishu')
        assert pids == {service: read_private(root/'run'/f'{service}.json')['pid'] for service in pids}
        monitoring_result = None
        if not (args.upgrade_from or args.recovery_from):
            # Current candidates must include the monitor. Explicit historical
            # upgrade/recovery inputs retain their original reviewed toolset.
            assert 'tools/installation/instance_monitor.py' in files
            from verify_instance_monitor import verify_running
            monitoring_result = verify_running(root)
        command(root, 'setup', '--root', str(root), expect=False)
        origin = f'http://127.0.0.1:{api_port}'
        password = read_private(root/'private/bootstrap.json')['operator_password']
        account_password = secrets.token_urlsafe(30)
        public_origin = 'https://instance.example.test' if args.private_proxy else origin
        headers = {'Origin':public_origin}
        if args.private_proxy:
            headers.update({'X-Forwarded-Proto':'https', 'Host':'instance.example.test'})
        closed_recovery = None
        async with httpx.AsyncClient(base_url=origin, trust_env=False, timeout=30, headers=headers) as client:
            assert (await client.get('/platform')).status_code == 200
            assert (await client.get('/admin')).status_code == 200
            result = await client.post('/api/v1/platform/auth/login', json={'account_code':'INSTALLTEST','password':password})
            assert_status(result, 200)
            if args.private_proxy:
                forward_proxy_platform_cookie(client, result)
            restricted_cookie = client.cookies.get('sales_platform_session')
            assert restricted_cookie, 'Login did not issue a platform session'
            assert result.json()['operator']['mfa_enrolled'] is False
            assert_status(await client.get('/api/v1/platform/tenants'), 403)
            enrollment = await client.post('/api/v1/platform/auth/mfa/enroll')
            assert_status(enrollment, 200)
            seed = base64.b32decode(enrollment.json()['secret'])
            code = authenticator(seed).generate(time.time()).decode()
            confirmed = await client.post('/api/v1/platform/auth/mfa/confirm', json={'otp':code})
            assert_status(confirmed, 200)
            # Explicit historical upgrade/recovery fixtures retain their old
            # session contract; V189+ must revoke the restricted proof and rotate.
            if int(manifest['expected_schema'][1:]) >= 189:
                assert confirmed.json()['operator']['mfa_verified'] is True
                assert confirmed.cookies.get('sales_platform_session') not in (None, restricted_cookie)
                if args.private_proxy:
                    forward_proxy_platform_cookie(client, confirmed)
                assert_status(await client.get('/api/v1/platform/tenants', headers={
                    'Cookie':'sales_platform_session='+restricted_cookie}), 401)
            tenants_response = await client.get('/api/v1/platform/tenants')
            assert_status(tenants_response, 200)
            assert tenants_response.json()['items'] == []
            tokens = []
            refresh_tokens = []
            platform_cookie = next((v for k,v in client.cookies.items() if 'platform' in k),None)
            for suffix in ('a', 'b'):
                company = 'installer-'+suffix
                result = await client.post('/api/v1/platform/tenants', headers={'Idempotency-Key':str(uuid.uuid4())}, json={
                    'company_code':company,'name':'Synthetic '+suffix,'purpose':'customer',
                    'administrator_account':'FIRSTADMIN','administrator_name':'Synthetic admin'})
                assert result.status_code == 201, result.status_code
                row = result.json()
                result = await client.post('/api/v1/platform/activate', json={'company_code':company,
                    'activation_token':row['activation_token'],'new_password':account_password})
                assert result.status_code == 200
                tenants = (await client.get('/api/v1/platform/tenants')).json()['items']
                tenant = next(t for t in tenants if t['id'] == row['id'])
                result = await client.post('/api/v1/platform/tenants/'+row['id']+'/state', json={
                    'version_no':tenant['version_no'],'status':'active','reason':'Synthetic installation acceptance'})
                assert result.status_code == 200
                result = await client.post('/api/v1/auth/password/login', json={
                    'account_code': 'FIRSTADMIN', 'password': account_password, 'workspace':company, 'role':'administrator'})
                assert result.status_code == 200, result.status_code
                tokens.append((row['id'], result.json()['access_token']))
                refresh_tokens.append(result.json()['refresh_token'])
            assert tokens[0][1] != tokens[1][1]
            customers = {}
            for workspace, _ in tokens:
                customer = uuid.uuid4(); customers[workspace] = str(customer)
                creator = await conn.fetchval('SELECT id FROM platform.user_ref WHERE workspace_id=$1 LIMIT 1', uuid.UUID(workspace))
                await conn.execute('''INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id)
                    VALUES($1,$2,'Synthetic shared customer name','synthetic-shared-name',$3)''', customer, uuid.UUID(workspace), creator)
            for workspace, token in tokens:
                result = await client.get('/api/v1/console/organization', headers={'Authorization':'Bearer '+token})
                assert result.status_code == 200
                result = await client.get('/api/v1/console/customers', headers={'Authorization':'Bearer '+token})
                assert result.status_code == 200
                assert [item['id'] for item in result.json()['items']] == [customers[workspace]]
                other = next(value for key, value in customers.items() if key != workspace)
                result = await client.get('/api/v1/console/customers/'+other, headers={'Authorization':'Bearer '+token})
                assert result.status_code == 404
            # An actual background process claims and fails a synthetic unknown
            # job deterministically, proving worker liveness beyond PID existence.
            workspace = uuid.UUID(tokens[0][0])
            user = await conn.fetchval('SELECT id FROM platform.user_ref WHERE workspace_id=$1 LIMIT 1', workspace)
            job = await conn.fetchval('''INSERT INTO ops.job(workspace_id,job_type,aggregate_type,aggregate_id,payload)
                VALUES($1,'installation.unknown','installation',$2,$3::jsonb) RETURNING id''', workspace, uuid.uuid4(),
                json.dumps({'workspace_id':str(workspace),'user_id':str(user),'role':'administrator','data_scope':'workspace','team_ids':[]}))
            deadline = time.monotonic()+20
            while time.monotonic() < deadline:
                if await conn.fetchval('SELECT status FROM ops.job WHERE id=$1', job) == 'dead_letter': break
                await asyncio.sleep(.2)
            else: raise AssertionError('Real worker did not produce a failure receipt')
            # Application writes and platform enrollment survive process restarts
            # and replayed setup. Credentials must not be silently regenerated.
            after_operator = dict(await conn.fetchrow('SELECT id,password_hash,mfa_secret FROM control.operator'))
            assert command(root, 'stop', '--root', str(root))['database_and_files_retained']
            # A stale receipt or health version cannot authorize starting against
            # a database whose actual migration ledger has drifted.
            checksum = await conn.fetchval("SELECT checksum_sha256 FROM ops.migration_checksum WHERE migration_key='V166'")
            await conn.execute("UPDATE ops.migration_checksum SET checksum_sha256=$1 WHERE migration_key='V166'", '0'*64)
            command(root, 'start', '--root', str(root), expect=False)
            await conn.execute("UPDATE ops.migration_checksum SET checksum_sha256=$1 WHERE migration_key='V166'", checksum)
            command(root, 'setup', '--root', str(root))
            assert after_operator == dict(await conn.fetchrow('SELECT id,password_hash,mfa_secret FROM control.operator'))
            assert (root/'private/bootstrap.json').read_bytes() == credentials
            command(root, 'start', '--root', str(root), '--feishu')
            assert (await client.get('/api/v1/platform/tenants')).status_code == 200
            assert len((await client.get('/api/v1/platform/tenants')).json()['items']) == 2
            assert await conn.fetchval('SELECT count(*) FROM ops.job WHERE id=$1', job) == 1
            command(root, 'check', '--root', str(root), '--feishu')
            demo_result = None
            if args.demo_template:
                from verify_demo_template import exercise as demo_exercise
                demo_result = await demo_exercise(root, conn, client, origin, command, args, customers)
            if args.disaster_recovery_pg_bin and int(manifest['expected_schema'][1:]) >= 180:
                from verify_instance_restore import prepare_closed_export_recovery
                closed_recovery = await prepare_closed_export_recovery(conn, client, origin, account_password,
                                                                       [workspace for workspace,_ in tokens])
        export_result = None
        closed_export_result = None
        if args.tenant_export:
            from verify_tenant_export import exercise as export_exercise
            export_result = await export_exercise(root, conn, origin, headers, tokens, customers)
            from verify_closed_company_export import exercise as closed_export_exercise
            closed_export_result = await closed_export_exercise(root, conn, origin, headers, tokens,
                customers, account_password, client.cookies)
        backup_result = None
        if args.backup_pg_bin or args.upgrade_from or args.disaster_recovery_pg_bin:
            from verify_instance_backup import exercise
            backup_result = await exercise(root, conn, args.backup_pg_bin or args.upgrade_pg_bin or args.disaster_recovery_pg_bin, command,
                                           include_pending=bool(args.disaster_recovery_pg_bin))
        disaster_result = None
        if args.disaster_recovery_pg_bin:
            from verify_instance_restore import exercise as restore_exercise
            disaster_result = await restore_exercise(root,args.disaster_recovery_pg_bin,command,backup_result,
                tokens,refresh_tokens,customers,account_password,password,seed,platform_cookie,
                closed_export=closed_recovery)
        upgrade_result = None
        if args.upgrade_from:
            from verify_instance_upgrade import exercise as upgrade_exercise
            if conn and not conn.is_closed(): await conn.close()
            upgrade_result = await upgrade_exercise(root, args, command, tokens, customers, account_password)
        passed = True
        return {'status':'passed', 'revision':revision, 'schema':manifest['expected_schema'],
            'target_profile':manifest['profile'], 'proxy_headers_only':args.private_proxy, 'actual_database_drift_refused':'passed',
            'empty_install':'passed', 'foreign_database_refused':'passed', 'uncertain_commit_resume':'passed',
            'service_processes':3, 'duplicate_start':'no new processes', 'platform_mfa_enrollment':'passed',
            'tenant_provision_activation_login':2, 'bidirectional_customer_isolation':'passed', 'worker_failure_receipt':'passed',
            'restart_and_setup_preserve_data_credentials_mfa':'passed', 'integrations':'unconfigured; no external calls',
            'production_changed':False, 'fixture':str(fixture), 'backup_and_restore':backup_result,
            'upgrade':upgrade_result, 'tenant_export':export_result, 'closed_company_export':closed_export_result,
            'demo_template':demo_result,
            'disaster_recovery':disaster_result, 'monitoring':monitoring_result}
    finally:
        try:
            try:
                if data: command(root, 'stop', '--root', str(root))
            finally:
                if conn: await conn.close()
                marker = await admin.fetchval("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=$1", database)
                if data and marker == 'salesbuddy-instance-v1:'+data['instance_id']:
                    await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
                    for role in data['roles'].values(): await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        finally:
            await admin.close()
        if passed:
            # Keep files and private logs locally as test evidence, but no services
            # or databases continue running. No credentials are printed or shipped.
            pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--private-proxy', action='store_true')
    parser.add_argument('--backup-pg-bin', type=Path)
    parser.add_argument('--disaster-recovery-pg-bin',type=Path)
    parser.add_argument('--recovery-from',help='Explicit committed old artifact for detached recovery acceptance')
    parser.add_argument('--tenant-export', action='store_true')
    parser.add_argument('--demo-template', action='store_true')
    parser.add_argument('--demo-reset-pg-bin', type=Path,
                        help='PostgreSQL tools for the demo reset backup and restore verification')
    parser.add_argument('--upgrade-from', help='Explicit committed old installer revision for upgrade acceptance')
    parser.add_argument('--upgrade-pg-bin', type=Path)
    args = parser.parse_args()
    if args.demo_reset_pg_bin and not args.demo_template:
        parser.error('--demo-reset-pg-bin requires --demo-template')
    if args.upgrade_from and not args.upgrade_pg_bin: parser.error('--upgrade-from requires --upgrade-pg-bin')
    if args.recovery_from and not args.disaster_recovery_pg_bin: parser.error('--recovery-from requires --disaster-recovery-pg-bin')
    if args.disaster_recovery_pg_bin and (args.upgrade_from or args.demo_template or args.private_proxy):
        parser.error('--disaster-recovery-pg-bin uses its own independent local cluster; run separately')
    if args.demo_template and (args.upgrade_from or args.tenant_export or args.backup_pg_bin or args.private_proxy):
        parser.error('--demo-template uses its own isolated data changes; run separately from other fixtures')
    print(json.dumps(asyncio.run(main(args)), ensure_ascii=False, indent=2))
