"""Private state, credential separation and process ownership for the installer."""
from argparse import Namespace
import importlib.util
import json
import os
import socket
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools/installation'))
import instance as installer
import instance_process as processes
from instance_files import InstanceError, atomic_json, clean_env, read_private, root_path, source_check
spec = importlib.util.spec_from_file_location('instance_bundle_fixture', Path(__file__).with_name('test_product_bundle.py'))
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)
source = fixture_module.source
run_git, prepare_bundle, bundle = fixture_module.run_git, fixture_module.prepare, fixture_module.bundle


@pytest.fixture
def prepared(source, tmp_path):
    for name in installer.REQUIRED_TOOLS:
        path = source/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# synthetic installer fixture\n')
    run_git(source, 'add', '.')
    run_git(source, 'commit', '-qm', 'installer fixture')
    files, modes, _ = prepare_bundle(source, api_url='http://127.0.0.1:18810/api/v1', profile='local')
    archive = tmp_path/'candidate.zip'
    digest = bundle.write_archive(files, modes, archive)
    args = Namespace(root=tmp_path.resolve()/'instance', archive=archive, sha256=digest,
        database='test_instance', db_host='127.0.0.1', db_port=15439, db_user='test_maintenance',
        db_sslmode='disable', api_port=18810, operator='TESTADMIN', operator_name='Synthetic operator')
    result = installer.prepare(args)
    assert result['database_created'] is False
    return args


def test_private_configuration_is_durable_and_each_service_only_has_its_credentials(prepared):
    root = prepared.root
    data, _ = source_check(root)
    bootstrap = read_private(root/'private/bootstrap.json')
    assert len(set(bootstrap['database_passwords'].values())) == 4
    assert len(bootstrap['operator_password']) >= 32
    for service in ('api', 'worker', 'feishu'):
        config = read_private(root/'private'/f'{service}.json')
        expected = {service, 'platform'} if service == 'api' else {service}
        assert set(config['database_passwords']) == expected
        for key in expected:
            assert config['database_passwords'][key] == bootstrap['database_passwords'][key]
        assert 'operator_password' not in config
    assert 'password' not in json.dumps(data)
    assert 'TESTADMIN' not in (root/'artifact.zip').read_bytes().decode('latin-1')
    assert root.stat().st_mode & 0o077 == 0
    for path in (root/'private').iterdir():
        assert path.stat().st_mode & 0o077 == 0


def test_prepare_refuses_existing_root_without_changing_credentials(prepared):
    before = (prepared.root/'private/bootstrap.json').read_bytes()
    with pytest.raises(FileExistsError):
        installer.prepare(prepared)
    assert (prepared.root/'private/bootstrap.json').read_bytes() == before


@pytest.mark.parametrize('case', ['source', 'config', 'symlink', 'permission', 'traversal'])
def test_check_refuses_source_or_private_config_drift(prepared, case):
    root = prepared.root
    if case == 'source': (root/'release/backend/src/sales_backend/main.py').write_text('changed')
    if case == 'config': atomic_json(root/'private/worker.json', {'unexpected': True})
    if case == 'symlink':
        (root/'private/api.json').rename(root/'private/old.json')
        (root/'private/api.json').symlink_to(root/'private/old.json')
    if case == 'permission': (root/'private/api.json').chmod(0o644)
    if case == 'traversal':
        data = read_private(root/'INSTANCE.json')
        data['private_hashes']['../outside.json'] = 'a'*64
        atomic_json(root/'INSTANCE.json', data)
    with pytest.raises((InstanceError, OSError)):
        source_check(root)


def test_private_parent_symlink_and_public_root_are_refused(tmp_path):
    root = tmp_path.resolve()/'actual'; root.mkdir(mode=0o700)
    link = tmp_path/'link'; link.symlink_to(root)
    with pytest.raises(InstanceError): root_path(link)
    root.chmod(0o755)
    with pytest.raises(InstanceError): root_path(root)


def test_clean_environment_does_not_inherit_database_models_or_proxy(monkeypatch):
    for key in ('DATABASE_URL', 'PLATFORM_DATABASE_URL', 'SENSEAUDIO_API_KEY', 'HTTPS_PROXY',
                'PYTHONPATH', 'UV_INDEX_URL', 'SALESBUDDY_INSTALL_DB_PASSWORD'):
        monkeypatch.setenv(key, 'synthetic-secret-value')
    env = clean_env()
    assert 'synthetic-secret-value' not in env.values()
    assert set(env) <= {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR'}


def test_exclusive_private_write_does_not_replace_previous_credentials(tmp_path):
    target = tmp_path/'credential.json'
    atomic_json(target, {'fixture': 'first'}, replace=False)
    with pytest.raises(FileExistsError): atomic_json(target, {'fixture': 'second'}, replace=False)
    assert read_private(target) == {'fixture': 'first'}


def test_listener_preflight_rejects_another_service_then_allows_restart():
    with socket.socket() as other:
        other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        other.bind(('127.0.0.1', 0)); other.listen(1)
        port = other.getsockname()[1]
        with pytest.raises(InstanceError, match='occupied'):
            installer.check_api_port(port)
    installer.check_api_port(port)


def test_process_lock_prevents_duplicate_service(prepared):
    descriptor = processes.service_lock(prepared.root, 'worker')
    try:
        with pytest.raises(InstanceError, match='already running'):
            processes.service_lock(prepared.root, 'worker')
    finally:
        os.close(descriptor)


def test_reused_pid_cannot_be_adopted_or_signalled(prepared, monkeypatch):
    root = prepared.root; data, _ = source_check(root)
    record = {'pid': 99999, 'started': 'old birth', 'command': 'old command',
              'instance_id': data['instance_id'], 'service': 'api'}
    atomic_json(root/'run/api.json', record)
    monkeypatch.setattr(processes, 'process_identity', lambda pid: {'pid': pid, 'started': 'new birth', 'command': 'new command'})
    monkeypatch.setattr(processes.os, 'kill', lambda *args: pytest.fail('must not signal a reused PID'))
    with pytest.raises(InstanceError, match='identity changed'):
        processes.stop(root, 'api', data['instance_id'])


@pytest.mark.parametrize('status', ['S', 'Ss', 'Z', 'Z+'])
def test_process_identity_uses_one_snapshot_and_preserves_old_record_format(monkeypatch, status):
    calls = []
    def ps(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=f'Fri Sep  4 12:00:01 2026     {status}  /python path with spaces/service.py worker\n')
    monkeypatch.setattr(processes.subprocess, 'run', ps)
    result = processes.process_identity(1234)
    assert len(calls) == 1
    assert calls[0] == ['ps', '-p', '1234', '-o', 'lstart=', '-o', 'stat=', '-o', 'command=']
    assert result == (None if status.startswith('Z') else {
        'pid':1234, 'started':'Fri Sep  4 12:00:01 2026', 'command':'/python path with spaces/service.py worker'})


@pytest.mark.parametrize('phase', ['state', 'before_signal'])
def test_process_that_exits_during_stop_is_not_mistaken_for_a_reused_pid(prepared, monkeypatch, phase):
    root = prepared.root; data, _ = source_check(root)
    record = {'pid':99999, 'started':'old birth', 'command':'old command',
              'instance_id':data['instance_id'], 'service':'api'}
    atomic_json(root/'run/api.json', record)
    live = {key:record[key] for key in ('pid','started','command')}
    observations = iter([{**live, 'command':'<exiting>'} if phase == 'state' else live, None])
    monkeypatch.setattr(processes, 'process_identity', lambda pid: next(observations))
    monkeypatch.setattr(processes.os, 'kill', lambda *args: pytest.fail('must not signal an exited process'))
    processes.stop(root, 'api', data['instance_id'])


def test_unparseable_process_identity_fails_closed(monkeypatch):
    monkeypatch.setattr(processes.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='unexpected ps output'))
    with pytest.raises(InstanceError, match='Cannot parse process identity'):
        processes.process_identity(1234)


@pytest.mark.parametrize('disappears', [True, False])
def test_after_owned_term_exit_race_is_waited_without_signalling_changed_pid(prepared, monkeypatch, disappears):
    root = prepared.root; data, _ = source_check(root)
    record = {'pid':99999, 'started':'old birth', 'command':'old command',
              'instance_id':data['instance_id'], 'service':'api'}
    atomic_json(root/'run/api.json', record)
    live = {key:record[key] for key in ('pid','started','command')}
    observations = iter([live, live, {**live, 'command':'<exiting>'}, {**live, 'command':'<exiting>'}, None])
    monkeypatch.setattr(processes, 'process_identity', lambda pid: next(observations))
    signals = []
    monkeypatch.setattr(processes.os, 'kill', lambda *args: signals.append(args))
    monkeypatch.setattr(processes.time, 'sleep', lambda _: None)
    clock = iter([0, 0, 1 if disappears else 21])
    monkeypatch.setattr(processes.time, 'monotonic', lambda: next(clock))
    if disappears:
        processes.stop(root, 'api', data['instance_id'])
    else:
        with pytest.raises(InstanceError, match='still stopping'):
            processes.stop(root, 'api', data['instance_id'])
    assert signals == [(99999, processes.signal.SIGTERM)]
    assert read_private(root/'run/api.json') == record


@pytest.mark.parametrize('field,value', [('api_port', 18811), ('db_host', 'external.example'), ('database', 'bad;sql')])
def test_invalid_target_rejected_before_creating_root(prepared, field, value):
    prepared.root = prepared.root.parent/'new'
    setattr(prepared, field, value)
    with pytest.raises(ValueError): installer.prepare(prepared)
    assert not prepared.root.exists()
