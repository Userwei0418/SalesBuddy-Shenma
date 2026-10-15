#!/usr/bin/env python3
"""Rehearse V152 adoption with real systemd, exclusively inside a disposable container.

Host usage (stdlib Python; Docker's Linux engine must already be running)::

  python3 backend/tests/system/verify_legacy_adoption_systemd.py --output /tmp/sb-adoption-evidence

The dependency preparation container may access package registries. The actual
rehearsal has network=none, a private cgroup namespace, no ports and no host
mounts. Privileged mode is needed for container-local systemd; it is never used
to enter the Docker VM or change its services. No production data/configuration
is read. The candidate comes from an explicit committed SHA in a clean clone.
The harness SHA is recorded separately so a working harness is not confused
with the frozen candidate. Containers and the temporary prepared image are
removed even on failure; a cleanup failure prevents an overall pass. Only
sanitized JSON and build/test logs are exported.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[3] if len(Path(__file__).resolve().parents) > 3 else Path('/')
HERE = Path(__file__).resolve().parent
BASELINE = '5d55f49eca809d3cc715f636c753d38dc039c60b'
BASE_IMAGE = 'salesbuddy-local-ci:ubuntu24-python312-node22-pg16'
SYSTEMD_IMAGE = 'salesbuddy-systemd-rehearsal:pg16-systemd'
LAB = Path('/opt/sb-systemd')
OLD = LAB/'releases/legacy'
NEW = LAB/'releases/candidate'
PG_BIN = Path('/usr/lib/postgresql/16/bin')
PG_PORT = 55432
API_PORT = 18080
SOCKET = '/run/postgresql/sb-adoption'
SERVICE_NAMES = ('sales-api.service', 'sales-worker.service', 'sales-feishu-worker.service')


def service_arguments(name):
    return (['uvicorn', 'sales_backend.main:app', '--host', '127.0.0.1', '--port', str(API_PORT), '--workers', '1']
            if name == 'api' else ['sales_backend.'+('feishu_worker' if name == 'feishu' else 'worker')])


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(args, *, log=None, timeout=180, env=None, check=True, cwd=None):
    result = subprocess.run([str(x) for x in args], capture_output=True, timeout=timeout,
                            env=env, cwd=cwd)
    if log is not None:
        log.write(result.stdout + result.stderr)
        log.flush()
    if check and result.returncode:
        raise RuntimeError(f'{Path(str(args[0])).name} failed with exit {result.returncode}')
    return result


def json_file(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    path.chmod(0o600)


def progress(phase):
    print(json.dumps({'phase': phase, 'production': False}), flush=True)


def local_docker_context():
    if os.environ.get('DOCKER_HOST') or os.environ.get('DOCKER_CONTEXT'):
        raise RuntimeError('Remove Docker environment overrides; select a local socket context explicitly')
    context = run(['docker', 'context', 'show']).stdout.decode().strip()
    details = json.loads(run(['docker', 'context', 'inspect', context]).stdout)[0]
    endpoint = details['Endpoints']['docker']['Host']
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'unix' or parsed.netloc or not Path(parsed.path).is_socket():
        raise RuntimeError('Only a verified local Unix Docker socket is allowed; remote engines are forbidden')
    return context, endpoint


def host(args):
    revision = args.revision or run(['git', '-C', ROOT, 'rev-parse', 'HEAD']).stdout.decode().strip()
    output = Path(args.output).resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError('Evidence must be outside the source repository')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    context, endpoint = local_docker_context()
    def docker(arguments, **options):
        # Freeze the selected context for every call, without changing the user's default.
        return run(['docker', '--context', context, *arguments], **options)
    engine = docker(['info', '--format', '{{.OSType}}']).stdout.decode().strip()
    if engine != 'linux':
        raise RuntimeError('A running Linux Docker engine is required; no VM is changed automatically')
    token = uuid.uuid4().hex[:12]
    prepare_name, exercise_name = 'sb-adopt-deps-'+token, 'sb-adopt-test-'+token
    prepared_image = 'salesbuddy-adoption-prepared:'+token
    names = []
    image_created = False
    pending_error = None
    log_path = output/'rehearsal.log'
    with tempfile.TemporaryDirectory(prefix='sb-systemd-build-') as temporary, log_path.open('wb') as log:
        log_path.chmod(0o600)
        temp = Path(temporary)
        try:
            progress('build_isolated_systemd_image')
            # A tiny explicit build context cannot accidentally include local credentials.
            shutil.copy2(HERE/'legacy_adoption_systemd.Dockerfile', temp/'Dockerfile')
            docker(['build', '--build-arg', 'BASE_IMAGE='+args.base_image,
                 '-t', SYSTEMD_IMAGE, temp], log=log, timeout=600)
            source = temp/'source'
            run(['git', 'clone', '--shared', '--no-checkout', ROOT, source], log=log)
            run(['git', '-C', source, 'checkout', '--detach', revision], log=log)
            artifact = temp/'candidate.zip'
            built = run([sys.executable, source/'tools/installation/release_bundle.py', 'build',
                         '--repo', source, '--output', artifact, '--revision', revision,
                         '--api-url', f'http://127.0.0.1:{API_PORT}/api/v1', '--appid', 'wx0000000000000000',
                         '--profile', 'local', '--label', 'systemd-rehearsal'], log=log)
            package = json.loads(built.stdout)
            with (temp/'legacy.tar').open('wb') as archive:
                subprocess.run(['git', '-C', str(source), 'archive', BASELINE, 'backend', 'database'],
                               stdout=archive, check=True, timeout=90)
            with (temp/'checks.tar').open('wb') as archive:
                subprocess.run(['git', '-C', str(source), 'archive', revision,
                                'database/tests/verify_v152_upgrade_postgres.py', 'database/scripts/migrate.py'],
                               stdout=archive, check=True, timeout=90)
            provenance = {'candidate_revision': revision, 'legacy_revision': BASELINE,
                          'candidate_sha256': package['sha256'], 'harness_sha256': digest(__file__),
                          'dockerfile_sha256': digest(HERE/'legacy_adoption_systemd.Dockerfile'),
                          'dependency_network': 'docker-default-bridge', 'rehearsal_network': 'none',
                          'host_mounts': [], 'published_ports': [], 'systemctl_mocked': False,
                          'legacy_dependencies': 'archived-lock-plus-declared-pypinyin-0.55.0',
                          'candidate_dependencies': 'locked',
                          'docker_context': context, 'docker_endpoint': endpoint,
                          'production': False}
            json_file(temp/'input.json', provenance)
            json_file(output/'provenance.json', provenance)
            progress('prepare_dependencies_with_network')
            names.append(prepare_name)
            docker(['run', '-d', '--name', prepare_name, '--entrypoint', '/bin/sleep',
                 SYSTEMD_IMAGE, 'infinity'], log=log)
            docker(['exec', prepare_name, 'mkdir', '-p', LAB, OLD, NEW, LAB/'checks'], log=log)
            for filename in ('candidate.zip', 'legacy.tar', 'checks.tar', 'input.json'):
                docker(['cp', temp/filename, prepare_name+':'+str(LAB/filename)], log=log)
            docker(['cp', __file__, prepare_name+':'+str(LAB/'harness.py')], log=log)
            docker(['exec', prepare_name, 'tar', '-xf', LAB/'legacy.tar', '-C', OLD], log=log)
            docker(['exec', prepare_name, 'tar', '-xf', LAB/'checks.tar', '-C', LAB/'checks'], log=log)
            docker(['exec', prepare_name, 'python3', '-m', 'zipfile', '-e', LAB/'candidate.zip', NEW], log=log)
            docker(['exec', prepare_name, 'python3', LAB/'harness.py', '--warm'], log=log, timeout=900)
            docker(['commit', prepare_name, prepared_image], log=log, timeout=180)
            image_created = True
            docker(['rm', '-f', prepare_name], log=log)
            names.remove(prepare_name)
            progress('start_real_systemd_without_network')
            names.append(exercise_name)
            docker(['run', '-d', '--name', exercise_name, '--network', 'none',
                 '--privileged', '--cgroupns', 'private', '--tmpfs', '/run', '--tmpfs', '/run/lock',
                 '--tmpfs', '/tmp', '-e', 'SB_SYSTEMD_REHEARSAL=1', '--entrypoint', '/sbin/init',
                 prepared_image, '--unit=multi-user.target'], log=log)
            inspect = json.loads(docker(['inspect', exercise_name]).stdout)[0]
            assert inspect['HostConfig']['NetworkMode'] == 'none'
            assert inspect['HostConfig']['CgroupnsMode'] == 'private'
            assert not inspect['Mounts'] and not inspect['HostConfig']['PortBindings']
            for _ in range(30):
                ready = docker(['exec', exercise_name, 'systemctl', 'is-system-running'], check=False)
                if ready.stdout.strip() == b'running':
                    break
                time.sleep(1)
            else:
                raise RuntimeError('Real systemd did not reach running; no mocked fallback is allowed')
            result = docker(['exec', exercise_name, str(NEW/'.venv/bin/python'),
                          LAB/'harness.py', '--inside'], log=log, timeout=1500, check=False)
            copied = docker(['cp', exercise_name+':'+str(LAB/'result.json'), output/'result.json'],
                         log=log, check=False)
            if copied.returncode == 0:
                (output/'result.json').chmod(0o600)
            if result.returncode:
                raise RuntimeError('Systemd rehearsal failed; inspect the private rehearsal log')
            summary = json.loads((output/'result.json').read_text())
            assert summary['status'] == 'passed' and summary['candidate_revision'] == revision
            provenance.update(prepared_image_id=inspect['Image'])
        except BaseException as error:
            pending_error = error
            raise
        finally:
            cleanup = {'containers': {}, 'prepared_image': 'not-created', 'verified': False}
            try:
                for name in names:
                    removed = docker(['rm', '-f', name], log=log, check=False)
                    cleanup['containers'][name] = removed.returncode == 0
                if image_created:
                    removed = docker(['image', 'rm', prepared_image], log=log, check=False)
                    cleanup['prepared_image'] = removed.returncode == 0
                remaining = docker(['ps', '-a', '--format', '{{.Names}}']).stdout.decode().splitlines()
                images = docker(['image', 'ls', '--format', '{{.Repository}}:{{.Tag}}']).stdout.decode().splitlines()
                cleanup['verified'] = not ({prepare_name, exercise_name} & set(remaining)) and prepared_image not in images
            except Exception as error:
                cleanup['error_type'] = type(error).__name__
            json_file(output/'cleanup.json', cleanup)
            if not cleanup['verified']:
                if (output/'result.json').exists():
                    failed = json.loads((output/'result.json').read_text())
                    failed.update(status='failed', failure_stage='cleanup')
                    json_file(output/'result.json', failed)
                if pending_error is None:
                    raise RuntimeError('Cleanup could not be verified; inspect cleanup.json before rerunning')
    summary['cleanup_verified'] = True
    json_file(output/'result.json', summary)
    provenance.update(status='passed', cleanup_verified=True)
    json_file(output/'provenance.json', provenance)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def warm():
    import tomllib
    if not (LAB/'input.json').is_file() or not Path('/.dockerenv').exists():
        raise RuntimeError('Dependency preparation is allowed only in the disposable container')
    (OLD/'REVISION').write_text(BASELINE+'\n')
    env = {**os.environ, 'UV_PYTHON_DOWNLOADS': 'never', 'UV_LINK_MODE': 'copy'}
    for release in (OLD, NEW):
        env['UV_PROJECT_ENVIRONMENT'] = str(release/'.venv')
        run(['uv', '--no-config', 'sync', '--frozen' if release == OLD else '--locked', '--no-dev', '--no-editable',
             '--python', '3.12', '--project', release/'backend'], env=env, timeout=800, log=sys.stdout.buffer)
    # The production-source V152 lock omitted the already-declared pypinyin.
    # Rebuild its runtime explicitly; never change historical source/lock bytes.
    old_project = tomllib.loads((OLD/'backend/pyproject.toml').read_text())
    assert 'pypinyin==0.55.0' in old_project['project']['dependencies']
    locked = tomllib.loads((NEW/'backend/uv.lock').read_text())
    pinned = next(package for package in locked['package'] if package['name'] == 'pypinyin')
    assert pinned['version'] == '0.55.0'
    requirement = LAB/'legacy-extra-requirements.txt'
    requirement.write_text('pypinyin==0.55.0 '+ ' '.join('--hash='+wheel['hash'] for wheel in pinned['wheels'])+'\n')
    run(['uv', '--no-config', 'pip', 'install', '--python', OLD/'.venv/bin/python', '--require-hashes',
         '--no-deps', '-r', requirement], env=env, timeout=180, log=sys.stdout.buffer)
    (LAB/'ISOLATED_SYSTEMD_REHEARSAL').write_text('synthetic-only\n')


def load(name, path):
    definition = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(definition)
    sys.modules[name] = module
    definition.loader.exec_module(module)
    return module


def systemctl(*args):
    return run(['systemctl', *args]).stdout.decode().strip()


def process_environment(pid):
    # Only synthetic container process environments are read. Values never leave the container.
    return dict(item.decode().split('=', 1) for item in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0') if b'=' in item)


def health(revision):
    for _ in range(30):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{API_PORT}/api/v1/health/version', timeout=2) as response:
                value = json.load(response)
            with urllib.request.urlopen(f'http://127.0.0.1:{API_PORT}/api/v1/health/ready', timeout=2) as response:
                assert response.status == 200
            if value.get('revision') == revision:
                return value
        except (OSError, ValueError, AssertionError):
            pass
        time.sleep(1)
    raise AssertionError('Actual API did not report the expected imported revision/readiness')


async def setup_fixture(adoption, backup, fixture, cipher_class):
    import asyncpg
    from legacy_adoption_state import atomic_json

    pgdata = Path('/var/lib/postgresql/sb-adoption')
    run(['install', '-d', '-o', 'postgres', '-g', 'postgres', '-m', '700', pgdata, SOCKET])
    run(['runuser', '-u', 'postgres', '--', PG_BIN/'initdb', '-D', pgdata, '-U', 'postgres',
         '--auth-local', 'peer', '--auth-host', 'scram-sha-256', '--no-locale', '--encoding=UTF8'])
    pgunit = Path('/etc/systemd/system/sb-test-postgres.service')
    pgunit.write_text('[Unit]\nDescription=Disposable adoption PostgreSQL\n[Service]\nType=forking\n'
                      'User=postgres\nGroup=postgres\n'
                      f'ExecStart={PG_BIN}/pg_ctl -D {pgdata} -l {pgdata}/server.log '
                      f'-o "-k {SOCKET} -p {PG_PORT} -c listen_addresses=127.0.0.1" -w start\n'
                      f'ExecStop={PG_BIN}/pg_ctl -D {pgdata} -m fast -w stop\n')
    systemctl('daemon-reload')
    systemctl('start', 'sb-test-postgres.service')
    maintenance = {'database': {'name': 'adoption_fixture', 'host': SOCKET, 'port': PG_PORT,
                               'administrator': 'postgres', 'sslmode': 'disable'},
                   'maintenance_auth': {'mode': 'peer', 'os_user': 'postgres'}}
    try:
        await asyncpg.connect(host=SOCKET, port=PG_PORT, user='postgres', database='postgres')
    except asyncpg.InvalidAuthorizationSpecificationError:
        pass
    else:
        raise AssertionError('Root bypassed required PostgreSQL peer authentication')
    conn = await backup.connect(maintenance, 'postgres')
    assert os.geteuid() == 0
    await conn.execute('CREATE DATABASE adoption_fixture TEMPLATE template0')
    await conn.close()
    conn = await backup.connect(maintenance)
    config = LAB/'config'
    config.mkdir()
    state = LAB/'state'
    state.mkdir(mode=0o700)
    run(['useradd', '--system', '--user-group', '--no-create-home', 'sales-backend'])
    uid = int(run(['id', '-u', 'sales-backend']).stdout)
    gid = int(run(['id', '-g', 'sales-backend']).stdout)
    run(['install', '-d', '-o', 'sales-backend', '-g', 'sales-backend', '/var/lib/sales-backend'])
    key_file = config/'provider.json'
    atomic_json(key_file, {'keys': {'synthetic': base64.b64encode(secrets.token_bytes(32)).decode()}})
    cipher = cipher_class.from_file(str(key_file), 'synthetic')
    os.chown(key_file, uid, gid)
    old_passwords = {name: secrets.token_urlsafe(32) for name in ('api', 'worker', 'feishu')}
    try:
        await fixture.migrate.migrate(conn, OLD/'database')
        await fixture.fixture(conn)
        for row in await conn.fetch('SELECT workspace_id FROM config.agent_runtime_config'):
            encrypted = cipher.encrypt(str(row['workspace_id']), 'synthetic-key-never-used')
            await conn.execute('UPDATE config.agent_runtime_config SET api_key_ciphertext=$2 WHERE workspace_id=$1',
                               row['workspace_id'], encrypted['api_key_ciphertext'])
        for row in await conn.fetch('SELECT id,workspace_id FROM config.feishu_connection'):
            encrypted = cipher.encrypt(str(row['workspace_id']), 'synthetic-feishu-never-used')
            await conn.execute('INSERT INTO security.feishu_credential(connection_id,workspace_id,ciphertext,encryption_key_id) VALUES($1,$2,$3,$4)',
                               row['id'], row['workspace_id'], encrypted['api_key_ciphertext'], 'synthetic')
        # Historical jobs are kept, but no external delivery is eligible in this fixture.
        await conn.execute('UPDATE config.feishu_connection SET enabled=false')
        await conn.execute("UPDATE ops.feishu_event SET status='succeeded',completed_at=clock_timestamp()")
        # Exercise the real worker loop while deliberately disabling new external AI work.
        await conn.execute("UPDATE config.sales_competency_framework SET status='draft'")
        local = config/'original.txt'
        local.write_bytes(b'synthetic local original')
        local.chmod(0o600)
        os.chown(local, uid, gid)
        await conn.execute("UPDATE activity.visit_import SET storage_driver='local',file_path=$1,storage_key=$1,file_size=$2,content_sha256=$3 WHERE id=(SELECT id FROM activity.visit_import ORDER BY id LIMIT 1)",
                           str(local), local.stat().st_size, backup.digest(local))
        await conn.execute('CREATE ROLE sales_app NOLOGIN NOINHERIT')
        for schema in fixture.SCHEMAS:
            await conn.execute(f'GRANT USAGE ON SCHEMA {schema} TO sales_app')
            await conn.execute(f'GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA {schema} TO sales_app')
            await conn.execute(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA {schema} TO sales_app')
            await conn.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO sales_app')
        for name, password in old_passwords.items():
            literal = await conn.fetchval('SELECT quote_literal($1::text)', password)
            await conn.execute(f'CREATE ROLE sb_old_{name} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD {literal}')
            parent = 'salegent_feishu_worker' if name == 'feishu' else 'sales_app'
            await conn.execute(f'GRANT {parent} TO sb_old_{name}')
        await conn.fetchval('SELECT security.reconcile_runtime_grants()')
    finally:
        await conn.close()
    current = LAB/'current'
    current.symlink_to(OLD)
    release_file = config/'RELEASES.json'
    atomic_json(release_file, {'current': 'legacy', 'previous': None, 'revision': BASELINE,
                               'operator_note': 'synthetic preserved unknown metadata'})
    shared = config/'shared.env'
    values = {'APP_ENV': 'production', 'AUTH_MODE': 'password',
              'ACCESS_TOKEN_SECRET': secrets.token_urlsafe(48), 'SENSEAUDIO_API_KEY': 'synthetic-never-used',
              'SENSEAUDIO_BASE_URL': 'http://127.0.0.1:9', 'CONFIG_CREDENTIAL_KEY_ID': 'synthetic',
              'CONFIG_CREDENTIAL_KEYRING_FILE': str(key_file), 'PYTHONPATH': str(current/'backend/src'),
              'PYTHONDONTWRITEBYTECODE': '1', 'SYNTHETIC_PRESERVED_SETTING': 'retained-original',
              'AGENT_PLATFORM_BINDINGS_JSON': '{"synthetic":"preserved"}'}
    shared.write_text(''.join(k+'='+v+'\n' for k, v in values.items()))
    shared.chmod(0o600)
    units = []
    for name, service in adoption.SERVICE_NAMES.items():
        envfile = config/(name+'.env')
        dsn = f'postgresql://sb_old_{name}:{old_passwords[name]}@127.0.0.1:{PG_PORT}/adoption_fixture?sslmode=disable'
        envfile.write_text(('FEISHU_SYNC_DATABASE_URL' if name == 'feishu' else 'DATABASE_URL')+'='+dsn+'\n')
        envfile.chmod(0o600)
        path = Path('/etc/systemd/system')/service
        module = ' '.join(service_arguments(name))
        path.write_text('[Unit]\nDescription=Actual legacy synthetic '+name+'\nAfter=sb-test-postgres.service\n'
                        '[Service]\nUser=sales-backend\nGroup=sales-backend\nType=simple\n'
                        f'WorkingDirectory={current}\nEnvironmentFile={shared}\nEnvironmentFile={envfile}\n'
                        f'ExecStart={OLD}/.venv/bin/python -m {module}\nRestart=on-failure\nRestartSec=1\nTimeoutStopSec=30\n'
                        'NoNewPrivileges=true\nPrivateTmp=true\nProtectSystem=strict\nProtectHome=true\n'
                        'ReadWritePaths=/var/lib/sales-backend\n')
        units.append(path)
        unit_dir = Path('/etc/systemd/system')/(service+'.d')
        unit_dir.mkdir()
        # These names mirror the old server's late-sorting overrides. A 90-*.conf
        # override is insufficient: real systemd must resolve the final candidate.
        runtime = unit_dir/'release-runtime.conf'
        runtime.write_text(f'[Service]\nWorkingDirectory={OLD}\nEnvironment=PYTHONPATH={OLD}/backend/src\n')
        module_override = unit_dir/'zz-python-module.conf'
        module_override.write_text(f'[Service]\nExecStart=\nExecStart={OLD}/.venv/bin/python -m {module}\n')
        late_environment = config/('legacy-late-'+name+'.env')
        late_environment.write_bytes(envfile.read_bytes())
        late_environment.chmod(0o600)
        credential_override = unit_dir/'zzzz-runtime-credentials.conf'
        credential_override.write_text(f'[Service]\nEnvironmentFile={late_environment}\n')
        units.extend((runtime, module_override, credential_override))
    provenance = json.loads((LAB/'input.json').read_text())
    specification = {'format': 'salesbuddy-legacy-adoption-v1',
        'database': {'name': 'adoption_fixture', 'host': SOCKET, 'port': PG_PORT,
                     'administrator': 'postgres', 'sslmode': 'disable'},
        'maintenance_auth': {'mode': 'peer', 'os_user': 'postgres'},
        'runtime_host': '127.0.0.1', 'pg_bin': str(PG_BIN), 'old_release': str(OLD),
        'new_release': str(NEW), 'old_revision': BASELINE, 'new_revision': provenance['candidate_revision'],
        'archive': str(LAB/'candidate.zip'), 'archive_sha256': provenance['candidate_sha256'],
        'current_link': str(current), 'releases_file': str(release_file),
        'roles': {name: 'sb_new_'+name for name in adoption.SERVICES},
        'retire_roles': ['sb_old_'+name for name in old_passwords],
        'backup_paths': [str(OLD), str(config), *map(str, units)],
        'provider_keyring': {'path': str(key_file), 'key_id': 'synthetic'},
        'mfa_file': str(config/'mfa.json'), 'api_port': API_PORT,
        'uv_binary': '/usr/local/bin/uv', 'uv_sha256': digest('/usr/local/bin/uv'),
        'environment_files': {name: str(config/('adopted-'+name+'.env')) for name in adoption.SERVICE_NAMES},
        'dropins': {name: '/etc/systemd/system/'+service+'.d/zzzzzz-saas-adoption.conf'
                    for name, service in adoption.SERVICE_NAMES.items()}}
    atomic_json(state/'spec.json', specification)
    systemctl('daemon-reload')
    systemctl('start', *SERVICE_NAMES)
    return specification, state/'adoption', values


def require_isolated_container():
    if (os.geteuid() != 0 or os.environ.get('SB_SYSTEMD_REHEARSAL') != '1'
            or not Path('/.dockerenv').is_file() or not (LAB/'ISOLATED_SYSTEMD_REHEARSAL').is_file()
            or Path('/proc/1/comm').read_text().strip() != 'systemd'):
        raise RuntimeError('Refusing to run outside the isolated real-systemd test container')
    for path in (NEW/'backend/src', NEW/'backend/deploy', NEW/'tools/installation'):
        sys.path.insert(0, str(path))


def crash_switch():
    require_isolated_container()
    import adopt_legacy_saas as adoption
    from legacy_adoption_state import read_private
    directory = LAB/'state/adoption'
    def crash(phase):
        if phase == 'current_switched':
            os._exit(91)
    adoption.switch(directory, read_private(directory/'ADOPTION.json'),
                    read_private(directory/'credentials.json'), fault=crash)
    raise RuntimeError('Switch did not reach the durable interruption checkpoint')


async def exercise():
    require_isolated_container()
    import asyncpg
    import adopt_legacy_saas as adoption
    import legacy_adoption_backup as backup
    import legacy_adoption_database as database
    from legacy_adoption_state import read_private
    from sales_backend.security.runtime_credentials import CredentialCipher

    fixture = load('systemd_historical_fixture', LAB/'checks/database/tests/verify_v152_upgrade_postgres.py')
    progress('create_synthetic_v152_and_real_old_services')
    specification, directory, preserved = await setup_fixture(adoption, backup, fixture, CredentialCipher)
    old_health = health(BASELINE)
    await asyncio.sleep(2)
    old_services = adoption.service_snapshot()
    hba_sha = digest('/var/lib/postgresql/sb-adoption/pg_hba.conf')
    runtime_uid = run(['id', '-u', 'sales-backend']).stdout.decode().strip()
    for row in old_services.values():
        assert row['active'] == 'active' and row['pid']
        assert Path(f'/proc/{row["pid"]}/cwd').resolve() == OLD
        assert re.search(r'^Uid:\s+(\d+)', Path(f'/proc/{row["pid"]}/status').read_text(), re.M).group(1) == runtime_uid
    assert all(systemctl('show', unit, '--property=NRestarts', '--value') == '0' for unit in SERVICE_NAMES)
    # Freeze hashes after all synthetic configuration is created, before prepare.
    originals = {str(path): digest(path) for path in (LAB/'config').iterdir() if path.is_file()}
    async def step(action):
        progress(action)
        return await adoption.execute(action, directory, specification if action == 'prepare' else None)

    directory.mkdir(mode=0o700)
    journal = await step('prepare')
    await step('stop')
    adoption.assert_offline()
    systemctl('start', *SERVICE_NAMES)
    adoption.assert_offline()
    systemctl('restart', *SERVICE_NAMES)
    adoption.assert_offline()
    assert all(systemctl('show', unit, '--property=ConditionResult', '--value') == 'no' for unit in SERVICE_NAMES)
    journal = await step('backup')
    assert journal['restore_check']['database_rows_acl_rls_verified']
    assert journal['restore_check']['encrypted_credentials'] == 4
    assert journal['restore_check']['originals'] == 2
    await step('migrate')
    credentials = read_private(directory/'credentials.json')
    credentials_hash = digest(directory/'credentials.json')
    assert (await step('status'))['database_state'] == 'new'
    assert (LAB/'current').resolve() == OLD
    # This is an actual process exit after durable COMMIT and symlink replacement,
    # before daemon-reload/RELEASES rewrite. systemctl itself is never replaced.
    progress('crash_after_committed_database_and_current_switch')
    crashed = run([sys.executable, __file__, '--crash-switch'], check=False, log=sys.stdout.buffer)
    assert crashed.returncode == 91
    assert read_private(directory/'ADOPTION.json')['phase'] == 'current_switched'
    assert (LAB/'current').resolve() == NEW
    systemctl('restart', *SERVICE_NAMES)
    adoption.assert_offline()
    assert (await step('status'))['database_state'] == 'new'
    await step('resume')
    assert digest(directory/'credentials.json') == credentials_hash
    assert read_private(directory/'ADOPTION.json')['phase'] == 'configuration_switched'
    adoption.assert_offline()
    try:
        await step('start')
    except adoption.AdoptionError:
        pass
    else:
        raise AssertionError('Services started before platform operator bootstrap')
    progress('bootstrap_operator_then_explicit_start')
    bootstrap_code = ('import asyncio,sys,secrets; sys.path.insert(0,'+repr(str(NEW/'backend/scripts'))+'); '
                      'from bootstrap_platform_operator import create_operator; '
                      'asyncio.run(create_operator('+repr(f'postgresql://postgres@/adoption_fixture?host={SOCKET}&port={PG_PORT}')+
                      ',"SYNTHETIC_PLATFORM","Synthetic rehearsal operator",secrets.token_urlsafe(24)))')
    run(['runuser', '-u', 'postgres', '--', 'env', 'PYTHONPATH='+str(NEW/'backend/src'),
         'PYTHONDONTWRITEBYTECODE=1', NEW/'.venv/bin/python', '-c', bootstrap_code])
    await step('start')
    new_health = health(specification['new_revision'])
    new_services = adoption.service_snapshot()
    for name, row in new_services.items():
        assert row['active'] == 'active' and row['pid'] != old_services[name]['pid']
        assert Path(f'/proc/{row["pid"]}/cwd').resolve() == NEW
        assert re.search(r'^Uid:\s+(\d+)', Path(f'/proc/{row["pid"]}/status').read_text(), re.M).group(1) == runtime_uid
        args = [item.decode() for item in Path(f'/proc/{row["pid"]}/cmdline').read_bytes().split(b'\0') if item]
        expected_args = [str(NEW/'.venv/bin/python'), '-m', *service_arguments(name)]
        assert args == expected_args
        assert int(systemctl('show', adoption.SERVICE_NAMES[name], '--property=NRestarts', '--value')) == 0
        assert systemctl('show', adoption.SERVICE_NAMES[name], '--property=WorkingDirectory', '--value') == specification['current_link']
        command = systemctl('show', adoption.SERVICE_NAMES[name], '--property=ExecStart', '--value')
        assert re.search(r'path=([^ ;]+)', command).group(1) == str(NEW/'.venv/bin/python')
        assert re.search(r'argv\[\]=(.*?) ;', command).group(1).split() == expected_args
        assert row['environment_files'][-1] == specification['environment_files'][name]
        actual = process_environment(row['pid'])
        for key, value in preserved.items():
            if key != 'PYTHONPATH':
                assert actual.get(key) == value, 'Original non-database configuration was lost: '+key
        assert actual['PYTHONPATH'] == str(NEW/'backend/src')
        key = 'FEISHU_SYNC_DATABASE_URL' if name == 'feishu' else 'DATABASE_URL'
        assert urlsplit(actual[key]).username == specification['roles'][name]
        if name == 'api':
            assert urlsplit(actual['PLATFORM_DATABASE_URL']).username == specification['roles']['platform']
            assert actual['PUBLIC_WEB_ORIGIN'] == f'http://127.0.0.1:{API_PORT}'
        else:
            assert not actual.get('PLATFORM_DATABASE_URL')
    # Wait long enough to catch start-then-crash, wrong old DSN and role-check failures.
    await asyncio.sleep(3)
    assert all(row['active'] == 'active' for row in adoption.service_snapshot().values())
    await database.verify_connections(specification, credentials)
    try:
        await database.verify_connections(specification, {**credentials, 'api': 'wrong-synthetic-password'})
    except asyncpg.InvalidPasswordError:
        pass
    else:
        raise AssertionError('Connections did not authenticate their persisted passwords')
    conn = await backup.connect(specification)
    try:
        assert await conn.fetchval("SELECT max(version) FROM ops.schema_migration") == fixture.TARGET
        assert await conn.fetchval('SELECT count(*) FROM ops.migration_checksum') == fixture.TARGET_CHECKSUM_COUNT
        assert await conn.fetchval('SELECT count(*) FROM platform.workspace') == 2
        assert await conn.fetchval('SELECT count(*) FROM activity.visit_import') == 2
        assert not await conn.fetchval('SELECT bool_or(rolcanlogin) FROM pg_roles WHERE rolname=ANY($1::text[])', specification['retire_roles'])
        connected = {row['usename'] for row in await conn.fetch("SELECT DISTINCT usename FROM pg_stat_activity WHERE datname=current_database()")}
        assert set(specification['roles'].values()) <= connected
    finally:
        await conn.close()
    for filename, sha in originals.items():
        if filename != specification['releases_file']:
            assert digest(filename) == sha
    assert digest('/var/lib/postgresql/sb-adoption/pg_hba.conf') == hba_sha
    record = read_private(Path(specification['releases_file']))
    assert record['current'] == 'candidate' and record['previous'] == 'legacy' and record['consistent']
    assert record['operator_note'] == 'synthetic preserved unknown metadata'
    await step('start')  # Re-entry must verify, not restart or generate new credentials.
    assert {key: row['pid'] for key, row in adoption.service_snapshot().items()} == {key: row['pid'] for key, row in new_services.items()}
    result = {**json.loads((LAB/'input.json').read_text()), 'status': 'passed',
              'linux_architecture': os.uname().machine,
              'systemd_version': systemctl('show', '--property=Version', '--value'),
              'old_http': old_health, 'new_http': new_health, 'from': 'V152', 'to': fixture.TARGET,
              'target_checksums': fixture.TARGET_CHECKSUM_COUNT,
              'real_old_and_new_services': 3, 'four_authenticated_identities': True,
              'wrong_password_rejected': True, 'four_actual_database_sessions': True,
              'committed_switch_crash_resumed': True, 'credentials_preserved_on_resume': True,
              'bootstrap_required_before_start': True, 'start_reentry_preserves_pids': True,
              'peer_maintenance_without_hba_changes': True, 'guard_blocks_accidental_start': True,
              'late_legacy_dropins_overridden': True, 'effective_systemd_properties_verified': True,
              'original_environment_and_provider_keyring_preserved': True,
              'legacy_logins_retired': True, 'synthetic_tenants_preserved': 2,
              'backup_restore': journal['restore_check'], 'real_external_integrations_tested': False}
    json_file(LAB/'result.json', result)
    progress('passed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', help='New private host evidence directory, outside the repository')
    parser.add_argument('--revision', help='Candidate full committed SHA; default HEAD')
    parser.add_argument('--base-image', default=BASE_IMAGE)
    parser.add_argument('--warm', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--crash-switch', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.warm:
        warm()
    elif args.crash_switch:
        crash_switch()
    elif args.inside:
        try:
            asyncio.run(exercise())
        except Exception as error:
            # All inputs are synthetic; traceback in the private host log aids repairs.
            json_file(LAB/'result.json', {'status': 'failed', 'error_type': type(error).__name__,
                                        'production': False, 'systemctl_mocked': False})
            for service in SERVICE_NAMES:
                run(['journalctl', '--no-pager', '-n', '60', '-u', service],
                    log=sys.stderr.buffer, check=False)
            raise
    else:
        if not args.output:
            parser.error('--output is required')
        host(args)


if __name__ == '__main__':
    main()
