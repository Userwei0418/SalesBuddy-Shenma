#!/usr/bin/env python3
"""Controlled V152 adoption. Requires an explicit private spec and maintenance credentials.

Commands are deliberately separate maintenance gates. `start` is explicit and
must follow operator bootstrap/MFA preparation. This tool never uploads a client,
changes business data deliberately, sends a notification or fabricates an installer
marker. Production use requires the matching isolated Linux rehearsal receipt.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from urllib.parse import quote, urlsplit
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools/installation'))
sys.path.insert(0, str(ROOT/'backend/src'))
from release_bundle import read_verified
from runtime_roles import SERVICES, identifier, load_policy
from upgrade_permissions import cluster_identity
from inherit_service_environment import environment_files, service_identity, service_property
from legacy_adoption_state import (AdoptionError, atomic_bytes, atomic_json, checkpoint, digest,
                                   fingerprint, fsync_dir, literal_path, locked, private_directory, read_private)
from legacy_adoption_backup import (connect, create_backup, inventory, restore_check,
                                    validate_backup, tree)
from legacy_adoption_database import (drained, identity, ledger, migrate, migration_ledger,
                                      no_connections, old_role_gate, state, verify_connections)

SERVICE_NAMES = {'api': 'sales-api.service', 'worker': 'sales-worker.service', 'feishu': 'sales-feishu-worker.service'}
ADOPTION_DROPIN = 'zzzzzz-saas-adoption.conf'
SPEC_KEYS = {'format', 'database', 'runtime_host', 'pg_bin', 'old_release', 'new_release', 'old_revision',
             'new_revision', 'archive', 'archive_sha256', 'current_link', 'releases_file', 'roles',
             'retire_roles', 'backup_paths', 'provider_keyring', 'mfa_file', 'environment_files', 'dropins', 'api_port',
             'uv_binary', 'uv_sha256', 'maintenance_auth'}


def validate_spec(spec):
    if set(spec) != SPEC_KEYS or spec['format'] != 'salesbuddy-legacy-adoption-v1':
        raise AdoptionError('Unknown or incomplete legacy adoption specification')
    if set(spec['database']) != {'name', 'host', 'port', 'administrator', 'sslmode'}:
        raise AdoptionError('Explicit database endpoint and maintenance identity required')
    auth = spec['maintenance_auth']
    if (not isinstance(auth, dict) or auth.get('mode') not in ('peer', 'password')
            or set(auth) != ({'mode', 'os_user'} if auth.get('mode') == 'peer' else {'mode'})):
        raise AdoptionError('Explicit peer or password maintenance authentication required')
    if auth['mode'] == 'peer':
        if auth['os_user'] != spec['database']['administrator'] or not spec['database']['host'].startswith('/'):
            raise AdoptionError('Peer authentication requires matching OS/database user and Unix socket')
        literal_path(spec['database']['host'])
    for name in [spec['database']['name'], spec['database']['administrator'], *spec['roles'].values(), *spec['retire_roles']]:
        identifier(name)
    if (set(spec['roles']) != SERVICES or len(set(spec['roles'].values())) != 4
            or not spec['retire_roles'] or len(set(spec['retire_roles'])) != len(spec['retire_roles'])
            or set(spec['retire_roles']) & set(spec['roles'].values())
            or spec['database']['administrator'] in {*spec['retire_roles'], *spec['roles'].values()}):
        raise AdoptionError('Four new runtime identities and exclusive legacy logins required')
    if any(not re.fullmatch(r'[a-f0-9]{40}', spec[k]) for k in ('old_revision', 'new_revision')):
        raise AdoptionError('Full old and new commit identities required')
    if spec['old_revision'] == spec['new_revision']:
        raise AdoptionError('Legacy and candidate releases must differ')
    if (type(spec['api_port']) is not int or not 1 <= spec['api_port'] <= 65535
            or type(spec['database']['port']) is not int or not 1 <= spec['database']['port'] <= 65535
            or spec['database']['sslmode'] not in {'disable', 'require', 'verify-ca', 'verify-full'}):
        raise AdoptionError('Invalid port or TLS mode')
    for value in (spec['database']['host'], spec['runtime_host']):
        if not value or any(ord(c) <= 32 for c in value) or any(c in value for c in ('%', '"', '\\', '$', '`')):
            raise AdoptionError('Invalid database host')
    if set(spec['environment_files']) != set(SERVICE_NAMES) or set(spec['dropins']) != set(SERVICE_NAMES):
        raise AdoptionError('Three explicit systemd overrides are required')
    if not re.fullmatch(r'[A-Za-z0-9.-]+', spec['runtime_host']):
        raise AdoptionError('Runtime DSN requires an explicit hostname or IPv4 address')
    if not re.fullmatch(r'[a-f0-9]{64}', spec['uv_sha256']):
        raise AdoptionError('Supply the reviewed uv executable SHA-256')
    paths = [spec[k] for k in ('uv_binary', 'pg_bin', 'old_release', 'new_release', 'archive', 'current_link', 'releases_file', 'mfa_file')]
    paths += [*spec['environment_files'].values(), *spec['dropins'].values(), *spec['backup_paths'], spec['provider_keyring']['path']]
    for value in paths:
        literal_path(value)
        if any(c in value for c in ('\n', '\r', '"', '\\', '%', '$', '`', ' ')):
            raise AdoptionError('Deployment paths must have unambiguous systemd syntax')
    if not spec['backup_paths'] or len(set(spec['backup_paths'])) != len(spec['backup_paths']):
        raise AdoptionError('Complete explicit backup paths required')
    if Path(spec['old_release']) == Path(spec['new_release']):
        raise AdoptionError('Never overwrite the running release directory')
    for service, path in spec['dropins'].items():
        if Path(path).parent.name != SERVICE_NAMES[service]+'.d' or Path(path).name != ADOPTION_DROPIN:
            raise AdoptionError('Dedicated named systemd drop-in required')
    outputs = [*spec['dropins'].values(), *spec['environment_files'].values(), spec['mfa_file']]
    if len(set(outputs)) != len(outputs):
        raise AdoptionError('Configuration output paths overlap')
    return spec


def command(args, *, timeout=30, env=None):
    result = subprocess.run(args, capture_output=True, timeout=timeout, env=env)
    if result.returncode:
        raise AdoptionError('Deployment command failed; output suppressed to protect configuration')
    return result.stdout


def systemctl(*args):
    return command(['systemctl', *args]).decode().strip()


def service_snapshot():
    result = {}
    for key, service in SERVICE_NAMES.items():
        data = systemctl('show', service, '-p', 'LoadState', '-p', 'MainPID', '-p', 'ActiveState', '-p', 'EnvironmentFiles', '-p', 'FragmentPath', '-p', 'DropInPaths')
        props = dict(line.split('=', 1) for line in data.splitlines() if '=' in line)
        # systemctl emits one EnvironmentFiles= line per file. A dictionary
        # retains only the last entry and silently drops the inherited sources.
        file_metadata = '\n'.join(line.partition('=')[2] for line in data.splitlines()
                                  if line.startswith('EnvironmentFiles='))
        if props.get('LoadState') != 'loaded':
            raise AdoptionError('Expected legacy service is not loaded')
        config_paths = [props['FragmentPath'], *props.get('DropInPaths', '').split()]
        if any(not path for path in config_paths):
            raise AdoptionError('Service has no concrete unit file')
        result[key] = {'unit_sha256': fingerprint(systemctl('cat', service)),
                       'configuration_files': {path: digest(literal_path(path)) for path in config_paths},
                       'environment_files': environment_files(file_metadata),
                       'active': props['ActiveState'], 'pid': int(props['MainPID'])}
    return result


def release_check(spec):
    uv = literal_path(spec['uv_binary'])
    if uv.is_symlink() or not uv.is_file() or uv.stat().st_mode & 0o022 or digest(uv) != spec['uv_sha256']:
        raise AdoptionError('Reviewed uv executable changed or has unsafe permissions')
    files, package = read_verified(spec['archive'], spec['archive_sha256'])
    package_web_origin(package)
    if package['revision'] != spec['new_revision']:
        raise AdoptionError('Candidate package revision differs')
    for name, content in files.items():
        path = Path(spec['new_release'])/name
        if path.is_symlink() or path.resolve() != path or not path.is_file() or digest(path) != __import__('hashlib').sha256(content).hexdigest():
            raise AdoptionError('Candidate release differs from verified product package')
    # Extra Python/SQL files can shadow or expand the reviewed program.
    for directory, suffixes in [('backend/src', ('.py',)), ('database', ('.sql', '.py')), ('tools/installation', ('.py', '.json'))]:
        for path in (Path(spec['new_release'])/directory).rglob('*'):
            if path.is_file() and path.suffix in suffixes and str(path.relative_to(spec['new_release'])) not in files:
                raise AdoptionError('Candidate contains unreviewed executable source')
    if (Path(spec['old_release'])/'REVISION').read_text().strip() != spec['old_revision']:
        raise AdoptionError('Legacy release revision differs')
    return package


def assert_offline():
    for service in SERVICE_NAMES.values():
        data = systemctl('show', service, '-p', 'ActiveState', '-p', 'MainPID')
        props = dict(line.split('=', 1) for line in data.splitlines() if '=' in line)
        if props.get('ActiveState') not in {'inactive', 'failed'} or props.get('MainPID') != '0':
            raise AdoptionError('All three services must remain stopped')


def maintenance_files(directory, spec):
    if any(c in str(directory) for c in ('\n', '\r', '"', '\\', '%', '$', '`', ' ')):
        raise AdoptionError('Maintenance path must have unambiguous systemd syntax')
    return {str(Path(path).parent/'80-saas-maintenance.conf'):
            ('[Unit]\nConditionPathExists=!'+str(directory/'maintenance.block')+'\n').encode()
            for path in spec['dropins'].values()}


def set_maintenance(directory, journal):
    atomic_bytes(directory/'maintenance.block', b'SaaS adoption in progress\n')
    for path, content in maintenance_files(directory, journal['spec']).items():
        ensure_file(path, content)
    systemctl('daemon-reload')


def clear_maintenance_marker(directory):
    marker = directory/'maintenance.block'
    if marker.exists():
        if marker.is_symlink():
            raise AdoptionError('Maintenance marker was redirected')
        marker.unlink()
        fsync_dir(directory)


def require_maintenance(directory, journal):
    if not (directory/'maintenance.block').is_file() or (directory/'maintenance.block').is_symlink():
        raise AdoptionError('Persistent maintenance guard is required before offline changes')
    for path, content in maintenance_files(directory, journal['spec']).items():
        if Path(path).is_symlink() or Path(path).read_bytes() != content:
            raise AdoptionError('Persistent systemd maintenance guard differs')


def original_files_unchanged(journal):
    spec = journal['spec']
    directory = Path(journal['directory'])
    if Path(spec['current_link']).resolve() != Path(spec['old_release']):
        raise AdoptionError('Current release changed before database commit')
    guards = maintenance_files(directory, spec)
    for key, row in service_snapshot().items():
        expected = dict(journal['services'][key]['configuration_files'])
        guard = str(Path(spec['dropins'][key]).parent/'80-saas-maintenance.conf')
        if Path(guard).exists():
            if Path(guard).is_symlink() or Path(guard).read_bytes() != guards[guard]:
                raise AdoptionError('Unexpected preexisting maintenance guard')
            expected[guard] = digest(guard)
        if row['configuration_files'] != expected:
            raise AdoptionError('Legacy service files changed since preparation')


def read_journal(directory):
    journal = read_private(directory/'ADOPTION.json')
    if (journal.get('format') != 'salesbuddy-legacy-adoption-v1'
            or journal.get('directory') != str(directory)
            or fingerprint(validate_spec(journal['spec'])) != journal['spec_sha256']):
        raise AdoptionError('Invalid adoption journal or changed specification')
    return journal


def check_override_order(spec, services):
    for key, row in services.items():
        for path in row['configuration_files']:
            if Path(path).parent.name.endswith('.d') and Path(path).name >= Path(spec['dropins'][key]).name:
                raise AdoptionError('Existing systemd drop-in sorts after the dedicated adoption override')


async def prepare(directory, spec):
    validate_spec(spec)
    package = release_check(spec)
    if Path(spec['current_link']).resolve() != Path(spec['old_release']):
        raise AdoptionError('Current link does not identify the reviewed legacy release')
    guards = maintenance_files(directory, spec)
    if any(Path(p).exists() or Path(p).is_symlink() for p in [*guards, spec['mfa_file'], *spec['environment_files'].values(), *spec['dropins'].values()]):
        raise AdoptionError('New configuration output already exists; do not replace credentials')
    services = service_snapshot()
    check_override_order(spec, services)
    for service in SERVICE_NAMES.values():
        unset = service_property(service, 'UnsetEnvironment', runner=subprocess.run)
        if any(item.partition('=')[0] in {'DATABASE_URL', 'FEISHU_SYNC_DATABASE_URL', 'PLATFORM_DATABASE_URL', 'PLATFORM_MFA_KEYRING_FILE', 'PLATFORM_MFA_KEY_ID', 'PYTHONPATH', 'PYTHONDONTWRITEBYTECODE', 'PUBLIC_WEB_ORIGIN'} for item in unset):
            raise AdoptionError('Service UnsetEnvironment conflicts with required adoption configuration')
    for row in services.values():
        for path in row['configuration_files']:
            if not any(Path(path) == Path(p) or Path(p) in Path(path).parents for p in spec['backup_paths']):
                raise AdoptionError('A systemd unit/drop-in is missing from backup scope')
        if row['active'] != 'active' or not row['pid']:
            raise AdoptionError('Prepare requires the verified running legacy services')
        if Path(f'/proc/{row["pid"]}/cwd').resolve() != Path(spec['old_release']):
            raise AdoptionError('Actual legacy process directory differs from current')
        for path in row['environment_files']:
            path = path.lstrip('-')
            if Path(path).exists() and not any(Path(path) == Path(p) or Path(p) in Path(path).parents for p in spec['backup_paths']):
                raise AdoptionError('A systemd environment file is missing from backup scope')
    required = [spec['old_release'], spec['releases_file'], spec['provider_keyring']['path']]
    if any(not any(Path(p) == Path(root) or Path(root) in Path(p).parents for root in spec['backup_paths']) for p in required):
        raise AdoptionError('Old release, release metadata and provider keyring must be backed up')
    if any(Path(p) == directory or Path(p) in directory.parents for p in spec['backup_paths']):
        raise AdoptionError('Backup source cannot contain its adoption/backup directory')
    conn = await connect(spec)
    try:
        database_identity = await identity(conn, spec)
        old = await ledger(conn)
        expected_old = {k: v for k, v in migration_ledger(spec['new_release']).items() if not k.startswith('V') or int(k[1:]) <= 152}
        if old != expected_old or await conn.fetchval('SELECT max(version) FROM ops.schema_migration') != 'V152':
            raise AdoptionError('Expected exact V152 historical checksums and schema ledger')
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=ANY($1::text[]))', list(spec['roles'].values())):
            raise AdoptionError('New runtime role already exists; no automatic adoption or password reset')
        target = migration_ledger(spec['new_release'])
        policy = load_policy(Path(spec['new_release'])/'tools/installation/runtime_permissions.json')
        if fingerprint(target) != policy['migration_fingerprint'] or package['expected_schema'] != policy['schema']:
            raise AdoptionError('Candidate migration and permission policies differ')
        journal = {'format': 'salesbuddy-legacy-adoption-v1', 'directory': str(directory), 'spec': spec,
                   'spec_sha256': fingerprint(spec), 'database_identity': database_identity,
                   'before_ledger': old, 'after_ledger': target, 'before_cluster': fingerprint(await cluster_identity(conn)),
                   'pending': sorted(set(target)-set(old)), 'services': services}
        atomic_json(directory/'ADOPTION.json', {**journal, 'phase': 'preparing'}, replace=False)
        # All passwords become durable before any CREATE ROLE statement is sent.
        atomic_json(directory/'credentials.json', {service: secrets.token_urlsafe(48) for service in SERVICES}, replace=False)
        atomic_json(directory/'mfa.json', {'keys': {'adoption-v1': base64.b64encode(secrets.token_bytes(32)).decode()}}, replace=False)
        journal['credentials_sha256'] = digest(directory/'credentials.json')
        journal['mfa_sha256'] = digest(directory/'mfa.json')
        checkpoint(directory, journal, 'prepared')
        return journal
    finally:
        await conn.close()


def validate_secrets(directory, journal):
    if any(digest(directory/name) != journal[key] for name, key in
           [('credentials.json', 'credentials_sha256'), ('mfa.json', 'mfa_sha256')]):
        raise AdoptionError('Persisted new credentials changed; no reset attempted')
    return read_private(directory/'credentials.json')


def install_dependencies(spec):
    runtime = Path(spec['new_release'])/'.venv'
    if runtime.is_symlink() or runtime.resolve() != runtime or (runtime.exists() and runtime.stat().st_mode & 0o022):
        raise AdoptionError('Candidate dependency directory is redirected or shared writable')
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'LC_ALL') if k in os.environ}
    env.update(UV_PROJECT_ENVIRONMENT=str(runtime), UV_LINK_MODE='copy')
    uv = literal_path(spec['uv_binary'])
    if uv.is_symlink() or digest(uv) != spec['uv_sha256']:
        raise AdoptionError('Reviewed dependency tool changed')
    command([str(uv), '--no-config', 'sync', '--locked', '--no-dev', '--no-editable', '--python', '3.12',
             '--project', str(Path(spec['new_release'])/'backend')], timeout=600, env=env)
    command([str(runtime/'bin/python'), '-c', 'import asyncpg,segno,cryptography,sales_backend'], env=env)


def package_web_origin(package):
    url = urlsplit(package['api_base_url'])
    if not re.fullmatch(r'(?:[A-Za-z0-9.-]+|\[[a-fA-F0-9:]+\])(?::[0-9]+)?', url.netloc):
        raise AdoptionError('Candidate public origin has unsafe environment syntax')
    return url.scheme+'://'+url.netloc


def candidate_web_origin(spec):
    # This is a frozen package field, never an incoming HTTP Host header.
    _, package = read_verified(spec['archive'], spec['archive_sha256'])
    return package_web_origin(package)


def config_bytes(spec, credentials, public_web_origin):
    def dsn(service):
        return 'postgresql://'+spec['roles'][service]+':'+quote(credentials[service], safe='')+'@'+spec['runtime_host']+':'+str(spec['database']['port'])+'/'+spec['database']['name']+'?sslmode='+spec['database']['sslmode']
    result = {}
    for service in SERVICE_NAMES:
        value = 'DATABASE_URL='+dsn(service)+'\n'
        value += 'PYTHONPATH='+spec['new_release']+'/backend/src\nPYTHONDONTWRITEBYTECODE=1\n'
        value += 'FEISHU_SYNC_DATABASE_URL='+(dsn(service) if service == 'feishu' else '')+'\n'
        if service == 'api':
            value += 'PLATFORM_DATABASE_URL='+dsn('platform')+'\nPLATFORM_MFA_KEYRING_FILE='+spec['mfa_file']+'\nPLATFORM_MFA_KEY_ID=adoption-v1\n'
            value += 'PUBLIC_WEB_ORIGIN='+public_web_origin+'\n'
        else:
            # Existing shared API env may contain the platform DSN. Workers must
            # not retain it after inheriting their older configuration.
            value += 'PLATFORM_DATABASE_URL=\nPLATFORM_MFA_KEYRING_FILE=\nPLATFORM_MFA_KEY_ID=\n'
        result[service] = value.encode()
    return result


def ensure_file(path, content, *, uid=None, gid=None):
    path = Path(path)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.read_bytes() != content or path.stat().st_mode & 0o077:
            raise AdoptionError('Configuration target already exists with different content or unsafe permissions')
    else:
        atomic_bytes(path, content, replace=False)
    if uid is not None:
        os.chown(path, uid, gid)


def service_argv(spec, key):
    prefix = [spec['new_release']+'/.venv/bin/python', '-m']
    return prefix + ({'api': ['uvicorn', 'sales_backend.main:app', '--host', '127.0.0.1', '--port', str(spec['api_port']), '--workers', '1'],
                      'worker': ['sales_backend.worker'], 'feishu': ['sales_backend.feishu_worker']}[key])


def verify_effective_service(spec, key):
    """Check resolved systemd properties, not merely our drop-in's bytes."""
    service = SERVICE_NAMES[key]
    if service_property(service, 'WorkingDirectory', runner=subprocess.run, value_type='s') != spec['current_link']:
        raise AdoptionError('Effective systemd working directory differs from adoption configuration')
    suffix = ''.join(c if c.isascii() and c.isalnum() else f'_{ord(c):02x}' for c in service)
    value = json.loads(command(['busctl', '--system', '--json=short', 'get-property',
        'org.freedesktop.systemd1', '/org/freedesktop/systemd1/unit/'+suffix,
        'org.freedesktop.systemd1.Service', 'ExecStart']))
    entries = value.get('data')
    expected = service_argv(spec, key)
    if (value.get('type') != 'a(sasbttttuii)' or not isinstance(entries, list) or len(entries) != 1
            or not isinstance(entries[0], list) or len(entries[0]) != 10
            or entries[0][0] != expected[0] or entries[0][1] != expected or entries[0][2] is not False):
        raise AdoptionError('Effective systemd ExecStart differs from adoption configuration')


def switch(directory, journal, credentials, *, fault=None):
    spec = journal['spec']
    assert_offline()
    require_maintenance(directory, journal)
    for row in journal['services'].values():
        for name, sha in row['configuration_files'].items():
            if digest(literal_path(name)) != sha:
                raise AdoptionError('Original service configuration changed during adoption')
    if Path(spec['current_link']).resolve() not in (Path(spec['old_release']), Path(spec['new_release'])):
        raise AdoptionError('Current points to an unrelated release')
    for service, content in config_bytes(spec, credentials, candidate_web_origin(spec)).items():
        ensure_file(spec['environment_files'][service], content)
    uid, gid = service_identity(SERVICE_NAMES['api'], runner=subprocess.run)
    ensure_file(spec['mfa_file'], (directory/'mfa.json').read_bytes(), uid=uid, gid=gid)
    for key, service in SERVICE_NAMES.items():
        # Inherit the original environment files and provider keys, overriding
        # only dedicated DB/MFA values via the LAST EnvironmentFile.
        content = ('[Service]\nWorkingDirectory='+spec['current_link']+'\nExecStart=\nExecStart='+
                   ' '.join(service_argv(spec, key))+'\nEnvironmentFile='+spec['environment_files'][key]+'\n').encode()
        ensure_file(spec['dropins'][key], content)
        checkpoint(directory, journal, 'configuration_'+key, fault=fault)
    journal['installed_files'] = {path: digest(path) for path in
                                  [*spec['environment_files'].values(), *spec['dropins'].values(), *maintenance_files(directory, spec), spec['mfa_file']]}
    link = Path(spec['current_link'])
    temporary = link.parent/('.adoption-current-'+uuid.uuid4().hex)
    temporary.symlink_to(spec['new_release'])
    os.replace(temporary, link)
    fsync_dir(link.parent)
    checkpoint(directory, journal, 'current_switched', fault=fault)
    systemctl('daemon-reload')
    verify_installed_unit_files(journal)
    # Preserve the original metadata in the verified backup. Unknown extra keys
    # stay intact, while current/previous are updated using the existing shape.
    record = json.loads(Path(spec['releases_file']).read_text())
    record.update(previous=Path(spec['old_release']).name, current=Path(spec['new_release']).name,
                  revision=spec['new_revision'], consistent=False,
                  rollback_requires_database_restore=True)
    record['services'] = []
    record['releases'] = [
        {'release': Path(spec['new_release']).name, 'revision': spec['new_revision'], 'purpose': 'current'},
        {'release': Path(spec['old_release']).name, 'revision': spec['old_revision'], 'purpose': 'requires_database_restore'}]

    atomic_json(spec['releases_file'], record)
    checkpoint(directory, journal, 'configuration_switched', fault=fault)


def verify_installed_unit_files(journal):
    spec = journal['spec']
    for name, sha in journal['installed_files'].items():
        path = literal_path(name)
        if path.is_symlink() or path.stat().st_mode & 0o077 or digest(path) != sha:
            raise AdoptionError('Installed runtime configuration changed or became readable to others')
    for key, row in service_snapshot().items():
        guard = str(Path(spec['dropins'][key]).parent/'80-saas-maintenance.conf')
        expected = {**journal['services'][key]['configuration_files'],
                    guard: journal['installed_files'][guard],
                    spec['dropins'][key]: journal['installed_files'][spec['dropins'][key]]}
        if row['configuration_files'] != expected:
            raise AdoptionError('Installed unit set differs from original plus approved drop-in')
        if row['environment_files'] != [*journal['services'][key]['environment_files'], spec['environment_files'][key]]:
            raise AdoptionError('Effective runtime EnvironmentFiles differ from original plus final adoption override')
        verify_effective_service(spec, key)


async def execute(action, directory, spec=None):
    if action == 'prepare':
        return await prepare(directory, spec)
    journal = read_journal(directory)
    spec = journal['spec']
    if action in ('status', 'abort'):
        conn = await connect(spec)
        try:
            result = await state(conn, journal)
            if action == 'abort':
                if result != 'old':
                    raise AdoptionError('Cannot abort committed or unknown database state; forward recovery required')
                original_files_unchanged(journal)
                # prepare never dispatches SQL or switches files; its incomplete
                # private credentials may safely remain for forensic review.
                if journal['phase'] not in ('preparing', 'prepared', 'aborted'):
                    assert_offline()
                    await no_connections(conn)
                for path, content in maintenance_files(directory, spec).items():
                    target = Path(path)
                    if target.exists():
                        if target.is_symlink() or target.read_bytes() != content:
                            raise AdoptionError('Cannot remove an unrelated maintenance guard')
                        target.unlink()
                        fsync_dir(target.parent)
                clear_maintenance_marker(directory)
                systemctl('daemon-reload')
                checkpoint(directory, journal, 'aborted')
        finally:
            await conn.close()
        return {**journal, 'database_state': result}
    if journal['phase'] == 'preparing':
        raise AdoptionError('Preparation interrupted before SQL; use status then abort, retain evidence and prepare a new directory')
    if journal['phase'] == 'aborted':
        raise AdoptionError('This adoption was aborted; retain evidence and prepare a new directory')
    credentials = validate_secrets(directory, journal)
    release_check(spec)
    if action == 'stop':
        original_files_unchanged(journal)
        set_maintenance(directory, journal)
        checkpoint(directory, journal, 'maintenance_guarded')
        systemctl('stop', SERVICE_NAMES['api'])
        conn = await connect(spec)
        try:
            await drained(conn)
        finally:
            await conn.close()
        systemctl('stop', SERVICE_NAMES['worker'], SERVICE_NAMES['feishu'])
        assert_offline()
        checkpoint(directory, journal, 'offline')
        return journal
    if action == 'start':
        if journal['phase'] not in ('configuration_switched', 'started'):
            raise AdoptionError('Configuration switch must finish before explicit start')
        conn = await connect(spec)
        try:
            if await state(conn, journal) != 'new':
                raise AdoptionError('Target database is not committed')
            if not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM control.operator WHERE enabled)'):
                raise AdoptionError('Bootstrap the approved platform operator before opening SaaS services')
        finally:
            await conn.close()
        verify_installed_unit_files(journal)
        try:
            if journal['phase'] != 'started':
                require_maintenance(directory, journal)
                clear_maintenance_marker(directory)
                systemctl('start', SERVICE_NAMES['api'], SERVICE_NAMES['worker'], SERVICE_NAMES['feishu'])
            for _ in range(15):
                try:
                    with urllib.request.urlopen('http://127.0.0.1:'+str(spec['api_port'])+'/api/v1/health/version', timeout=3) as response:
                        actual = json.load(response)
                    if actual.get('revision') == spec['new_revision']:
                        with urllib.request.urlopen('http://127.0.0.1:'+str(spec['api_port'])+'/api/v1/health/ready', timeout=3) as ready:
                            if ready.status == 200:
                                break
                except (OSError, ValueError):
                    pass
                await asyncio.sleep(1)
            else:
                raise AdoptionError('New API version check failed')
            actual_services = []
            for key, row in service_snapshot().items():
                if row['active'] != 'active' or not row['pid'] or Path(f'/proc/{row["pid"]}/cwd').resolve() != Path(spec['new_release']):
                    raise AdoptionError('Actual running service differs from new release')
                actual_services.append({'service': SERVICE_NAMES[key], 'state': row['active'], 'pid': row['pid'],
                                        'release': Path(spec['new_release']).name, 'revision': spec['new_revision'],
                                        'matches_current': True})
            record = json.loads(Path(spec['releases_file']).read_text())
            record.update(consistent=True, services=actual_services)
            atomic_json(spec['releases_file'], record)
            checkpoint(directory, journal, 'started')
        except BaseException:
            set_maintenance(directory, journal)
            systemctl('stop', *SERVICE_NAMES.values())
            checkpoint(directory, journal, 'configuration_switched')
            raise
        return journal
    assert_offline()
    require_maintenance(directory, journal)
    conn = await connect(spec)
    try:
        await no_connections(conn)
        await drained(conn)
        dbstate = await state(conn, journal)
        if action == 'backup':
            if dbstate != 'old':
                raise AdoptionError('Backup baseline must precede migration')
            original_files_unchanged(journal)
            await old_role_gate(conn, spec)
            backup = directory/('backup-'+uuid.uuid4().hex)
            backup_spec = {**spec, 'backup_paths': [*spec['backup_paths'],
                                                   str(directory/'credentials.json'), str(directory/'mfa.json')]}
            sha = await create_backup(conn, backup_spec, backup)
            result = await restore_check(spec, backup, sha)
            journal.update(backup=str(backup), backup_sha256=sha, restore_check=result)
            checkpoint(directory, journal, 'backup_verified')
        elif action in ('migrate', 'resume', 'switch'):
            if not journal.get('restore_check', {}).get('database_rows_acl_rls_verified'):
                raise AdoptionError('A real isolated backup restore must pass before migration')
            manifest = validate_backup(Path(journal['backup']), journal['backup_sha256'])
            if dbstate == 'old':
                if action == 'switch':
                    raise AdoptionError('Database must commit before switching configurations')
                original_files_unchanged(journal)
                for item in manifest['paths']:
                    if tree(item['source']) != item['entries']:
                        raise AdoptionError('Original/configuration files changed after verified backup')
                install_dependencies(spec)
                await migrate(conn, directory, journal, manifest, credentials)
            else:
                async with conn.transaction(readonly=True):
                    if fingerprint(await inventory(conn)) != journal['after_inventory']:
                        raise AdoptionError('Target database changed before configuration switch')
                checkpoint(directory, journal, 'database_committed')
            await verify_connections(spec, credentials)
            if action in ('switch', 'resume'):
                switch(directory, journal, credentials)
        else:
            raise AdoptionError('Unknown adoption phase')
        return journal
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'stop', 'backup', 'migrate', 'resume', 'switch', 'start', 'abort', 'status'))
    parser.add_argument('--directory', required=True)
    parser.add_argument('--spec', help='Private reviewed JSON, required for prepare only')
    args = parser.parse_args()
    if args.command == 'prepare' and not args.spec:
        parser.error('prepare requires --spec')
    if args.command != 'prepare' and args.spec:
        parser.error('spec is frozen at prepare and cannot be replaced during resume')
    directory = private_directory(args.directory, create=args.command == 'prepare')
    with locked(directory):
        result = asyncio.run(execute(args.command, directory, read_private(args.spec) if args.spec else None))
    # The full private journal remains on disk; stdout never serializes a spec,
    # DSN, source path list, cluster hashes, role SQL, credentials or business data.
    print(json.dumps({'phase': result['phase'], 'database_state': result.get('database_state'),
                      'old_revision': result['spec']['old_revision'], 'new_revision': result['spec']['new_revision'],
                      'production_acceptance_complete': False}))


if __name__ == '__main__':
    try:
        main()
    except AdoptionError as exc:
        raise SystemExit(str(exc)) from None
    except Exception as exc:
        raise SystemExit('Legacy adoption failed ('+type(exc).__name__+'); private evidence retained, inspect status before retry') from None
