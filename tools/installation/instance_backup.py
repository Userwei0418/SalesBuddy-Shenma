"""Offline instance backup and isolated same-cluster restore check.

Never overwrites a database or changes cluster roles. The restore probe gets a
random new database, denies runtime connections, and is removed in finally.
This is a local upgrade safeguard, not an offsite disaster-recovery claim.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import time
import traceback
import uuid

if __name__ == '__main__': sys.dont_write_bytecode = True

import asyncpg

from database_inventory import table_inventory, security_inventory, sequence_inventory, verify_sequence_positions
from instance_database import database_config
from instance_files import InstanceError, atomic_json, clean_env, instance, locked, metadata_digest, read_private, root_path, source_check
from instance_process import service_lock
from release_bundle import read_verified
from runtime_roles import identifier, verify, validate_policy

BACKUP_NAME = re.compile(r'backup-[0-9]{8}-[0-9]{6}-[a-f0-9]{12}\Z')
MAX_MANIFEST_BYTES = 32*1024*1024


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''): value.update(chunk)
    return value.hexdigest()


def owned_file(path, *, private=True):
    path = Path(path)
    if path.resolve() != path or path.is_symlink():
        raise InstanceError('Backup member path is redirected')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or (private and info.st_mode & 0o077):
        raise InstanceError('Backup member is not an owned private file')
    return path


def copy_file(source, target, *, private=True):
    owned_file(source, private=private)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with target.open('xb') as output, source.open('rb') as incoming:
        os.chmod(target, 0o600)
        for chunk in iter(lambda: incoming.read(1024*1024), b''): output.write(chunk)
        output.flush(); os.fsync(output.fileno())
    if digest(source) != digest(target):
        raise InstanceError('Source file changed while copying the backup')


def material_tree(root):
    result = {}
    for path in sorted((root/'materials').rglob('*')):
        if path.is_symlink() or path.resolve() != path:
            raise InstanceError('Material directory contains a redirected path')
        if path.is_file():
            owned_file(path, private=False)
            result[str(path.relative_to(root))] = digest(path)
        elif not path.is_dir():
            raise InstanceError('Material directory contains a special file')
    return result


def tool(pg_bin, name):
    path = Path(pg_bin).resolve()/name
    result = subprocess.run([str(path), '--version'], capture_output=True, text=True, timeout=10, env=clean_env())
    if result.returncode or not re.search(r'\(PostgreSQL\) 16\.', result.stdout):
        raise InstanceError('Backup and restore require explicitly selected PostgreSQL 16 client tools')
    return path


def pg_command(pg_bin, name, args, data, database):
    config = data['database']
    env = {**clean_env(), 'PGHOST':config['host'], 'PGPORT':str(config['port']),
           'PGUSER':config['administrator'], 'PGDATABASE':database, 'PGSSLMODE':config['sslmode'], 'PGCONNECT_TIMEOUT':'10'}
    if 'SALESBUDDY_INSTALL_DB_PASSWORD' in os.environ:
        env['PGPASSWORD'] = os.environ['SALESBUDDY_INSTALL_DB_PASSWORD']
    result = subprocess.run([str(tool(pg_bin, name)), *args], capture_output=True, env=env, timeout=600)
    if result.returncode or result.stderr.strip():
        raise InstanceError(name+' failed; incomplete backup/restore was retained without a success receipt')


async def check_record(conn, data):
    if await conn.fetchval('SELECT current_database()') != data['database']['name']:
        raise InstanceError('Maintenance connection targets another instance database')
    marker = await conn.fetchval("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()")
    row = await conn.fetchrow('SELECT instance_id,metadata_digest FROM public.salesbuddy_installation')
    if (marker != 'salesbuddy-instance-v1:'+data['instance_id'] or not row
            or row['instance_id'] != uuid.UUID(data['instance_id']) or row['metadata_digest'] != metadata_digest(data)):
        raise InstanceError('Database identity does not match this instance')


async def originals(conn, root, materials):
    result = []
    for row in await conn.fetch('SELECT id,storage_driver,storage_key,file_path,file_size,content_sha256 FROM activity.visit_import ORDER BY id'):
        sha = hashlib.sha256(); size = 0
        if row['storage_driver'] == 'postgres':
            index = 0
            async for part in conn.cursor('SELECT chunk_no,content FROM activity.visit_import_content WHERE import_id=$1 ORDER BY chunk_no', row['id']):
                if part['chunk_no'] != index: raise InstanceError('Original material chunks are incomplete')
                sha.update(part['content']); size += len(part['content']); index += 1
            value = sha.hexdigest()
        elif row['storage_driver'] == 'local':
            path = Path(row['storage_key'] or row['file_path'])
            if path.resolve() != path or root/'materials' not in path.parents:
                raise InstanceError('Original file lies outside the instance material directory')
            relative = str(path.relative_to(root))
            if relative not in materials: raise InstanceError('Original file is missing from the backup')
            value = materials[relative]
            size = owned_file(path, private=False).stat().st_size
        else:
            raise InstanceError('External object storage needs its own verified snapshot; incomplete backup refused')
        if size != row['file_size'] or (row['content_sha256'] and value != row['content_sha256']):
            raise InstanceError('Original material bytes differ from their database record')
        result.append({'id':str(row['id']), 'driver':row['storage_driver'], 'bytes':size, 'sha256':value})
    return result


async def verify_keys(conn, directory):
    from sales_backend.security.platform_mfa import PlatformMFACipher
    from sales_backend.security.runtime_credentials import CredentialCipher
    mfa = PlatformMFACipher.from_file(str(directory/'private/platform-mfa-keys.json'), 'instance-v1')
    provider = CredentialCipher.from_file(str(directory/'private/provider-keys.json'), 'instance-v1')
    counts = {'mfa':0, 'provider':0}
    for row in await conn.fetch('SELECT id,mfa_secret FROM control.operator WHERE mfa_secret IS NOT NULL'):
        mfa.open(str(row['id']), json.loads(row['mfa_secret'])); counts['mfa'] += 1
    for row in await conn.fetch('SELECT operator_id,mfa_pending FROM control.session WHERE mfa_pending IS NOT NULL'):
        mfa.open(str(row['operator_id']), json.loads(row['mfa_pending'])); counts['mfa'] += 1
    for table in ('config.agent_runtime_config','config.model_api_test','config.model_api_release'):
        for row in await conn.fetch('SELECT workspace_id,encryption_key_id,api_key_ciphertext FROM '+table+' WHERE api_key_ciphertext IS NOT NULL'):
            provider.decrypt(str(row['workspace_id']), row['encryption_key_id'], bytes(row['api_key_ciphertext']))
            counts['provider'] += 1
    return counts


async def inventory(conn):
    await conn.execute("SET LOCAL timezone='UTC'")
    # Baseline migrations leave search_path empty. PostgreSQL renders extension
    # argument types differently there than on a fresh maintenance connection;
    # pin the same context without discarding any ownership/ACL/RLS fields.
    await conn.execute('SET LOCAL search_path=pg_catalog,public')
    return {'tables':await table_inventory(conn),'security':await security_inventory(conn),
            'sequences':await sequence_inventory(conn)}


async def wait_for_offline(conn):
    # A terminated application's TCP backend can briefly remain in PostgreSQL.
    # Allow it to drain, but never terminate or ignore another live connection.
    deadline = time.monotonic()+3
    while True:
        await conn.execute('SELECT pg_stat_clear_snapshot()')
        remaining = await conn.fetchval('SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()')
        if remaining == 0: return
        if time.monotonic() >= deadline:
            raise InstanceError(f'Stop all instance connections before offline maintenance ({remaining} still present)')
        await asyncio.sleep(.1)


def backup_path(root, directory):
    directory = root_path(directory)
    if directory.parent != root/'backups' or not BACKUP_NAME.fullmatch(directory.name):
        raise InstanceError('Select a named private backup belonging to this instance')
    return directory


def validate_contents(directory):
    """Verify a backup without trusting or touching its original instance.

    Callers accepting a detached backup must separately bind BACKUP.json to a
    trusted digest. The existing same-instance probe keeps its stronger binding.
    """
    directory = root_path(directory)
    manifest = read_private(directory/'BACKUP.json', max_bytes=MAX_MANIFEST_BYTES)
    source = manifest.get('source_root')
    if (manifest.get('format') != 'salesbuddy-instance-backup-v1' or not isinstance(source, str)
            or not Path(source).is_absolute() or '..' in Path(source).parts or source == '/'):
        raise InstanceError('Backup belongs to another instance or format')
    required = {'database.dump','INSTANCE.json','artifact.zip', *('private/'+name for name in (
        'bootstrap.json','api.json','worker.json','feishu.json','provider-keys.json','platform-mfa-keys.json'))}
    if not required <= set(manifest.get('files', {})):
        raise InstanceError('Backup is incomplete')
    actual = set()
    for path in directory.rglob('*'):
        if path.is_symlink() or path.resolve() != path:
            raise InstanceError('Backup contains a redirected path')
        if path.is_file() and path != directory/'BACKUP.json': actual.add(str(path.relative_to(directory)))
        elif not path.is_dir() and path != directory/'BACKUP.json': raise InstanceError('Backup contains a special file')
    if actual != set(manifest['files']): raise InstanceError('Backup file list changed')
    for name, expected in manifest['files'].items():
        relative = PurePosixPath(name)
        if (relative.is_absolute() or '..' in relative.parts or str(relative) != name
                or (name not in required and not name.startswith('materials/'))):
            raise InstanceError('Invalid backup member name')
        if digest(owned_file(directory/name)) != expected:
            raise InstanceError('Backup file hash mismatch')
    saved = read_private(directory/'INSTANCE.json')
    if saved['instance_id'] != manifest['instance_id'] or saved['root'] != source:
        raise InstanceError('Backed-up identity is inconsistent')
    _, package = read_verified(directory/'artifact.zip', saved['artifact_sha256'])
    if package['revision'] != manifest['revision']:
        raise InstanceError('Backed-up artifact version is inconsistent')
    return manifest, saved


def validate_backup(root, directory):
    directory = backup_path(root, directory)
    manifest, saved = validate_contents(directory)
    if manifest['source_root'] != str(root):
        raise InstanceError('Backup belongs to another instance or format')
    current = instance(root)
    if any(saved[key] != current[key] for key in ('instance_id','database','roles')):
        raise InstanceError('Backup cannot select another database endpoint or runtime identity')
    return manifest, saved


async def backup(root, pg_bin):
    data, package = source_check(root)
    sys.path.insert(0, str(root/'release/backend/src'))
    config = database_config(data, admin=True, password=os.environ.get('SALESBUDDY_INSTALL_DB_PASSWORD'))
    conn = await asyncpg.connect(database=data['database']['name'], **config)
    parent = root/'backups'; parent.mkdir(mode=0o700, exist_ok=True); root_path(parent)
    directory = parent/('backup-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:12])
    directory.mkdir(mode=0o700)
    try:
        await check_record(conn, data)
        policy = validate_policy(json.loads((root/'release/tools/installation/runtime_permissions.json').read_text()))
        await verify(conn, data['roles'], policy)
        await wait_for_offline(conn)
        for name in ('INSTANCE.json','artifact.zip', *('private/'+name for name in data['private_hashes'])):
            copy_file(root/name, directory/name)
        materials = material_tree(root)
        for name in materials: copy_file(root/name, directory/name, private=False)
        dump = directory/'database.dump'; dump.touch(mode=0o600)
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            snapshot = await conn.fetchval('SELECT pg_export_snapshot()')
            expected = await inventory(conn)
            original_records = await originals(conn, root, materials)
            keys = await verify_keys(conn, directory)
            await asyncio.to_thread(pg_command, pg_bin, 'pg_dump', ['--format=custom','--snapshot='+snapshot,'--file='+str(dump)], data, data['database']['name'])
        if material_tree(root) != materials: raise InstanceError('Material files changed during the offline backup')
        source_check(root)
        manifest = {'format':'salesbuddy-instance-backup-v1','source_root':str(root),'instance_id':data['instance_id'],
            'revision':package['revision'],'created_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'files':{str(p.relative_to(directory)):digest(p) for p in directory.rglob('*') if p.is_file()},
            'inventory':expected,'originals':original_records,'keys_checked':keys, 'restore_verified':False}
        atomic_json(directory/'BACKUP.json', manifest, replace=False)
        validate_backup(root, directory)
        return {'backup':str(directory),'revision':data['revision'],'tables':len(expected['tables']),
                'originals':len(original_records),'material_files':len(materials),'restore_verified':False}
    finally:
        await conn.close()


async def restore_check(root, directory, pg_bin):
    manifest, data = validate_backup(root, directory)
    sys.path.insert(0, str(root/'release/backend/src'))
    config = database_config(data, admin=True, password=os.environ.get('SALESBUDDY_INSTALL_DB_PASSWORD'))
    admin = await asyncpg.connect(database='postgres', **config)
    name = 'sb_restore_probe_'+uuid.uuid4().hex[:20]
    conn = None; created = False
    try:
        # No create option is passed to pg_restore; the archive cannot choose an
        # existing target. Cluster roles and their passwords are never restored.
        await admin.execute(f'CREATE DATABASE {identifier(name)} TEMPLATE template0')
        created = True
        await admin.execute(f'REVOKE ALL ON DATABASE {identifier(name)} FROM PUBLIC')
        await asyncio.to_thread(pg_command, pg_bin, 'pg_restore', ['--exit-on-error','--single-transaction','--no-owner','--dbname='+name,str(directory/'database.dump')],data,name)
        conn = await asyncpg.connect(database=name, **config)
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            if await inventory(conn) != manifest['inventory']:
                raise InstanceError('Restored rows, ACLs, RLS or sequence definitions differ from the backup')
            if await verify_keys(conn, directory) != manifest['keys_checked']:
                raise InstanceError('Restored encryption keys differ from the backup')
            sequences = await verify_sequence_positions(conn)
        files, _ = read_verified(directory/'artifact.zip', data['artifact_sha256'])
        policy = validate_policy(json.loads(files['tools/installation/runtime_permissions.json']))
        # CONNECT grants below are uncommitted and always rolled back, so other
        # sessions never gain access to the probe. SET ROLE checks actual row
        # policies, but does not claim a new login/server or offsite restore.
        transaction = conn.transaction(); await transaction.start()
        try:
            for role in data['roles'].values():
                await conn.execute(f'GRANT CONNECT ON DATABASE {identifier(name)} TO {identifier(role)}')
            await verify(conn, data['roles'], policy)
            for service in ('api', 'worker'):
                await conn.execute('SET LOCAL ROLE '+identifier(data['roles'][service]))
                if await conn.fetchval('SELECT count(*) FROM crm.customer'):
                    raise InstanceError('Restored runtime sees customers without tenant identity')
                await conn.execute('RESET ROLE')
        finally:
            await transaction.rollback()
        validate_backup(root, directory)
        return {'status':'passed','backup':str(directory),'revision':manifest['revision'],
                'backup_manifest_sha256':digest(directory/'BACKUP.json'),
                'tables':len(manifest['inventory']['tables']), 'originals':len(manifest['originals']),
                'sequences_checked':sequences, 'keys_checked':manifest['keys_checked'],
                'restore_scope':'new database on same cluster; no role/credential changes',
                'production_changed':False,'offsite_recovery_verified':False}
    finally:
        if conn: await conn.close()
        if created: await admin.execute(f'DROP DATABASE {identifier(name)} WITH (FORCE)')
        await admin.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('backup','restore-check'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    if args.command == 'restore-check' and args.backup is None: parser.error('--backup is required')
    root = root_path(args.root)
    with locked(root), ExitStack() as locks:
        if args.command == 'backup':
            for service in ('api','worker','feishu'):
                locks.callback(os.close, service_lock(root, service))
        result = asyncio.run(backup(root, args.pg_bin) if args.command == 'backup' else restore_check(root, args.backup, args.pg_bin))
        if args.command == 'restore-check':
            # Written only after the temporary DB was successfully removed.
            atomic_json(root/'run/last-restore-check.json', {**result,'probe_database_removed':True})
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try: main()
    except (InstanceError, ValueError) as exc: raise SystemExit(str(exc)) from None
    except Exception as exc:
        # Exception values may contain SQL/data. Report code locations only.
        location = ','.join(Path(frame.filename).name+':'+str(frame.lineno) for frame in traceback.extract_tb(exc.__traceback__))
        raise SystemExit('Backup/restore check failed ('+type(exc).__name__+'; '+location+'); no existing database was replaced') from None
