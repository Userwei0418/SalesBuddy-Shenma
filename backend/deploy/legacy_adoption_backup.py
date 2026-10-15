"""Legacy backup adapter with an actual isolated database/file restore check.

All archives and logs stay private. Cluster role SQL is preserved but never
executed by this same-cluster probe. Cross-cluster disaster recovery is separate.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import pwd
import threading
import shutil
import stat
import subprocess
import uuid

import asyncpg

from database_inventory import (table_inventory, security_inventory, sequence_inventory,
                                verify_sequence_positions)
from runtime_roles import identifier
from legacy_adoption_state import AdoptionError, atomic_json, digest, literal_path, read_private, fsync_dir


def tree(path):
    path = literal_path(path)
    entries = {}
    candidates = [path, *sorted(path.rglob('*'))] if path.is_dir() and not path.is_symlink() else [path]
    for item in candidates:
        name = '.' if item == path else str(item.relative_to(path))
        info = item.lstat()
        common = {'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid}
        if stat.S_ISLNK(info.st_mode):
            entries[name] = {**common, 'kind': 'link', 'target': os.readlink(item)}
        elif stat.S_ISREG(info.st_mode):
            entries[name] = {**common, 'kind': 'file', 'sha256': digest(item), 'size': info.st_size}
        elif stat.S_ISDIR(info.st_mode):
            entries[name] = {**common, 'kind': 'directory'}
        else:
            raise AdoptionError('Backup source contains a socket, device or other special file')
    return entries


def copy_tree(source, destination):
    source = literal_path(source)
    if source.is_symlink():
        destination.symlink_to(os.readlink(source))
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        shutil.copy2(source, destination, follow_symlinks=False)
    for item in ([destination, *destination.rglob('*')] if destination.is_dir() and not destination.is_symlink() else [destination]):
        if item.is_file() and not item.is_symlink():
            with item.open('rb') as stream:
                os.fsync(stream.fileno())
    if destination.is_dir() and not destination.is_symlink():
        for item in sorted(destination.rglob('*'), key=lambda p: len(p.parts), reverse=True):
            if item.is_dir() and not item.is_symlink():
                fsync_dir(item)
        fsync_dir(destination)
    fsync_dir(destination.parent)
    # Parent is 0700. Preserve original file modes and record ownership separately;
    # restore does not chown arbitrary production paths during the probe.


def compare_copy(expected, actual):
    strip_owner = lambda entries: {k: {n: v for n, v in row.items() if n not in ('uid', 'gid')}
                                  for k, row in entries.items()}
    return strip_owner(expected) == strip_owner(actual)


_PEER_GATE = threading.Lock()


def peer_identity(spec):
    auth = spec['maintenance_auth']
    if auth['mode'] != 'peer':
        return None
    if not spec['database']['host'].startswith('/') or spec['database']['administrator'] != auth['os_user']:
        raise AdoptionError('Peer mode requires a local Unix socket and matching database/OS identity')
    account = pwd.getpwnam(auth['os_user'])
    if account.pw_uid == 0 or os.geteuid() not in (0, account.pw_uid):
        raise AdoptionError('Peer connection requires root or its explicit non-root maintenance OS identity')
    return account


def pg_env(spec, database):
    data = spec['database']
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'LC_ALL') if k in os.environ}
    env.update(PGHOST=data['host'], PGPORT=str(data['port']), PGUSER=data['administrator'],
               PGDATABASE=database, PGSSLMODE=data['sslmode'], PGCONNECT_TIMEOUT='10')
    if spec['maintenance_auth']['mode'] == 'password' and os.environ.get('SALESBUDDY_ADOPTION_DB_PASSWORD'):
        env['PGPASSWORD'] = os.environ['SALESBUDDY_ADOPTION_DB_PASSWORD']
    return env


async def connect(spec, database=None):
    data = spec['database']
    account = peer_identity(spec)
    if not _PEER_GATE.acquire(blocking=False):
        raise AdoptionError('Concurrent peer connection/backup operation refused')
    uid, gid, groups = os.geteuid(), os.getegid(), os.getgroups()
    changed = account is not None and uid == 0
    try:
        if changed:
            os.setgroups([])
            os.setegid(account.pw_gid)
            os.seteuid(account.pw_uid)
        return await asyncpg.connect(host=data['host'], port=data['port'], user=data['administrator'],
                                     database=database or data['name'], ssl=False if account else data['sslmode'],
                                     password=None if account else os.environ.get('SALESBUDDY_ADOPTION_DB_PASSWORD'),
                                     command_timeout=600)
    finally:
        if changed:
            # Restore effective root first so the original group identities can
            # be restored even when connect raises/cancels. No file work occurs
            # while temporarily assuming the maintenance identity.
            os.seteuid(uid)
            os.setegid(gid)
            os.setgroups(groups)
        _PEER_GATE.release()


def pg_run(spec, tool, args, *, database=None, output=None, input_file=None):
    executable = Path(spec['pg_bin'])/tool
    check = subprocess.run([str(executable), '--version'], capture_output=True, timeout=10)
    if check.returncode or not re.search(rb'\(PostgreSQL\) 16\.', check.stdout):
        raise AdoptionError('Explicit PostgreSQL 16 client tools are required')
    stream = incoming = None
    if not _PEER_GATE.acquire(blocking=False):
        raise AdoptionError('Concurrent peer connection/backup operation refused')
    try:
        account = peer_identity(spec)
        identities = ({'user': account.pw_uid, 'group': account.pw_gid, 'extra_groups': []}
                      if account is not None and os.geteuid() == 0 else {})
        if input_file:
            descriptor = os.open(input_file, os.O_RDONLY | os.O_NOFOLLOW)
            incoming = os.fdopen(descriptor, 'rb')
        if output:
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            stream = os.fdopen(fd, 'wb')
        result = subprocess.run([str(executable), *args], stdout=stream or subprocess.PIPE,
                                stderr=subprocess.PIPE, env=pg_env(spec, database or spec['database']['name']),
                                stdin=incoming, cwd='/', timeout=1800, **identities)
        if stream:
            stream.flush()
            os.fsync(stream.fileno())
        if result.returncode or result.stderr.strip():
            raise AdoptionError('PostgreSQL backup/restore command failed; private partial evidence retained')
    finally:
        if stream:
            stream.close()
        if incoming:
            incoming.close()
        _PEER_GATE.release()


async def inventory(conn):
    await conn.execute("SET LOCAL timezone='UTC'")
    await conn.execute('SET LOCAL search_path=pg_catalog,public')
    return {'tables': await table_inventory(conn), 'security': await security_inventory(conn),
            'sequences': await sequence_inventory(conn)}


def backed_path(directory, manifest, path):
    path = literal_path(path)
    for entry in manifest['paths']:
        root = Path(entry['source'])
        if path == root or root in path.parents:
            target = directory/entry['copy']
            return target if path == root else target/path.relative_to(root)
    raise AdoptionError('Required original/configuration file was not included in the backup')


async def verify_materials(conn, directory, manifest):
    count = 0
    for row in await conn.fetch('''SELECT id,storage_driver,storage_key,file_path,file_size,content_sha256
                                  FROM activity.visit_import ORDER BY id'''):
        if row['storage_driver'] == 'postgres':
            import hashlib
            sha, size, index = hashlib.sha256(), 0, 0
            async for part in conn.cursor('SELECT chunk_no,content FROM activity.visit_import_content WHERE import_id=$1 ORDER BY chunk_no', row['id']):
                if part['chunk_no'] != index:
                    raise AdoptionError('Original material chunks are incomplete')
                sha.update(part['content']); size += len(part['content']); index += 1
            value = sha.hexdigest()
        elif row['storage_driver'] == 'local':
            file = backed_path(directory, manifest, row['storage_key'] or row['file_path'])
            if file.is_symlink() or file.resolve() != file or not file.is_file():
                raise AdoptionError('Original material is missing or redirected in backup')
            value, size = digest(file), file.stat().st_size
        else:
            raise AdoptionError('External original storage requires a separately implemented restore adapter')
        if size != row['file_size'] or (row['content_sha256'] and value != row['content_sha256']):
            raise AdoptionError('Original material size or checksum mismatch')
        count += 1
    return count


async def verify_keys(conn, directory, manifest, spec):
    from sales_backend.security.runtime_credentials import CredentialCipher
    key = spec['provider_keyring']
    key_path = backed_path(directory, manifest, key['path'])
    if key_path.is_symlink() or key_path.resolve() != key_path:
        raise AdoptionError('Backed-up credential keyring must not refer to a live source path')
    cipher = CredentialCipher.from_file(str(key_path), key['key_id'])
    count = 0
    for table in ('config.agent_runtime_config', 'config.model_api_test', 'config.model_api_release'):
        for row in await conn.fetch('SELECT workspace_id,encryption_key_id,api_key_ciphertext FROM '+table+' WHERE api_key_ciphertext IS NOT NULL'):
            cipher.decrypt(str(row['workspace_id']), row['encryption_key_id'], bytes(row['api_key_ciphertext']))
            count += 1
    for row in await conn.fetch('SELECT workspace_id,encryption_key_id,ciphertext FROM security.feishu_credential'):
        cipher.decrypt(str(row['workspace_id']), row['encryption_key_id'], bytes(row['ciphertext']))
        count += 1
    return count


def validate_backup(directory, expected_hash):
    if digest(directory/'BACKUP.json') != expected_hash:
        raise AdoptionError('Backup manifest changed')
    manifest = read_private(directory/'BACKUP.json')
    if (manifest.get('format') != 'salesbuddy-legacy-backup-v1'
            or set(manifest.get('archives', {})) != {'database.dump', 'roles.sql'}
            or not isinstance(manifest.get('paths'), list)):
        raise AdoptionError('Unknown backup manifest format')
    copies = set()
    for item in manifest['paths']:
        if not re.fullmatch(r'files/[0-9]+', item.get('copy', '')) or item['copy'] in copies:
            raise AdoptionError('Invalid or duplicate backup member path')
        copies.add(item['copy'])
        literal_path(item['source'])
        if not isinstance(item.get('entries'), dict) or '.' not in item['entries']:
            raise AdoptionError('Incomplete backed-up file inventory')
        if not compare_copy(item['entries'], tree(directory/item['copy'])):
            raise AdoptionError('Backed-up files changed')
    for name, sha in manifest['archives'].items():
        path = directory/name
        if path.is_symlink() or path.resolve() != path or digest(path) != sha:
            raise AdoptionError('Backup archive changed')
    return manifest


async def create_backup(conn, spec, directory):
    directory.mkdir(mode=0o700)
    manifest = {'format': 'salesbuddy-legacy-backup-v1', 'paths': []}
    for index, path in enumerate(spec['backup_paths']):
        source = literal_path(path)
        entries = tree(source)
        relative = 'files/'+str(index)
        (directory/'files').mkdir(mode=0o700, exist_ok=True)
        copy_tree(source, directory/relative)
        if tree(source) != entries or not compare_copy(entries, tree(directory/relative)):
            raise AdoptionError('Backup source changed while copying')
        manifest['paths'].append({'source': str(source), 'copy': relative, 'entries': entries})
    async with conn.transaction(isolation='repeatable_read', readonly=True):
        snapshot = await conn.fetchval('SELECT pg_export_snapshot()')
        manifest['inventory'] = await inventory(conn)
        manifest['originals'] = await verify_materials(conn, directory, manifest)
        manifest['encrypted_credentials'] = await verify_keys(conn, directory, manifest, spec)
        await asyncio.to_thread(pg_run, spec, 'pg_dump', ['--format=custom', '--snapshot='+snapshot],
                                output=directory/'database.dump')
        await asyncio.to_thread(pg_run, spec, 'pg_dumpall', ['--roles-only'], output=directory/'roles.sql')
    for item in manifest['paths']:
        if tree(item['source']) != item['entries']:
            raise AdoptionError('Original/configuration files changed while database was backed up')
    manifest['archives'] = {name: digest(directory/name) for name in ('database.dump', 'roles.sql')}
    atomic_json(directory/'BACKUP.json', manifest, replace=False)
    return digest(directory/'BACKUP.json')


async def restore_check(spec, directory, expected_hash):
    manifest = validate_backup(directory, expected_hash)
    admin = await connect(spec, 'postgres')
    name = 'sb_legacy_probe_'+uuid.uuid4().hex[:20]
    conn, created = None, False
    try:
        await admin.execute(f'CREATE DATABASE {identifier(name)} TEMPLATE template0')
        created = True
        await admin.execute(f'REVOKE ALL ON DATABASE {identifier(name)} FROM PUBLIC')
        await asyncio.to_thread(pg_run, spec, 'pg_restore',
                                ['--exit-on-error', '--single-transaction', '--dbname='+name],
                                database=name, input_file=directory/'database.dump')
        conn = await connect(spec, name)
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            if await inventory(conn) != manifest['inventory']:
                raise AdoptionError('Restored data, ownership, ACL or isolation differs from backup')
            if await verify_materials(conn, directory, manifest) != manifest['originals']:
                raise AdoptionError('Restored original materials differ')
            if await verify_keys(conn, directory, manifest, spec) != manifest['encrypted_credentials']:
                raise AdoptionError('Restored encrypted credentials differ')
            await verify_sequence_positions(conn)
        # Actually copy saved files into a second isolated tree, not just hash
        # the backup. Symlinks are preserved without dereferencing or execution.
        restore = directory/'restored-files'
        restore.mkdir(mode=0o700)
        for index, item in enumerate(manifest['paths']):
            copy_tree(directory/item['copy'], restore/str(index))
            if not compare_copy(item['entries'], tree(restore/str(index))):
                raise AdoptionError('File restore differs from saved originals')
        validate_backup(directory, expected_hash)
        return {'database_rows_acl_rls_verified': True, 'file_restore_verified': True,
                'originals': manifest['originals'], 'encrypted_credentials': manifest['encrypted_credentials'],
                'same_cluster_only': True, 'cluster_roles_restored': False}
    finally:
        if conn:
            await conn.close()
        if created:
            await admin.execute(f'DROP DATABASE {identifier(name)} WITH (FORCE)')
        await admin.close()
