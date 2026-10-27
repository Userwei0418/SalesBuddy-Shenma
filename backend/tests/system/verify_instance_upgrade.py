"""Real offline upgrades and abrupt exits inside a disposable installer fixture."""
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys

import asyncpg
import httpx

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'tools/installation'))
from instance_files import read_private, metadata_digest
from release_bundle import committed_files, prepare, write_archive, migration_manifest, json_bytes
from runtime_roles import fingerprint


def failure_diagnostic(root, action, crash, result=None, *, timed_out=False, dependency_offset=0, expect=True):
    """Only fixed categories escape the private fixture, never exception text."""
    stderr = getattr(result, 'stderr', '') or ''
    if isinstance(stderr, bytes): stderr = stderr.decode('utf-8', errors='replace')
    # UV's detailed output is private. Read a bounded tail only to classify the
    # failure, without echoing paths, request URLs, credentials or SQL arguments.
    dependency = root/'logs/upgrade-dependencies.log'
    dependency_tail = ''
    if dependency.is_file() and not dependency.is_symlink():
        with dependency.open('rb') as stream:
            stream.seek(0, os.SEEK_END); size = stream.tell()
            stream.seek(max(dependency_offset, size-65536))
            dependency_tail = stream.read(65536).decode('utf-8', errors='replace')
    combined = (stderr[-65536:]+'\n'+dependency_tail).lower()
    markers = {
        'network_timeout': ('timed out', 'timeout', 'time out'),
        'network_dns': ('dns error', 'failed to lookup address', 'name resolution'),
        'network_connection': ('connection refused', 'connection reset', 'failed to connect'),
        'network_tls': ('certificate verify failed', 'invalid peer certificate', 'unknown issuer'),
        'package_download': ('failed to download', 'failed to fetch'),
        'dependency_operation': ('upgrade-dependencies.log',),
        'dependency_missing': ('no module named', 'modulenotfounderror'),
        'source_integrity': ('differs from its verified archive', 'source tree differs'),
    }
    allowed_errors = ('InstanceError','ValueError','RuntimeError','AssertionError',
                      'ModuleNotFoundError','FileNotFoundError','PermissionError',
                      'TimeoutError','ConnectionError','CalledProcessError')
    diagnostic = {
        'kind': 'upgrade-fixture-failure', 'action': action,
        'checkpoint': crash, 'expected_exit': '92' if crash else ('0' if expect else 'nonzero'),
        'actual_exit_code': getattr(result, 'returncode', None), 'timed_out': timed_out,
        'error_types': [name for name in allowed_errors if re.search(r'\b'+name+r'\b', stderr)],
        'categories': [name for name, patterns in markers.items() if any(p in combined for p in patterns)],
        'private_log_retained_in_fixture': True,
    }
    line=json.dumps(diagnostic, sort_keys=True)
    descriptor=os.open(root.parent/'upgrade-diagnostics.jsonl',os.O_CREAT|os.O_WRONLY|os.O_APPEND,0o600)
    with os.fdopen(descriptor,'a') as stream: stream.write(line+'\n')
    print(line,file=sys.stderr,flush=True)
    return diagnostic


def invoke(root,pg_bin,action,*,archive=None,sha=None,crash=None,expect=True):
    driver=ROOT/'backend/tests/system/crash_instance_upgrade.py' if crash else ROOT/'tools/installation/instance_upgrade.py'
    command=[sys.executable,str(driver),*([crash] if crash else []),action,'--root',str(root),'--pg-bin',str(pg_bin)]
    if archive: command += ['--archive',str(archive),'--sha256',sha,'--acknowledge-api-change'] # reviewed additive fixture API delta
    env={k:os.environ[k] for k in ('PATH','HOME','LANG','LC_ALL','TMPDIR','SALESBUDDY_INSTALL_DB_PASSWORD') if k in os.environ}
    env['PYTHONPYCACHEPREFIX']=str(root/'cache/pycache')
    dependency=root/'logs/upgrade-dependencies.log'
    dependency_offset=dependency.stat().st_size if dependency.is_file() and not dependency.is_symlink() else 0
    try:
        result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=600)
    except subprocess.TimeoutExpired as exc:
        descriptor=os.open(root.parent/'upgrade-driver.log',os.O_CREAT|os.O_WRONLY|os.O_APPEND,0o600)
        with os.fdopen(descriptor,'a') as stream:
            stream.write(action+' '+str(crash)+' timeout=600\n')
            for value in (exc.stdout,exc.stderr):
                if value: stream.write(value.decode('utf-8',errors='replace') if isinstance(value,bytes) else value)
            stream.write('\n')
        failure_diagnostic(root,action,crash,exc,timed_out=True,dependency_offset=dependency_offset,expect=expect)
        raise
    descriptor=os.open(root.parent/'upgrade-driver.log',os.O_CREAT|os.O_WRONLY|os.O_APPEND,0o600)
    with os.fdopen(descriptor,'a') as stream: stream.write(action+' '+str(crash)+' exit='+str(result.returncode)+'\n'+result.stdout+result.stderr+'\n')
    if ((crash and result.returncode!=92) or (not crash and expect and result.returncode!=0)
            or (not crash and not expect and result.returncode==0)):
        failure_diagnostic(root,action,crash,result,dependency_offset=dependency_offset,expect=expect)
    if crash:
        assert result.returncode==92, 'Crash checkpoint not reached; private fixture: '+str(root)
    elif not expect:
        assert result.returncode, 'Unsafe upgrade unexpectedly accepted'
    else:
        assert result.returncode==0, 'Upgrade command failed; private fixture: '+str(root)
        return json.loads(result.stdout)


async def exercise(root,args,command,tokens,customers,account_password):
    old=read_private(root/'INSTANCE.json')
    original_private={p.name:p.read_bytes() for p in (root/'private').iterdir()}
    original_materials={str(p.relative_to(root)):p.read_bytes() for p in (root/'materials').rglob('*') if p.is_file()}
    assert original_materials, 'Upgrade acceptance needs an actual synthetic local original'
    revision,files,modes=committed_files(ROOT)
    target=json.loads((root/'release/PRODUCT_MANIFEST.json').read_text())
    def package(label,source,version=None):
        prepared,permissions,manifest=prepare(source,modes,revision=version or revision,api_url=target['api_base_url'],
            appid=target['appid'],profile=target['profile'],product_name=label)
        archive=root.parent/(label+'.zip'); sha=write_archive(prepared,permissions,archive)
        return archive,sha,manifest
    archive,sha,manifest=package('upgrade-real',files)
    invoke(root,args.upgrade_pg_bin,'upgrade',archive=archive,sha=sha,expect=False) # live services
    command(root,'stop','--root',str(root))
    async def connection():
        return await asyncpg.connect(host='127.0.0.1',port=args.port,user=args.user,database=old['database']['name'],
            password=os.environ.get('SALESBUDDY_INSTALL_DB_PASSWORD'),command_timeout=300)
    async def preserved(expected):
        conn=await connection()
        try:
            assert await conn.fetchval('SELECT metadata_digest FROM public.salesbuddy_installation')==expected
            assert await conn.fetchval('SELECT count(*) FROM platform.workspace')==2
            assert set(str(r['id']) for r in await conn.fetch('SELECT id FROM crm.customer'))==set(customers.values())
            assert before_roles==dict(await conn.fetch('SELECT rolname,rolpassword FROM pg_authid WHERE rolname=ANY($1::text[])',list(old['roles'].values())))
            assert before_operator==dict(await conn.fetchrow('SELECT id,password_hash,mfa_secret FROM control.operator'))
            assert before_imports==[dict(r) for r in await conn.fetch('SELECT * FROM activity.visit_import ORDER BY id')]
            assert before_chunks==[dict(r) for r in await conn.fetch('SELECT * FROM activity.visit_import_content ORDER BY import_id,chunk_no')]
            assert original_materials=={str(p.relative_to(root)):p.read_bytes() for p in (root/'materials').rglob('*') if p.is_file()}
        finally: await conn.close()
    conn=await connection()
    before_roles=dict(await conn.fetch('SELECT rolname,rolpassword FROM pg_authid WHERE rolname=ANY($1::text[])',list(old['roles'].values())))
    before_operator=dict(await conn.fetchrow('SELECT id,password_hash,mfa_secret FROM control.operator'))
    before_imports=[dict(r) for r in await conn.fetch('SELECT * FROM activity.visit_import ORDER BY id')]
    before_chunks=[dict(r) for r in await conn.fetch('SELECT * FROM activity.visit_import_content ORDER BY import_id,chunk_no')]
    assert len(before_imports)==2 and before_chunks
    await conn.execute('CREATE SEQUENCE public.upgrade_probe_sequence START 101')
    await conn.close()
    # Two synthetic migrations prove a later failure rolls back the earlier file,
    # rather than leaving a partially upgraded database. Never a shipping migration.
    fixture_version=int(manifest['expected_schema'][1:])+1
    first_key=f'V{fixture_version:03d}'; second_key=f'V{fixture_version+1:03d}'
    failed=dict(files)
    failed[f'database/migrations/{first_key}__upgrade_fixture.sql']=b"BEGIN;\nCREATE TABLE ops.upgrade_fixture(id integer);\nINSERT INTO ops.upgrade_fixture VALUES(1);\nSELECT nextval('public.upgrade_probe_sequence');\nCOMMIT;\n"
    failed[f'database/migrations/{second_key}__upgrade_failure.sql']=b"BEGIN;\nDO $$ BEGIN RAISE EXCEPTION 'synthetic upgrade failure'; END $$;\nCOMMIT;\n"
    policy=json.loads(failed['tools/installation/runtime_permissions.json']); policy['schema']=second_key
    policy['migration_fingerprint']=fingerprint({r['key']:r['sha256'] for r in migration_manifest(failed)})
    failed['tools/installation/runtime_permissions.json']=json_bytes(policy)
    failed_archive,failed_sha,_=package('upgrade-transaction-failure',failed)
    invoke(root,args.upgrade_pg_bin,'upgrade',archive=failed_archive,sha=failed_sha,expect=False)
    conn=await connection()
    try:
        # nextval is deliberately nontransactional and belongs only to this
        # fixture: it proves the first ran before the second failed and rolled it back.
        assert dict(await conn.fetchrow('SELECT last_value,is_called FROM public.upgrade_probe_sequence'))=={'last_value':101,'is_called':True}
        assert await conn.fetchval("SELECT to_regclass('ops.upgrade_fixture')") is None
        assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops.migration_checksum WHERE migration_key=ANY($1::text[]))", [first_key,second_key])
    finally: await conn.close()
    await preserved(metadata_digest(old))
    failed_pending=read_private(root/'run/upgrade.json')
    # Incoming bytes may be damaged before commit. Original version checks must
    # still allow cancellation without trusting or executing the damaged package.
    (root/'upgrades'/failed_pending['id']/'artifact.zip').write_bytes(b'damaged synthetic incoming archive')
    assert invoke(root,args.upgrade_pg_bin,'abort')['status']=='aborted'
    conn=await connection()
    try: await conn.execute('DROP SEQUENCE public.upgrade_probe_sequence')
    finally: await conn.close()
    # Exit while SQL is open, then cancel without resetting accounts/data.
    invoke(root,args.upgrade_pg_bin,'upgrade',archive=archive,sha=sha,crash='before_database_commit')
    await preserved(metadata_digest(old))
    command(root,'start','--root',str(root),expect=False)
    assert invoke(root,args.upgrade_pg_bin,'abort')['revision']==old['revision']
    command(root,'start','--root',str(root),'--feishu'); command(root,'stop','--root',str(root))
    # Uncertain database commit and half-renamed file trees finish forward.
    invoke(root,args.upgrade_pg_bin,'upgrade',archive=archive,sha=sha,crash='database_committed')
    journal=read_private(root/'run/upgrade.json')
    await preserved(metadata_digest(journal['new']))
    invoke(root,args.upgrade_pg_bin,'abort',expect=False)
    runtime=root/journal['new']['runtime_directory']
    dependency=next(runtime.glob('lib/python*/site-packages/asyncpg/__init__.py'))
    expected_dependency=dependency.read_bytes(); dependency.unlink()
    for phase in ('old_release_saved','old_archive_saved','metadata_switched'):
        invoke(root,args.upgrade_pg_bin,'resume',crash=phase)
    assert dependency.read_bytes()==expected_dependency
    result=invoke(root,args.upgrade_pg_bin,'resume')
    assert result['status']=='completed' and result['revision']==revision
    assert original_private=={p.name:p.read_bytes() for p in (root/'private').iterdir()}
    await preserved(metadata_digest(journal['new']))
    started=command(root,'start','--root',str(root),'--feishu')
    assert started['revision']==revision and started['database_ledger_checked']
    assert command(root,'check','--root',str(root),'--feishu')['revision']==revision
    proxy_headers={'X-Forwarded-Proto':'https'} if args.private_proxy else {}
    async with httpx.AsyncClient(base_url=started['api'],trust_env=False,timeout=20,headers=proxy_headers) as client:
        for workspace,token in tokens:
            result=await client.get('/api/v1/console/customers',headers={'Authorization':'Bearer '+token})
            assert result.status_code==200 and [i['id'] for i in result.json()['items']]==[customers[workspace]]
        for suffix in ('a','b'):
            result=await client.post('/api/v1/auth/password/login',json={'account_code':'FIRSTADMIN','password':account_password,
                'workspace':'installer-'+suffix,'role':'administrator'})
            assert result.status_code==200
    # A second synthetic-only candidate exercises a real forward schema change
    # and an explicit new API grant, after the committed code-only upgrade above.
    # Its revision is a test marker, never a release or deliverable Git commit.
    forward=dict(files)
    forward[f'database/migrations/{first_key}__upgrade_grant_fixture.sql']=f'BEGIN;\nCREATE TABLE public.upgrade_fixture(id integer);\nINSERT INTO public.upgrade_fixture VALUES({fixture_version});\nCREATE FUNCTION ops.upgrade_probe() RETURNS integer LANGUAGE sql AS $$ SELECT {fixture_version} $$;\nCOMMIT;\n'.encode()
    policy=json.loads(forward['tools/installation/runtime_permissions.json']); policy['schema']=first_key
    policy['services']['api']['functions'].append('ops.upgrade_probe()')
    policy['migration_fingerprint']=fingerprint({r['key']:r['sha256'] for r in migration_manifest(forward)})
    forward['tools/installation/runtime_permissions.json']=json_bytes(policy)
    synthetic_revision=hashlib.sha1((revision+':synthetic-upgrade-'+first_key).encode()).hexdigest()
    forward_archive,forward_sha,_=package('upgrade-forward-fixture',forward,synthetic_revision)
    command(root,'stop','--root',str(root))
    invoke(root,args.upgrade_pg_bin,'upgrade',archive=forward_archive,sha=forward_sha)
    updated=read_private(root/'INSTANCE.json'); await preserved(metadata_digest(updated))
    for service in ('api','worker'):
        private=read_private(root/'private'/f'{service}.json')
        conn=await asyncpg.connect(host='127.0.0.1',port=args.port,user=old['roles'][service],
            password=private['database_passwords'][service],database=old['database']['name'])
        try:
            if service=='api': assert await conn.fetchval('SELECT ops.upgrade_probe()')==fixture_version
            else:
                try: await conn.fetchval('SELECT ops.upgrade_probe()')
                except asyncpg.InsufficientPrivilegeError: pass
                else: raise AssertionError('Worker inherited new API-only grant')
        finally: await conn.close()
    assert command(root,'start','--root',str(root),'--feishu')['schema']==first_key
    assert command(root,'check','--root',str(root),'--feishu')['revision']==synthetic_revision
    # Direct installed command, without a caller-supplied pycache setting, must
    # keep the hash-verified release tree pristine.
    active=read_private(root/'INSTANCE.json')
    clean={k:os.environ[k] for k in ('PATH','HOME','LANG','LC_ALL','TMPDIR') if k in os.environ}
    direct=subprocess.run([str(root/active['runtime_directory']/'bin/python'),str(root/'release/tools/installation/instance.py'),
        'check','--root',str(root),'--feishu'],capture_output=True,text=True,env=clean,timeout=30)
    assert direct.returncode==0 and json.loads(direct.stdout)['revision']==synthetic_revision
    assert command(root,'check','--root',str(root),'--feishu')['revision']==synthetic_revision
    return {'from_revision':old['revision'],'revision':revision,'status':'passed','migration_transaction_rollback':True,
        'before_commit_abort_preserves_old':True,'after_commit_abort_refused':True,
        'damaged_incoming_can_cancel_before_commit':True,
        'interrupted_checkpoints':['before_database_commit','database_committed','old_release_saved','old_archive_saved','metadata_switched'],
        'credentials_mfa_tenants_customers_preserved':True,'three_services_restarted':True,'real_http_login_and_isolation':True,
        'postgres_and_local_originals_preserved':2,
        'synthetic_forward_migration_and_api_only_grant':first_key+' test fixture; not a released migration',
        'interrupted_dependency_reinstalled':True,'installed_cli_keeps_verified_tree_unchanged':True,
        'automatic_database_rollback':False,'production_changed':False}
