"""Durability, path safety and interrupted configuration adoption boundaries."""
import copy
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'backend/deploy'))
import adopt_legacy_saas as adoption
import legacy_adoption_backup as backup
from legacy_adoption_state import AdoptionError, atomic_json, digest, locked, read_private


def specification(tmp_path):
    tmp_path = tmp_path.resolve()
    paths = {}
    for label in ('old', 'new', 'private', 'units'):
        paths[label] = tmp_path/label
        paths[label].mkdir(mode=0o700)
    spec = {'format': 'salesbuddy-legacy-adoption-v1',
            'database': {'name': 'synthetic', 'host': '127.0.0.1', 'port': 5432, 'administrator': 'postgres', 'sslmode': 'disable'},
            'runtime_host': '127.0.0.1', 'pg_bin': '/synthetic/bin',
            'old_release': str(paths['old']), 'new_release': str(paths['new']),
            'old_revision': '1'*40, 'new_revision': '2'*40, 'archive': str(tmp_path/'candidate.zip'), 'archive_sha256': 'a'*64,
            'current_link': str(tmp_path/'current'), 'releases_file': str(tmp_path/'RELEASES.json'),
            'roles': {s: 'synthetic_'+s for s in adoption.SERVICES}, 'retire_roles': ['synthetic_legacy'],
            'backup_paths': [str(paths['old']), str(paths['private'])],
            'provider_keyring': {'path': str(paths['private']/'provider.json'), 'key_id': 'synthetic'},
            'mfa_file': str(paths['private']/'mfa.json'), 'environment_files': {}, 'dropins': {}, 'api_port': 18080, 'uv_binary': str(tmp_path/'uv'), 'uv_sha256': 'f'*64, 'maintenance_auth': {'mode': 'password'}}
    for key, unit in adoption.SERVICE_NAMES.items():
        target = paths['units']/(unit+'.d'); target.mkdir()
        spec['dropins'][key] = str(target/adoption.ADOPTION_DROPIN)
        spec['environment_files'][key] = str(paths['private']/(key+'.env'))
    (tmp_path/'current').symlink_to(paths['old'])
    (tmp_path/'RELEASES.json').write_text(json.dumps({'current': {}, 'previous': {}, 'extra': 'keep'}))
    return spec


def test_spec_rejects_confused_identity_and_path_injection(tmp_path):
    spec = specification(tmp_path)
    adoption.validate_spec(spec)
    for mutate in (lambda s: s['roles'].update(worker=s['roles']['api']),
                   lambda s: s.update(retire_roles=['postgres']),
                   lambda s: s.update(mfa_file=str(tmp_path/'../escape')),
                   lambda s: s.update(api_port='8080'),
                   lambda s: s.update(new_revision='2'*7),
                   lambda s: s['environment_files'].update(api='/etc/a\nExecStart=/malicious'),
                   lambda s: s['environment_files'].update(api=s['mfa_file'])):
        invalid = copy.deepcopy(spec); mutate(invalid)
        with pytest.raises(AdoptionError):
            adoption.validate_spec(invalid)


def test_private_atomic_state_exclusive_create_and_lock(tmp_path):
    tmp_path.chmod(0o700)
    state = tmp_path/'state.json'
    atomic_json(state, {'value': 1}, replace=False)
    assert read_private(state) == {'value': 1}
    assert state.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        atomic_json(state, {'value': 2}, replace=False)
    assert read_private(state) == {'value': 1}
    with locked(tmp_path):
        with pytest.raises(AdoptionError, match='running'):
            with locked(tmp_path):
                pass
    state.chmod(0o644)
    with pytest.raises(AdoptionError):
        read_private(state)


def test_secret_integrity_no_regeneration(tmp_path):
    atomic_json(tmp_path/'credentials.json', {'api': 'synthetic'})
    atomic_json(tmp_path/'mfa.json', {'synthetic': True})
    journal = {'credentials_sha256': digest(tmp_path/'credentials.json'), 'mfa_sha256': digest(tmp_path/'mfa.json')}
    assert adoption.validate_secrets(tmp_path, journal) == {'api': 'synthetic'}
    (tmp_path/'credentials.json').write_text('{}')
    with pytest.raises(AdoptionError, match='no reset'):
        adoption.validate_secrets(tmp_path, journal)
    assert (tmp_path/'credentials.json').read_text() == '{}'


@pytest.mark.parametrize('phase', ['configuration_api', 'configuration_worker', 'configuration_feishu', 'current_switched'])
def test_half_switched_configuration_can_finish_forward(tmp_path, monkeypatch, phase):
    spec = specification(tmp_path)
    atomic_json(tmp_path/'mfa.json', {'keys': {'synthetic': 'not-a-real-key'}})
    credentials = {s: 'synthetic-'+s for s in adoption.SERVICES}
    for name, content in adoption.maintenance_files(tmp_path, spec).items():
        Path(name).write_bytes(content)
    journal = {'spec': spec, 'services': {s: {'configuration_files': {}} for s in adoption.SERVICE_NAMES}}
    monkeypatch.setattr(adoption, 'assert_offline', lambda: None)
    monkeypatch.setattr(adoption, 'require_maintenance', lambda *_: None)
    monkeypatch.setattr(adoption, 'candidate_web_origin', lambda *_: 'https://synthetic.example')
    monkeypatch.setattr(adoption, 'systemctl', lambda *args: '')
    monkeypatch.setattr(adoption, 'verify_installed_unit_files', lambda *args: None)
    monkeypatch.setattr(adoption, 'service_identity', lambda *args, **kwargs: (os.getuid(), os.getgid()))
    def fault(at):
        if at == phase:
            raise RuntimeError('synthetic crash')
    with pytest.raises(RuntimeError):
        adoption.switch(tmp_path, journal, credentials, fault=fault)
    adoption.switch(tmp_path, journal, credentials)
    assert Path(spec['current_link']).resolve() == Path(spec['new_release'])
    assert json.loads(Path(spec['releases_file']).read_text())['extra'] == 'keep'
    assert journal['phase'] == 'configuration_switched'
    assert 'PLATFORM_DATABASE_URL=\n' in Path(spec['environment_files']['worker']).read_text()
    assert 'PLATFORM_DATABASE_URL=postgresql://' in Path(spec['environment_files']['api']).read_text()
    assert 'PUBLIC_WEB_ORIGIN=https://synthetic.example\n' in Path(spec['environment_files']['api']).read_text()
    assert 'FEISHU_SYNC_DATABASE_URL=postgresql://' in Path(spec['environment_files']['feishu']).read_text()
    assert 'FEISHU_SYNC_DATABASE_URL=\n' in Path(spec['environment_files']['api']).read_text()
    assert 'PYTHONPATH='+spec['new_release']+'/backend/src\n' in Path(spec['environment_files']['api']).read_text()
    assert (Path(spec['old_release'])).exists()


def test_switch_does_not_overwrite_unrelated_configuration(tmp_path, monkeypatch):
    spec = specification(tmp_path)
    atomic_json(tmp_path/'mfa.json', {})
    target = Path(spec['environment_files']['api']); target.write_text('other configuration'); target.chmod(0o600)
    monkeypatch.setattr(adoption, 'assert_offline', lambda: None)
    monkeypatch.setattr(adoption, 'require_maintenance', lambda *_: None)
    monkeypatch.setattr(adoption, 'candidate_web_origin', lambda *_: 'https://synthetic.example')
    with pytest.raises(AdoptionError, match='different content'):
        adoption.switch(tmp_path, {'spec': spec, 'services': {}}, {s: 'synthetic-'+s for s in adoption.SERVICES})
    assert target.read_text() == 'other configuration'
    assert Path(spec['current_link']).resolve() == Path(spec['old_release'])


def test_backup_preserves_links_without_following_them(tmp_path):
    root = tmp_path/'source'; root.mkdir()
    (root/'original').write_bytes(b'original')
    outside = tmp_path/'outside'; outside.write_bytes(b'never copied via link')
    (root/'link').symlink_to(outside)
    expected = backup.tree(root)
    destination = tmp_path/'backup'
    backup.copy_tree(root, destination)
    assert backup.compare_copy(expected, backup.tree(destination))
    assert (destination/'link').is_symlink()
    (destination/'original').write_bytes(b'corrupt')
    assert not backup.compare_copy(expected, backup.tree(destination))


def test_dependency_directory_cannot_redirect(tmp_path, monkeypatch):
    spec = specification(tmp_path)
    outside = tmp_path/'outside'; outside.mkdir()
    (Path(spec['new_release'])/'.venv').symlink_to(outside)
    monkeypatch.setattr(adoption, 'command', lambda *args, **kwargs: pytest.fail('must reject before uv'))
    with pytest.raises(AdoptionError, match='redirected'):
        adoption.install_dependencies(spec)


@pytest.mark.asyncio
async def test_preparing_interruption_status_and_abort_do_not_require_missing_secrets(tmp_path, monkeypatch):
    spec = specification(tmp_path)
    journal = {'format': 'salesbuddy-legacy-adoption-v1', 'directory': str(tmp_path), 'spec': spec,
               'spec_sha256': adoption.fingerprint(spec), 'phase': 'preparing'}
    atomic_json(tmp_path/'ADOPTION.json', journal)
    class Connection:
        async def close(self):
            pass
    async def connect(_):
        return Connection()
    async def old_state(*_):
        return 'old'
    monkeypatch.setattr(adoption, 'connect', connect)
    monkeypatch.setattr(adoption, 'state', old_state)
    monkeypatch.setattr(adoption, 'original_files_unchanged', lambda *_: None)
    monkeypatch.setattr(adoption, 'systemctl', lambda *_: None)
    assert (await adoption.execute('status', tmp_path))['database_state'] == 'old'
    assert (await adoption.execute('abort', tmp_path))['phase'] == 'aborted'
    assert not (tmp_path/'credentials.json').exists()
    with pytest.raises(AdoptionError, match='aborted'):
        await adoption.execute('resume', tmp_path)


def test_backup_member_escape_refused_even_with_matching_manifest_hash(tmp_path):
    directory = tmp_path/'backup'; directory.mkdir()
    atomic_json(directory/'BACKUP.json', {'format': 'salesbuddy-legacy-backup-v1',
        'archives': {'database.dump': 'a'*64, 'roles.sql': 'b'*64},
        'paths': [{'copy': '../outside', 'source': str(tmp_path), 'entries': {'.': {}}}]})
    with pytest.raises(AdoptionError, match='member path'):
        backup.validate_backup(directory, digest(directory/'BACKUP.json'))


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_peer_connection_restores_all_identities_on_failure(tmp_path, monkeypatch, cancel):
    import asyncio
    from types import SimpleNamespace
    spec = specification(tmp_path)
    spec['database']['host'] = '/tmp'
    spec['maintenance_auth'] = {'mode': 'peer', 'os_user': 'postgres'}
    changes = []
    monkeypatch.setattr(backup.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(backup.os, 'getegid', lambda: 0)
    monkeypatch.setattr(backup.os, 'getgroups', lambda: [0, 10])
    monkeypatch.setattr(backup.pwd, 'getpwnam', lambda _: SimpleNamespace(pw_uid=123, pw_gid=456))
    for method in ('setgroups', 'setegid', 'seteuid'):
        monkeypatch.setattr(backup.os, method, lambda value, name=method: changes.append((name, value)))
    async def failed(**kwargs):
        assert kwargs['password'] is None and kwargs['ssl'] is False
        raise asyncio.CancelledError() if cancel else OSError('synthetic connection failure')
    monkeypatch.setattr(backup.asyncpg, 'connect', failed)
    with pytest.raises(asyncio.CancelledError if cancel else OSError):
        await backup.connect(spec)
    assert changes == [('setgroups', []), ('setegid', 456), ('seteuid', 123),
                       ('seteuid', 0), ('setegid', 0), ('setgroups', [0, 10])]
    assert backup._PEER_GATE.acquire(blocking=False)
    backup._PEER_GATE.release()


def test_peer_restore_passes_private_archive_by_fd_not_path(tmp_path, monkeypatch):
    from types import SimpleNamespace
    spec = specification(tmp_path)
    spec['database']['host'] = '/tmp'
    spec['maintenance_auth'] = {'mode': 'peer', 'os_user': 'postgres'}
    source = tmp_path/'private.dump'; source.write_bytes(b'synthetic dump')
    monkeypatch.setattr(backup.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(backup.pwd, 'getpwnam', lambda _: SimpleNamespace(pw_uid=123, pw_gid=456))
    calls = []
    def runner(args, **kwargs):
        calls.append(args)
        if args[-1] == '--version':
            return SimpleNamespace(returncode=0, stdout=b'pg_restore (PostgreSQL) 16.15')
        assert kwargs['stdin'].read() == b'synthetic dump'
        assert kwargs['user'] == 123 and kwargs['group'] == 456 and kwargs['extra_groups'] == []
        assert kwargs['cwd'] == '/' and str(source) not in args
        return SimpleNamespace(returncode=0, stderr=b'')
    monkeypatch.setattr(backup.subprocess, 'run', runner)
    backup.pg_run(spec, 'pg_restore', ['--single-transaction', '--dbname=synthetic'], input_file=source)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_stop_persists_restart_guard_when_queue_has_not_drained(tmp_path, monkeypatch):
    spec = specification(tmp_path)
    journal = {'format': 'salesbuddy-legacy-adoption-v1', 'directory': str(tmp_path), 'spec': spec,
               'spec_sha256': adoption.fingerprint(spec), 'phase': 'prepared'}
    atomic_json(tmp_path/'ADOPTION.json', journal)
    class Connection:
        async def close(self):
            pass
    async def connect(_):
        return Connection()
    async def not_drained(_):
        raise AdoptionError('synthetic running job')
    calls = []
    monkeypatch.setattr(adoption, 'connect', connect)
    monkeypatch.setattr(adoption, 'drained', not_drained)
    monkeypatch.setattr(adoption, 'validate_secrets', lambda *_: {})
    monkeypatch.setattr(adoption, 'release_check', lambda *_: None)
    monkeypatch.setattr(adoption, 'original_files_unchanged', lambda *_: None)
    monkeypatch.setattr(adoption, 'systemctl', lambda *args: calls.append(args))
    with pytest.raises(AdoptionError, match='running job'):
        await adoption.execute('stop', tmp_path)
    assert (tmp_path/'maintenance.block').is_file()
    assert read_private(tmp_path/'ADOPTION.json')['phase'] == 'maintenance_guarded'
    assert calls == [('daemon-reload',), ('stop', 'sales-api.service')]
    for path in adoption.maintenance_files(tmp_path, spec):
        assert 'ConditionPathExists=!' in Path(path).read_text()


def test_unknown_later_dropin_refused_before_preparation(tmp_path):
    spec = specification(tmp_path)
    services = {key: {'configuration_files': {
        str(Path(spec['dropins'][key]).parent/'release-runtime.conf'): 'old',
        str(Path(spec['dropins'][key]).parent/'zz-python-module.conf'): 'old',
        str(Path(spec['dropins'][key]).parent/'zzzz-runtime-credentials.conf'): 'old',
    }} for key in adoption.SERVICE_NAMES}
    adoption.check_override_order(spec, services)
    services['api']['configuration_files'][str(Path(spec['dropins']['api']).parent/'zzzzzzz-other.conf')] = 'unreviewed'
    with pytest.raises(AdoptionError, match='sorts after'):
        adoption.check_override_order(spec, services)


@pytest.mark.parametrize('change', ['cwd', 'executable', 'argument', 'ignore_failure'])
def test_effective_service_rejects_later_runtime_overrides(tmp_path, monkeypatch, change):
    spec = specification(tmp_path)
    entry = [adoption.service_argv(spec, 'worker')[0], adoption.service_argv(spec, 'worker'), False, 0, 0, 0, 0, 0, 0, 0]
    monkeypatch.setattr(adoption, 'service_property', lambda *args, **kwargs: spec['current_link'])
    monkeypatch.setattr(adoption, 'command', lambda *_: json.dumps({'type': 'a(sasbttttuii)', 'data': [entry]}).encode())
    adoption.verify_effective_service(spec, 'worker')
    if change == 'cwd':
        monkeypatch.setattr(adoption, 'service_property', lambda *args, **kwargs: spec['old_release'])
    elif change == 'executable':
        entry[0] = spec['old_release']+'/.venv/bin/python'
    elif change == 'argument':
        entry[1][-1] = 'sales_backend.feishu_worker'
    else:
        entry[2] = True
    with pytest.raises(AdoptionError, match='Effective systemd'):
        adoption.verify_effective_service(spec, 'worker')


def test_effective_environment_files_require_complete_original_order(tmp_path, monkeypatch):
    spec = specification(tmp_path)
    files = [*spec['dropins'].values(), *adoption.maintenance_files(tmp_path, spec)]
    for name in files:
        Path(name).write_bytes(b'synthetic unit'); Path(name).chmod(0o600)
    hashes = {name: digest(name) for name in files}
    journal = {'spec': spec, 'installed_files': hashes, 'services': {
        key: {'configuration_files': {}, 'environment_files': ['/original.env', '/credentials.env']}
        for key in adoption.SERVICE_NAMES}}
    snapshot = {key: {'configuration_files': {
        spec['dropins'][key]: hashes[spec['dropins'][key]],
        str(Path(spec['dropins'][key]).parent/'80-saas-maintenance.conf'): hashes[str(Path(spec['dropins'][key]).parent/'80-saas-maintenance.conf')],
    }, 'environment_files': ['/original.env', '/credentials.env', spec['environment_files'][key]]}
        for key in adoption.SERVICE_NAMES}
    monkeypatch.setattr(adoption, 'service_snapshot', lambda: snapshot)
    monkeypatch.setattr(adoption, 'verify_effective_service', lambda *_: None)
    adoption.verify_installed_unit_files(journal)
    snapshot['api']['environment_files'] = ['/credentials.env', '/original.env', spec['environment_files']['api']]
    with pytest.raises(AdoptionError, match='EnvironmentFiles differ'):
        adoption.verify_installed_unit_files(journal)


def test_public_web_origin_is_bound_to_verified_candidate(tmp_path, monkeypatch):
    spec = specification(tmp_path)
    calls = []
    def verified(archive, digest):
        calls.append((archive, digest))
        return {}, {'api_base_url': 'https://sales.synthetic.example:8443/api/v1'}
    monkeypatch.setattr(adoption, 'read_verified', verified)
    origin = adoption.candidate_web_origin(spec)
    assert origin == 'https://sales.synthetic.example:8443'
    assert calls == [(spec['archive'], spec['archive_sha256'])]
    config = adoption.config_bytes(spec, {s: 'synthetic' for s in adoption.SERVICES}, origin)
    assert b'PUBLIC_WEB_ORIGIN=https://sales.synthetic.example:8443\n' in config['api']
    with pytest.raises(AdoptionError, match='unsafe environment'):
        adoption.package_web_origin({'api_base_url': 'https://bad"host/api/v1'})


def test_service_snapshot_preserves_multiline_environment_files(tmp_path, monkeypatch):
    fragment = tmp_path/'synthetic.service'; fragment.write_text('[Service]\n')
    override = tmp_path/'late.conf'; override.write_text('[Service]\n')
    def systemctl(*args):
        if args[0] == 'cat':
            return '[Service]\n'
        return ('LoadState=loaded\nMainPID=123\nActiveState=active\n'
                'EnvironmentFiles=/etc/synthetic-base.env (ignore_errors=no)\n'
                'EnvironmentFiles=/etc/synthetic-optional.env (ignore_errors=yes)\n'
                'EnvironmentFiles=/etc/synthetic-credentials.env (ignore_errors=no)\n'
                f'FragmentPath={fragment}\nDropInPaths={override}\n')
    monkeypatch.setattr(adoption, 'systemctl', systemctl)
    for row in adoption.service_snapshot().values():
        assert row['environment_files'] == ['/etc/synthetic-base.env', '-/etc/synthetic-optional.env',
                                            '/etc/synthetic-credentials.env']
