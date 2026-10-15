"""Prepare, initialize and run a verified instance. Never adopt an existing database.

Run with Python 3.12 on macOS/Linux. PostgreSQL 16 and uv are explicit prerequisites.
Maintenance credentials are supplied only via SALESBUDDY_INSTALL_DB_PASSWORD.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler
import uuid

if __name__ == '__main__': sys.dont_write_bytecode = True

from instance_files import InstanceError, atomic_json, clean_env, instance, locked, root_path, source_check, runtime_path, no_pending_upgrade
from instance_process import service_lock, state, stop
from release_bundle import MANIFEST, json_bytes, read_verified
from runtime_roles import identifier

REQUIRED_TOOLS = {f'tools/installation/{name}.py' for name in (
    'instance', 'instance_files', 'instance_process', 'instance_database', 'instance_service', 'runtime_roles')}


def prepare(args):
    root = root_path(args.root, existing=False)
    files, manifest = read_verified(args.archive, args.sha256)
    if not REQUIRED_TOOLS <= files.keys():
        raise InstanceError('Artifact predates the complete instance installer')
    source = Path(__file__).resolve().parents[2]
    if root == source or source in root.parents:
        raise InstanceError('Instance data must be outside the source checkout')
    for value in (args.database, args.db_user):
        identifier(value)
    if not 1 <= args.db_port <= 65535 or not 1 <= args.api_port <= 65535:
        raise InstanceError('Invalid database or API port')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{2,63}', args.operator):
        raise InstanceError('Platform account must contain 3–64 letters, digits, dot, dash or underscore')
    if not args.operator_name.strip() or len(args.operator_name) > 100:
        raise InstanceError('Provide a platform operator display name')
    if not re.fullmatch(r'[a-zA-Z0-9.:-]{1,253}', args.db_host):
        raise InstanceError('Invalid explicit database host')
    if args.db_host not in {'localhost', '127.0.0.1', '::1'} and args.db_sslmode != 'verify-full':
        raise InstanceError('Remote PostgreSQL requires verify-full TLS')
    target = urlsplit(manifest['api_base_url'])
    if manifest['profile'] == 'local' and (target.hostname not in {'127.0.0.1', 'localhost'} or (target.port or 80) != args.api_port):
        raise InstanceError('Local artifact must match the IPv4 loopback API port')
    proxy = bool(getattr(args, 'trust_loopback_proxy', False))
    if (manifest['profile'] == 'private') != proxy:
        raise InstanceError('Private HTTPS instances require explicit --trust-loopback-proxy; local instances do not use it')
    # Exclusive root creation. On failure retain the owned partial directory for
    # diagnosis; never overwrite another install or remove unknown user files.
    root.mkdir(mode=0o700)
    for name in ('private', 'release', 'cache', 'run', 'logs', 'materials'):
        (root/name).mkdir(mode=0o700)
    with locked(root):
        shutil.copyfile(args.archive, root/'artifact.zip')
        os.chmod(root/'artifact.zip', 0o600)
        files, verified = read_verified(root/'artifact.zip', args.sha256)
        if verified != manifest:
            raise InstanceError('Artifact changed while copying')
        for name, payload in {**files, MANIFEST: json_bytes(manifest)}.items():
            path = root/'release'/name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(payload)
        identity = uuid.uuid4().hex
        recovery = bool(getattr(args,'recovery_pending',False))
        if recovery:
            atomic_json(root/'release/RECOVERY_PENDING',{'instance_id':identity},replace=False)
        passwords = {service: secrets.token_urlsafe(48) for service in ('api', 'worker', 'platform', 'feishu')}
        token = secrets.token_urlsafe(48)
        configs = {'bootstrap.json': {'operator_password': secrets.token_urlsafe(32), 'database_passwords': passwords}}
        for service in ('api', 'worker', 'feishu'):
            kinds = (service, 'platform') if service == 'api' else (service,)
            configs[service+'.json'] = {'database_passwords': {k: passwords[k] for k in kinds}, 'token_secret': token}
        for name in ('provider-keys.json', 'platform-mfa-keys.json'):
            configs[name] = {'keys': {'instance-v1': base64.b64encode(secrets.token_bytes(32)).decode()}}
        for name, value in configs.items():
            atomic_json(root/'private'/name, value, replace=False)
        data = {'format': 'salesbuddy-instance-v1', 'root': str(root), 'instance_id': identity,
            'artifact_sha256': args.sha256, 'revision': manifest['revision'], 'expected_schema': manifest['expected_schema'],
            'api_base_url': manifest['api_base_url'], 'api_port': args.api_port, 'profile': manifest['profile'],
            'trust_loopback_proxy': proxy,
            'database': {'host': args.db_host, 'port': args.db_port, 'name': args.database,
                         'administrator': args.db_user, 'sslmode': args.db_sslmode},
            'operator': {'account': args.operator.upper(), 'name': args.operator_name.strip()},
            'roles': {s: 'sb_'+identity[:16]+'_'+s for s in passwords},
            'private_hashes': {name: hashlib.sha256(json_bytes(value)).hexdigest() for name, value in configs.items()}}
        atomic_json(root/'INSTANCE.json', data, replace=False)
        source_check(root,recovery=recovery)
    return {'prepared': True, 'root': str(root), 'revision': data['revision'], 'database_created': False,
            'initial_credentials_file': str(root/'private/bootstrap.json')}


def runtime_env(root):
    return {**clean_env(), 'PYTHONPYCACHEPREFIX': str(root/'cache/pycache'), 'PYTHONUNBUFFERED': '1',
            'UV_PROJECT_ENVIRONMENT': str(runtime_path(root)), 'UV_LINK_MODE': 'copy'}


def run_private(command, root, logname, *, env):
    descriptor = os.open(root/'logs'/logname, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'ab') as stream:
        result = subprocess.run(command, cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=600)
    if result.returncode:
        raise InstanceError(f'Operation failed; private diagnostic log: {root/"logs"/logname}')


def database_command(root, *, check_only):
    python = runtime_path(root)/'bin/python'
    if not python.is_file():
        raise InstanceError('Instance Python runtime is missing; run setup first')
    env = runtime_env(root)
    if 'SALESBUDDY_INSTALL_DB_PASSWORD' in os.environ:
        env['SALESBUDDY_INSTALL_DB_PASSWORD'] = os.environ['SALESBUDDY_INSTALL_DB_PASSWORD']
    command = [str(python), str(root/'release/tools/installation/instance_database.py'), '--root', str(root)]
    if check_only:
        command.append('--check-only')
    run_private(command, root, 'database.log', env=env)


def setup(root, *, check_only=False):
    with locked(root):
        no_pending_upgrade(root)
        data, _ = source_check(root)
        if not check_only:
            for service in ('api', 'worker', 'feishu'):
                descriptor = service_lock(root, service)
                os.close(descriptor)
        env = runtime_env(root)
        if not check_only:
            uv = shutil.which('uv', path=env.get('PATH'))
            if not uv:
                raise InstanceError('Install uv before initializing this instance')
            run_private([uv, '--no-config', 'sync', '--locked', '--no-dev', '--no-editable', '--python', '3.12',
                         '--project', str(root/'release/backend')], root, 'dependencies.log', env=env)
            source_check(root)
        database_command(root, check_only=check_only)
        if not check_only:
            atomic_json(root/'run/initialized.json', {'instance_id': data['instance_id'], 'revision': data['revision']})
        return {'initialized': True, 'database_verified': True, 'runtime_permissions': 'verified',
                'revision': data['revision'], 'schema': data['expected_schema'], 'company_data_seeded': False,
                'platform_account': data['operator']['account'], 'mfa_enrollment_required': True}


def request_json(url):
    opener = build_opener(ProxyHandler({}))
    try:
        response = opener.open(url, timeout=5)
    except HTTPError as exc:
        response = exc
    with response:
        return response.status, json.load(response)


def check_api_port(port):
    with socket.socket() as probe:
        # Match the server's reuse policy so closed keep-alive connections in
        # TIME_WAIT do not falsely block a restart. Listen still rejects an
        # existing listener; SO_REUSEPORT is deliberately not enabled.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(('127.0.0.1', port))
            probe.listen(1)
        except OSError:
            raise InstanceError('API port is occupied; refusing another process') from None


def check(root, *, feishu=False):
    data, _ = source_check(root)
    services = ('api', 'worker', 'feishu') if feishu else ('api', 'worker')
    if any(not state(root, service, data['instance_id']) for service in services):
        raise InstanceError('A required instance process is not running')
    origin = f'http://127.0.0.1:{data["api_port"]}'
    code, version = request_json(origin+'/api/v1/health/version')
    if code != 200 or version.get('revision') != data['revision'] or version.get('expected_schema') != data['expected_schema']:
        raise InstanceError('Running API version does not match this instance')
    code, ready = request_json(origin+'/api/v1/health/ready')
    checks = ready.get('checks', {})
    if code not in (200, 503) or not checks.get('database') or not checks.get('auth_configured'):
        raise InstanceError('Instance database or authentication is not ready')
    return {'status': 'matched', 'revision': data['revision'], 'schema': data['expected_schema'],
            'services': list(services), 'api': origin, 'readiness': ready,
            'database_ledger_checked': False,
            'frontend': str(root/'release/frontend'), 'production_deployed': False}


def start(root, *, feishu=False):
    with locked(root):
        no_pending_upgrade(root)
        data, _ = source_check(root)
        from instance_files import read_private
        initialized = read_private(root/'run/initialized.json')
        if initialized != {'instance_id': data['instance_id'], 'revision': data['revision']}:
            raise InstanceError('Instance database setup is incomplete or for another version')
        # A health response alone cannot prove the actual schema or grants.
        # Maintenance credentials exist only in this short-lived preflight, and
        # are stripped from the environment inherited by every runtime service.
        database_command(root, check_only=True)
        services = ('api', 'worker', 'feishu') if feishu else ('api', 'worker')
        python = runtime_path(root, data)/'bin/python'
        if not python.is_file():
            raise InstanceError('Instance runtime is missing')
        for service in services:
            if state(root, service, data['instance_id']):
                continue
            descriptor = service_lock(root, service)
            os.close(descriptor)
            if service == 'api':
                check_api_port(data['api_port'])
            descriptor = os.open(root/'logs'/f'{service}.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'ab') as log:
                child = subprocess.Popen([str(python), str(root/'release/tools/installation/instance_service.py'),
                    '--root', str(root), '--service', service, '--instance-id', data['instance_id']],
                    env=runtime_env(root), cwd=root, stdout=log, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, start_new_session=True)
            deadline = time.monotonic()+15
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise InstanceError(f'{service} exited during startup; inspect its private log')
                record = state(root, service, data['instance_id'])
                if record and record['pid'] == child.pid:
                    break
                time.sleep(.1)
            else:
                raise InstanceError('Service startup is unresolved; retained state, do not adopt another process')
        deadline = time.monotonic()+30
        while time.monotonic() < deadline:
            try:
                return {**check(root, feishu=feishu), 'database_ledger_checked': True}
            except (InstanceError, URLError, OSError):
                time.sleep(.3)
        raise InstanceError('Instance did not become ready; retained processes and logs for diagnosis')


def stop_all(root):
    with locked(root):
        data = instance(root)
        for service in ('feishu', 'worker', 'api'):
            stop(root, service, data['instance_id'])
    return {'stopped': True, 'database_and_files_retained': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--database', required=True)
    p.add_argument('--db-host', required=True)
    p.add_argument('--db-port', type=int, required=True)
    p.add_argument('--db-user', required=True)
    p.add_argument('--db-sslmode', choices=('disable', 'verify-full'), required=True)
    p.add_argument('--api-port', type=int, required=True)
    p.add_argument('--operator', required=True)
    p.add_argument('--operator-name', required=True)
    p.add_argument('--trust-loopback-proxy', action='store_true')
    for command in ('setup', 'database-check', 'start', 'stop', 'check'):
        p = commands.add_parser(command)
        p.add_argument('--root', type=Path, required=True)
        if command in ('start', 'check'):
            p.add_argument('--feishu', action='store_true')
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args)
    else:
        root = root_path(args.root)
        if args.command in ('setup', 'database-check'):
            result = setup(root, check_only=args.command == 'database-check')
        elif args.command == 'stop': result = stop_all(root)
        elif args.command == 'start': result = start(root, feishu=args.feishu)
        else: result = check(root, feishu=args.feishu)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (InstanceError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    except (OSError, subprocess.SubprocessError, KeyError):
        raise SystemExit('Instance operation failed; existing data and private logs were retained') from None
