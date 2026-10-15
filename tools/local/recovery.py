"""Owned local backup and fresh-cluster restore drill; never accepts a server DSN."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
import uuid

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dev import clean_env, config, pg_bin, port_free, write_json
from local_paths import state_from_config, ensure_private_directory
from bootstrap import connect, owner

RESTORE_PORT = 15433
OWNER = 'salesbuddy_local_owner'
RUNTIMES = ('salesbuddy_local_api', 'salesbuddy_local_worker', 'salesbuddy_local_platform')
NAME = re.compile(r'backup-\d{8}-\d{6}-[a-f0-9]{12}')


class RecoveryError(RuntimeError):
    pass


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def private_file(path):
    if path.is_symlink():
        raise RecoveryError('Backup or key file is a symlink')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RecoveryError('Backup or key file must be private and owned by the current user')
    return path


def copy_private(source, destination):
    private_file(source)
    with source.open('rb') as incoming, destination.open('xb') as outgoing:
        os.chmod(destination, 0o600)
        for chunk in iter(lambda: incoming.read(1024 * 1024), b''):
            outgoing.write(chunk)
    if digest(source) != digest(destination):
        raise RecoveryError('Private file changed during backup')


def sql_name(name):
    return '"' + name.replace('"', '""') + '"'


def pg_command(name, args, *, port=15432, password=None):
    env = clean_env()
    env.update(PGHOST='127.0.0.1', PGPORT=str(port), PGUSER=OWNER,
               PGDATABASE='salesbuddy_local', PGCONNECT_TIMEOUT='10')
    if password is not None:
        env['PGPASSWORD'] = password
    result = subprocess.run([str(pg_bin() / name), *map(str, args)], env=env, capture_output=True)
    if result.returncode or (name in {'pg_dump','pg_restore'} and result.stderr.strip()):
        # Tool errors may contain SQL/data. Do not expose them or accept a partial restore.
        raise RecoveryError(name + ' failed; this backup/restore has not passed verification')
    return result


async def table_inventory(conn):
    result = {}
    tables = await conn.fetch("""SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','p') AND n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'
        ORDER BY n.nspname,c.relname""")
    for row in tables:
        quoted = sql_name(row['nspname']) + '.' + sql_name(row['relname'])
        sha, count = hashlib.sha256(), 0
        # Stable row hashes keep business text and credentials out of the manifest.
        async for record in conn.cursor('SELECT md5(to_jsonb(t)::text) AS h FROM ' + quoted + ' t ORDER BY 1'):
            sha.update(record['h'].encode()); count += 1
        result[row['nspname'] + '.' + row['relname']] = {'rows': count, 'sha256': sha.hexdigest()}
    return result


async def security_inventory(conn):
    # Include every non-system object's stored ACL, owners and policy definitions.
    # OIDs differ between clusters; names/signatures do not. Expand default
    # ACLs: NULL means the object-specific default, not an empty grant list.
    data = {}
    data['tables'] = [dict(r) for r in await conn.fetch("""SELECT n.nspname,c.relname,c.relrowsecurity,c.relforcerowsecurity,
        pg_get_userbyid(c.relowner) AS owner,
        ARRAY(SELECT a::text FROM unnest(COALESCE(c.relacl,acldefault(
            CASE WHEN c.relkind='S' THEN 's'::\"char\" ELSE 'r'::\"char\" END,c.relowner))) a ORDER BY 1) AS acl
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' ORDER BY 1,2""")]
    data['functions'] = [dict(r) for r in await conn.fetch("""SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) AS args,
        p.prosecdef,p.proconfig,pg_get_userbyid(p.proowner) AS owner,
        ARRAY(SELECT a::text FROM unnest(COALESCE(p.proacl,acldefault('f',p.proowner))) a ORDER BY 1) AS acl
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' ORDER BY 1,2,3""")]
    data['schemas'] = [dict(r) for r in await conn.fetch("""SELECT nspname,pg_get_userbyid(nspowner) AS owner,
        ARRAY(SELECT a::text FROM unnest(COALESCE(nspacl,acldefault('n',nspowner))) a ORDER BY 1) AS acl
        FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema' ORDER BY 1""")]
    data['policies'] = [dict(r) for r in await conn.fetch('SELECT * FROM pg_policies ORDER BY schemaname,tablename,policyname')]
    data['defaults'] = [dict(r) for r in await conn.fetch("""SELECT pg_get_userbyid(d.defaclrole) AS owner,
        COALESCE(n.nspname,'') AS schema,d.defaclobjtype::text AS defaclobjtype, ARRAY(SELECT a::text FROM unnest(d.defaclacl) a ORDER BY 1) AS acl FROM pg_default_acl d
        LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace ORDER BY 1,2,3""")]
    return data


async def sequence_inventory(conn):
    return [dict(r) for r in await conn.fetch("""SELECT n.nspname,c.relname,s.seqtypid::regtype::text AS type,
        s.seqstart,s.seqincrement,s.seqmin,s.seqmax,s.seqcache,s.seqcycle FROM pg_sequence s
        JOIN pg_class c ON c.oid=s.seqrelid JOIN pg_namespace n ON n.oid=c.relnamespace ORDER BY 1,2""")]


async def verify_sequence_positions(conn):
    # Sequence counters are not MVCC snapshot data. They may safely be ahead of
    # snapshot rows, but must never generate an already restored owned-column ID.
    rows=await conn.fetch("""SELECT sn.nspname AS schema,sc.relname AS sequence,s.seqincrement,s.seqcycle,
        tn.nspname AS table_schema,tc.relname AS table_name,a.attname AS column_name
        FROM pg_sequence s JOIN pg_class sc ON sc.oid=s.seqrelid JOIN pg_namespace sn ON sn.oid=sc.relnamespace
        JOIN pg_depend d ON d.classid='pg_class'::regclass AND d.objid=sc.oid AND d.deptype IN ('a','i')
        JOIN pg_class tc ON tc.oid=d.refobjid JOIN pg_namespace tn ON tn.oid=tc.relnamespace
        JOIN pg_attribute a ON a.attrelid=tc.oid AND a.attnum=d.refobjsubid""")
    for row in rows:
        if row['seqcycle']:raise RecoveryError('Cyclic owned sequences require a separate recovery check')
        counter=await conn.fetchrow('SELECT last_value,is_called FROM '+sql_name(row['schema'])+'.'+sql_name(row['sequence']))
        aggregate='max' if row['seqincrement']>0 else 'min'
        bound=await conn.fetchval('SELECT '+aggregate+'('+sql_name(row['column_name'])+') FROM '+
                                 sql_name(row['table_schema'])+'.'+sql_name(row['table_name']))
        following=counter['last_value']+(row['seqincrement'] if counter['is_called'] else 0)
        if bound is not None and ((following<=bound) if row['seqincrement']>0 else (following>=bound)):
            raise RecoveryError('Restored sequence could reuse an existing ID')
    return len(rows)


async def roles(conn):
    rows = [dict(r) for r in await conn.fetch("""SELECT rolname,rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,
        rolcanlogin,rolinherit,rolreplication FROM pg_roles WHERE rolname NOT LIKE 'pg_%' ORDER BY rolname""")]
    known_login = {OWNER, *RUNTIMES}
    for row in rows:
        if row['rolname'] != OWNER and any(row[k] for k in ('rolsuper','rolbypassrls','rolcreaterole','rolcreatedb','rolreplication')):
            raise RecoveryError('Unexpected privileged role in local cluster')
        if row['rolcanlogin'] and row['rolname'] not in known_login:
            raise RecoveryError('Unknown local login role needs an explicit credential recovery plan')
    members = [dict(r) for r in await conn.fetch("""SELECT pg_get_userbyid(roleid) AS role,pg_get_userbyid(member) AS member,
        admin_option,inherit_option,set_option FROM pg_auth_members
        WHERE pg_get_userbyid(member) NOT LIKE 'pg_%' AND pg_get_userbyid(roleid) NOT LIKE 'pg_%' ORDER BY 1,2""")]
    return {'roles': rows, 'memberships': members}


async def originals(conn, directory, local, *, copying):
    result = []
    rows = await conn.fetch('SELECT id,storage_driver,storage_key,file_path,file_size,content_sha256 FROM activity.visit_import ORDER BY id')
    for row in rows:
        sha, size = hashlib.sha256(), 0
        if row['storage_driver'] == 'postgres':
            index = 0
            async for chunk in conn.cursor('SELECT chunk_no,content FROM activity.visit_import_content WHERE import_id=$1 ORDER BY chunk_no',row['id']):
                if chunk['chunk_no'] != index:
                    raise RecoveryError('Original material chunk sequence is incomplete')
                sha.update(chunk['content']); size += len(chunk['content']); index += 1
        elif row['storage_driver'] == 'local':
            source = Path(row['storage_key'] or row['file_path'])
            # Legacy files are immutable. Only this environment's materials root is allowed.
            if source.parent != local / 'materials' or source.is_symlink() or source.resolve() != source:
                raise RecoveryError('Legacy original file is outside the owned material directory')
            destination = directory / 'originals' / str(row['id'])
            if copying:
                copy_private(source, destination)
            private_file(destination)
            with destination.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    sha.update(chunk); size += len(chunk)
        else:
            raise RecoveryError('External object storage requires its own verified snapshot; incomplete backup refused')
        if size != row['file_size'] or (row['content_sha256'] and sha.hexdigest() != row['content_sha256']):
            raise RecoveryError('Original material size or hash mismatch')
        result.append({'id':str(row['id']), 'driver':row['storage_driver'], 'bytes':size, 'sha256':sha.hexdigest()})
    return result


async def verify_keys(conn, directory):
    from sales_backend.security.platform_mfa import PlatformMFACipher
    cipher = PlatformMFACipher.from_file(str(directory / 'platform-mfa-keys.json'), 'local-v1')
    count = 0
    for row in await conn.fetch('SELECT id,mfa_secret FROM control.operator WHERE mfa_secret IS NOT NULL'):
        cipher.open(str(row['id']), json.loads(row['mfa_secret'])); count += 1
    for row in await conn.fetch('SELECT operator_id,mfa_pending FROM control.session WHERE mfa_pending IS NOT NULL'):
        cipher.open(str(row['operator_id']), json.loads(row['mfa_pending'])); count += 1
    # The isolated local runtime has no provider credential keyring. Never claim
    # credentials can be restored when an unconfigured external key is needed.
    for table in ('config.agent_runtime_config', 'config.model_api_test', 'config.model_api_release'):
        if await conn.fetchval('SELECT to_regclass($1)',table) and await conn.fetchval(
                'SELECT EXISTS(SELECT 1 FROM ' + table + ' WHERE api_key_ciphertext IS NOT NULL)'):
            raise RecoveryError('Provider credentials require an explicit private key backup configuration')
    return count


async def snapshot_inventory(conn, directory, local, *, copying):
    await conn.execute("SET LOCAL timezone='UTC'")
    return {'tables':await table_inventory(conn), 'security':await security_inventory(conn),
            'sequences':await sequence_inventory(conn),
            'roles':await roles(conn), 'originals':await originals(conn,directory,local,copying=copying),
            'migrations':[dict(r) for r in await conn.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum ORDER BY migration_key')],
            'mfa_secrets_checked':await verify_keys(conn,directory)}


async def backup(cfg):
    local = state_from_config(cfg)
    admin = await owner(cfg); await admin.close()
    parent = local / 'backups'; ensure_private_directory(parent)
    directory = parent / ('backup-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:12])
    directory.mkdir(mode=0o700); (directory/'originals').mkdir(mode=0o700)
    for name in ('settings.json', 'platform-mfa-keys.json'):
        copy_private(local / name, directory / name)
    dump = directory / 'database.dump'; dump.touch(mode=0o600)
    conn = await connect(cfg)
    try:
        async with conn.transaction(isolation='repeatable_read',readonly=True):
            if await conn.fetchval('SELECT version FROM public.salesbuddy_local_fixture WHERE id=1') != 1:
                raise RecoveryError('Only the marked synthetic local environment may be backed up')
            snapshot = await conn.fetchval('SELECT pg_export_snapshot()')
            expected = await snapshot_inventory(conn,directory,local,copying=True)
            await asyncio.to_thread(pg_command,'pg_dump',['--format=custom','--snapshot='+snapshot,'--file='+str(dump)],password=cfg['owner_password'])
        for name in ('settings.json','platform-mfa-keys.json'):
            if digest(directory/name) != digest(local/name):
                raise RecoveryError('Local credentials changed during backup; retry with stable configuration')
        hashes = {str(p.relative_to(directory)):digest(p) for p in directory.rglob('*') if p.is_file()}
        write_json(directory/'manifest.json',{'kind':'salesbuddy-local-backup-v2', 'source_root':cfg['root'],
                   'created_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'build':json.loads((local/'build.json').read_text()),
                   'files':hashes,'inventory':expected},private=True)
    finally:
        await conn.close()
    return directory


def validate_backup(cfg, directory):
    local = state_from_config(cfg)
    directory = Path(directory)
    if directory.parent != local/'backups' or not NAME.fullmatch(directory.name) or directory.is_symlink() or directory.resolve()!=directory:
        raise RecoveryError('Only this local environment\'s named backup may be restored')
    if directory.stat().st_uid!=os.getuid() or directory.stat().st_mode&0o077:
        raise RecoveryError('Backup directory must be private')
    manifest = json.loads(private_file(directory/'manifest.json').read_text())
    if manifest.get('kind')!='salesbuddy-local-backup-v2' or manifest.get('source_root')!=cfg['root']:
        raise RecoveryError('Backup identity differs from this environment')
    required={'database.dump','settings.json','platform-mfa-keys.json'}
    if not required.issubset(manifest.get('files',{})):
        raise RecoveryError('Backup is incomplete')
    for name, expected in manifest['files'].items():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or (name not in required and not re.fullmatch(r'originals/[a-f0-9-]{36}',name)):
            raise RecoveryError('Invalid backup member path')
        source = directory/path
        if source.resolve()!=source or digest(private_file(source))!=expected:
            raise RecoveryError('Backup member is redirected or differs from checksum')
    return manifest


async def restored_isolation(conn, cfg):
    from sales_backend.db import set_request_context
    from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
    fixtures = json.loads(await conn.fetchval('SELECT data::text FROM public.salesbuddy_local_fixture WHERE id=1'))
    accounts=[]
    for company in fixtures['companies']:
        code='AADMIN' if company['workspace']=='local-a' else 'BADMIN'
        row=await conn.fetchrow('''SELECT u.id,c.password_hash FROM platform.user_ref u JOIN platform.password_credential c
            ON c.user_ref_id=u.id WHERE u.workspace_id=$1::uuid AND u.account_code=$2''',company['id'],code)
        # Passwords are covered by the byte-preserving table comparison; do not reset them.
        accounts.append((company,str(row['id'])))
    await conn.execute('SET ROLE salesbuddy_local_api')
    try:
        if await conn.fetchval('SELECT count(*) FROM crm.customer'):
            raise RecoveryError('Restored runtime sees customers without identity')
        for company, user in accounts:
            actor=ActorContext(workspace_id=company['id'],user_id=user,role=RoleCode.ADMINISTRATOR,data_scope=DataScope.WORKSPACE)
            async with conn.transaction():
                await set_request_context(conn,actor)
                for other,_ in accounts:
                    count=await conn.fetchval('SELECT count(*) FROM crm.customer WHERE id=$1::uuid',other['customer_id'])
                    if count != (1 if company==other else 0):
                        raise RecoveryError('Restored tenant isolation failed')
    finally:
        await conn.execute('RESET ROLE')


async def restore_check(cfg, directory):
    manifest = validate_backup(cfg,directory)
    local = state_from_config(cfg)
    saved = json.loads((directory/'settings.json').read_text())
    if saved.get('root')!=cfg['root'] or state_from_config(saved)!=local:
        raise RecoveryError('Backed-up settings identity is invalid')
    parent=local/'restore-drills'; ensure_private_directory(parent)
    target=parent/uuid.uuid4().hex; target.mkdir(mode=0o700)
    (target/'originals').mkdir(mode=0o700)
    for name in manifest['files']:
        if name!='database.dump': copy_private(directory/name,target/name)
    data=target/'postgres'; password=secrets.token_hex(32)
    pwfile=target/'owner-password'; pwfile.touch(mode=0o600); pwfile.write_text(password)
    started=False; conn=None; beginning=time.monotonic()
    try:
        port_free(RESTORE_PORT)
        pg_command('initdb',['-D',data,'-U',OWNER,'--pwfile',pwfile,'--auth-host=scram-sha-256',
                            '--auth-local=scram-sha-256','--encoding=UTF8','--locale=C'])
        pwfile.unlink()
        pg_command('pg_ctl',['-D',data,'-l',target/'postgres.log','-o',
            f"-h 127.0.0.1 -p {RESTORE_PORT} -c unix_socket_directories=''",'-w','start'])
        started=True
        conn=await asyncpg.connect(host='127.0.0.1',port=RESTORE_PORT,user=OWNER,password=password,database='postgres')
        if Path(await conn.fetchval('SHOW data_directory'))!=data:
            raise RecoveryError('Restoration connected to an unexpected cluster')
        for row in manifest['inventory']['roles']['roles']:
            name=row['rolname']
            if name==OWNER: continue
            if any(row[k] for k in ('rolsuper','rolbypassrls','rolcreaterole','rolcreatedb','rolreplication')):
                raise RecoveryError('Restored application role must not be privileged')
            await conn.execute('CREATE ROLE '+sql_name(name)+' NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB '+('INHERIT' if row['rolinherit'] else 'NOINHERIT'))
            if row['rolcanlogin']:
                if name not in RUNTIMES:raise RecoveryError('Unexpected login role')
                literal=await conn.fetchval('SELECT quote_literal($1::text)',saved[name.removeprefix('salesbuddy_local_')+'_password'])
                await conn.execute('ALTER ROLE '+sql_name(name)+' LOGIN PASSWORD '+literal)
        for membership in manifest['inventory']['roles']['memberships']:
            if OWNER in (membership['role'],membership['member']):
                raise RecoveryError('Unexpected maintenance role membership')
            await conn.execute('GRANT '+sql_name(membership['role'])+' TO '+sql_name(membership['member'])+
                ' WITH ADMIN '+str(membership['admin_option']).lower()+', INHERIT '+str(membership['inherit_option']).lower()+', SET '+str(membership['set_option']).lower())
        await conn.execute('CREATE DATABASE salesbuddy_local TEMPLATE template0')
        await conn.close(); conn=None
        await asyncio.to_thread(pg_command,'pg_restore',['--exit-on-error','--dbname=salesbuddy_local',directory/'database.dump'],port=RESTORE_PORT,password=password)
        conn=await asyncpg.connect(host='127.0.0.1',port=RESTORE_PORT,user=OWNER,password=password,database='salesbuddy_local')
        async with conn.transaction(isolation='repeatable_read',readonly=True):
            actual=await snapshot_inventory(conn,target,local,copying=False)
        if actual!=manifest['inventory']:
            different=[k for k in actual if actual[k]!=manifest['inventory'][k]]
            if 'security' in different:
                differences={}
                for key in actual['security']:
                    old=manifest['inventory']['security'][key];new=actual['security'][key]
                    if old!=new: differences[key]={'source':[r for r in old if r not in new], 'restored':[r for r in new if r not in old]}
                write_json(target/'security-difference.json',differences,private=True)
            raise RecoveryError('Restored verification differs: '+','.join(different))
        # Historical local references must point into the restored file set, never
        # accidentally keep reading the source environment after a successful drill.
        from sales_backend.services.material_storage import MaterialStorage
        storage=MaterialStorage({'default':'postgres','profiles':{'postgres':{'driver':'postgres'},
            'legacy-local':{'driver':'local','root':str(target/'originals')}}})
        for row in await conn.fetch("SELECT * FROM activity.visit_import WHERE storage_driver='local'"):
            path=str(target/'originals'/str(row['id']))
            if await conn.fetchval("SELECT to_regprocedure('control.rebind_restored_local_original(uuid,text)') IS NOT NULL"):
                await conn.execute('SELECT control.rebind_restored_local_original($1,$2)',row['id'],path)
            else:
                await conn.execute('UPDATE activity.visit_import SET storage_key=$2,file_path=$2 WHERE id=$1',row['id'],path)
            restored=dict(row);restored.update(storage_key=path,file_path=path)
            await storage.read(None,None,restored)
        sequences_checked=await verify_sequence_positions(conn)
        await restored_isolation(conn,saved)
        # Connect as a real restricted login, without maintenance-role SET ROLE.
        for role in RUNTIMES:
            runtime=await asyncpg.connect(host='127.0.0.1',port=RESTORE_PORT,user=role,
                password=saved[role.removeprefix('salesbuddy_local_')+'_password'],database='salesbuddy_local')
            try:
                if role.endswith('_platform'):
                    try: await runtime.fetchval('SELECT count(*) FROM crm.customer')
                    except asyncpg.InsufficientPrivilegeError: pass
                    else: raise RecoveryError('Platform role can directly read business data')
                elif await runtime.fetchval('SELECT count(*) FROM crm.customer'):
                    raise RecoveryError('Restored runtime login bypasses tenant isolation')
            finally: await runtime.close()
        report={'status':'passed','checked_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'source_revision':manifest['build']['revision'], 'restore_seconds':round(time.monotonic()-beginning,2),
            'table_count':len(actual['tables']), 'original_count':len(actual['originals']),
            'migration_count':len(actual['migrations']), 'sequences_checked':sequences_checked, 'mfa_secrets_checked':actual['mfa_secrets_checked'],
            'checks':['fresh PostgreSQL cluster','database rows and hashes','ACLs and RLS policies','roles and memberships',
                      'migration checksums','sequence definitions and safe next IDs','original material bytes','platform MFA decryption','actual restricted logins','two-way tenant isolation'],
            'limits':['local drill only; not production RPO/RTO or offsite backup','no Worker or external integration started',
                      'external object stores and provider keyrings require separately configured backups'],
            'production_changed':False}
    finally:
        try:
            if conn:await conn.close()
        finally:
            pwfile.unlink(missing_ok=True)
            # A start command may time out after creating its own process. Only
            # inspect/stop this invocation's newly created data directory.
            if started or (data/'postmaster.pid').exists():
                pg_command('pg_ctl',['-D',data,'-m','fast','-w','stop'])
        # Retain this drill directory for inspection. Never overwrite the active cluster.
    report['checks'].append('independent restore cluster stopped')
    write_json(directory/'restore-verification.json',report,private=True)
    return report


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('backup','restore-check'))
    parser.add_argument('--backup', help='A named private backup created by this environment')
    args=parser.parse_args()
    if args.backup and args.action!='restore-check':parser.error('--backup is only valid for restore-check')
    cfg=config()
    directory=Path(args.backup) if args.backup else await backup(cfg)
    if args.action=='restore-check':
        print(json.dumps(await restore_check(cfg,directory),ensure_ascii=False,indent=2))
    print('Private local backup: '+str(directory))


if __name__=='__main__':
    try: asyncio.run(main())
    except RecoveryError as exc:
        print(str(exc),file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print('Local backup/recovery did not pass. No production change or active database replacement was made.',file=sys.stderr)
        raise SystemExit(1) from None
