"""Use synthetic credentials only
        never start a service or make network calls."""
import importlib.util
import json
import os
import runpy
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/local'))
import model_config as model  # noqa: E402
from local_paths import state_directory  # noqa: E402

spec = importlib.util.spec_from_file_location('model_hook_dev', ROOT / 'tools/local/dev.py')
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)
SYNTHETIC = 'synthetic-test-only-never-a-real-key'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    home = tmp_path.resolve()
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    directory = home / '.config/raccoon-salesbuddy/credentials'
    directory.mkdir(parents=True, mode=0o700)
    key = directory / 'provider.key'
    key.write_text(SYNTHETIC + '\n')
    key.chmod(0o600)
    root = home / 'checkout'
    state = state_directory(root)
    cfg = {'kind': 'salesbuddy-local-v1', 'root': str(root), 'state_root': str(state),
           'local_model': {'api_key_file': str(key)}, 'api_port': 18822, 'db_port': 15452,
           'api_password': 'fake-api', 'worker_password': 'fake-worker',
           'token_secret': 'fake-token', 'platform_password': 'fake-platform'}
    return cfg, key


def test_default_off_and_inherited_environment_cleared(setup):
    cfg, _ = setup
    cfg.pop('local_model')
    env = {'SENSEAUDIO_API_KEY': SYNTHETIC, 'SENSEAUDIO_BASE_URL': 'bad',
           'CONFIG_CREDENTIAL_LEGACY_KEY_FILE': 'bad', 'AGENT_FDE_API_KEY': 'bad', 'PATH': '/bin'}
    model.apply_model_config(cfg, env)
    assert env == {'PATH': '/bin'}


@pytest.mark.parametrize('service', ['api', 'worker'])
def test_actual_runner_loads_consistently_before_service_import(setup, monkeypatch, tmp_path, capsys, service):
    cfg, key = setup
    ring = key.parent / 'profiles' / Path(cfg['state_root']).name / 'keyring.json'
    ring.parent.mkdir(parents=True, mode=0o700)
    ring.write_text('not read by hook')
    ring.chmod(0o600)
    cfg['local_model'].update(keyring_file=str(ring), key_id='local-test',
                              base_url='https://model.example.invalid/v1', llm_model='test-model')
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps(cfg))
    seen = []

    def entry(*args, **kwargs):
        seen.append(dict(os.environ))

    monkeypatch.setattr(sys, 'argv', ['run_service.py', service, str(settings)])
    monkeypatch.setitem(sys.modules, 'uvicorn', SimpleNamespace(run=entry))
    monkeypatch.setattr(runpy, 'run_module', entry)
    monkeypatch.setattr(os, 'environ', {'AGENT_FDE_API_KEY': 'inherited', 'SENSEAUDIO_API_KEY': 'inherited'})
    runpy.run_path(str(ROOT / 'tools/local/run_service.py'), run_name='__main__')
    assert len(seen) == 1
    assert seen[0]['SENSEAUDIO_API_KEY'] == SYNTHETIC
    assert seen[0]['CONFIG_CREDENTIAL_KEYRING_FILE'] == str(ring)
    assert seen[0]['CONFIG_CREDENTIAL_KEY_ID'] == 'local-test'
    assert seen[0]['SENSEAUDIO_LLM_MODEL'] == 'test-model'
    assert 'AGENT_FDE_API_KEY' not in seen[0]
    assert SYNTHETIC not in ' '.join(sys.argv)
    assert SYNTHETIC not in str(capsys.readouterr())
    assert SYNTHETIC not in settings.read_text()


@pytest.mark.parametrize('mode', [0o644, 0o400, 0o660, 0o700, 0o1600])
def test_file_mode_exactly_0600(setup, mode):
    cfg, key = setup
    key.chmod(mode)
    with pytest.raises(RuntimeError, match=model.ERROR):
        model.validate_model_config(cfg)


@pytest.mark.parametrize('kind', [
    'symlink', 'directory', 'fifo', 'hardlink', 'missing', 'outside', 'parent_symlink', 'writable_parent',
])
def test_unsafe_paths_fail_closed(setup, kind, tmp_path):
    cfg, key = setup
    if kind == 'symlink':
        other = key.with_name('linked.key')
        other.symlink_to(key)
        key = other
    elif kind == 'directory':
        key.unlink()
        key.mkdir(mode=0o600)
    elif kind == 'fifo':
        key.unlink()
        os.mkfifo(key, 0o600)
    elif kind == 'hardlink':
        os.link(key, key.with_name('extra.key'))
    elif kind == 'missing':
        key.unlink()
    elif kind == 'outside':
        other = tmp_path / 'outside.key'
        key.rename(other)
        key = other
    elif kind == 'parent_symlink':
        directory = key.parent
        moved = directory.with_name('original')
        directory.rename(moved)
        directory.symlink_to(moved)
    else:
        key.parent.chmod(0o777)
    cfg['local_model']['api_key_file'] = str(key)
    with pytest.raises(RuntimeError, match=model.ERROR):
        model.validate_model_config(cfg)


def test_wrong_owner_rejected(setup, monkeypatch):
    cfg, _ = setup
    original = os.fstat

    def wrong(fd):
        info = original(fd)
        if stat.S_ISREG(info.st_mode):
            return SimpleNamespace(st_mode=info.st_mode, st_uid=info.st_uid + 1, st_nlink=1)
        return info

    monkeypatch.setattr(os, 'fstat', wrong)
    with pytest.raises(RuntimeError, match=model.ERROR):
        model.validate_model_config(cfg)


def test_cross_profile_keyring_rejected(setup):
    cfg, key = setup
    cfg['local_model'].update(keyring_file=str(key), key_id='old-profile')
    with pytest.raises(RuntimeError, match=model.ERROR):
        model.validate_model_config(cfg)


@pytest.mark.parametrize('changes', [
    {'api_key': SYNTHETIC}, {'key_id': 'orphan'}, {'base_url': 'http://example.invalid'},
    {'base_url': 'https://user:secret@example.invalid'}, {'base_url': 'https://example.invalid/?token=secret'},
    {'llm_model': 'test\nsecret'}, {'api_key_file': None}, {'api_key_file': '../secret.key'},
])
def test_invalid_schema_and_urls_rejected(setup, changes):
    cfg, _ = setup
    cfg['local_model'].update(changes)
    with pytest.raises(RuntimeError, match=model.ERROR):
        model.validate_model_config(cfg)


@pytest.mark.parametrize('raw', [b'', b'line-one\nline-two', b'\xffsecret', b'x' * 16385])
def test_bad_contents_only_rejected_at_startup_without_leak(setup, raw, capsys):
    cfg, key = setup
    key.write_bytes(raw)
    assert model.validate_model_config(cfg)  # Metadata only.
    with pytest.raises(RuntimeError) as error:
        model.apply_model_config(cfg, {})
    assert str(error.value) == model.ERROR
    assert capsys.readouterr() == ('', '')


def test_configuration_writes_references_only_and_disable_is_explicit(setup, monkeypatch, tmp_path):
    cfg, _ = setup
    monkeypatch.setattr(dev, 'LOCAL', tmp_path)
    original = os.read
    monkeypatch.setattr(os, 'read', lambda *args: pytest.fail('configure must not read key bytes'))
    dev.configure_model(cfg, cfg['local_model'])
    saved = json.loads((tmp_path / 'settings.json').read_text())
    assert saved['local_model'] == cfg['local_model']
    assert SYNTHETIC not in (tmp_path / 'settings.json').read_text()
    assert stat.S_IMODE((tmp_path / 'settings.json').stat().st_mode) == 0o600
    dev.configure_model(saved, {})
    assert 'local_model' not in json.loads((tmp_path / 'settings.json').read_text())
    monkeypatch.setattr(os, 'read', original)


def test_up_rejects_metadata_before_stopping_existing_services(setup, monkeypatch):
    cfg, key = setup
    key.chmod(0o644)
    monkeypatch.setattr(dev, 'source_files', lambda: {})
    monkeypatch.setattr(dev, 'config', lambda **kwargs: cfg)
    monkeypatch.setattr(dev, 'stop_apps', lambda: pytest.fail('must not stop services'))
    with pytest.raises(RuntimeError, match=model.ERROR):
        dev.up()


@pytest.mark.parametrize('service', ['api', 'worker'])
def test_runner_failure_is_redacted_and_does_not_start_service(setup, monkeypatch, tmp_path, capsys, service):
    cfg, key = setup
    key.write_text(SYNTHETIC + '\ninvalid-second-line')
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys, 'argv', ['run_service.py', service, str(settings)])
    monkeypatch.setattr(os, 'environ', {})
    monkeypatch.setitem(sys.modules, 'uvicorn', SimpleNamespace(run=lambda *a, **k: pytest.fail('started')))
    monkeypatch.setattr(runpy, 'run_module', lambda *a, **k: pytest.fail('started'))
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(ROOT / 'tools/local/run_service.py'), run_name='__main__')
    assert str(error.value) == model.ERROR
    assert 'SENSEAUDIO_API_KEY' not in os.environ
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('arguments', [
    ['up', '--model-api-key-file', 'anything'],
    ['configure', '--disable-model', '--model-api-key-file', 'anything'],
    ['configure', '--test-appid', 'anything', '--disable-model'],
])
def test_cli_rejects_ambiguous_or_nonconfigure_options(monkeypatch, arguments):
    monkeypatch.setattr(sys, 'argv', ['dev.py', *arguments])
    with pytest.raises(SystemExit) as error:
        dev.main()
    assert error.value.code == 2


def test_cli_configures_references_then_disables_without_service_changes(setup, monkeypatch, tmp_path):
    cfg, key = setup
    monkeypatch.setattr(dev, 'LOCAL', tmp_path)
    monkeypatch.setattr(dev, 'config', lambda: cfg)
    monkeypatch.setattr(dev, 'stop_apps', lambda: pytest.fail('must not stop services'))
    monkeypatch.setattr(sys, 'argv', ['dev.py', 'configure', '--model-api-key-file', str(key)])
    dev.main()
    assert json.loads((tmp_path / 'settings.json').read_text())['local_model'] == cfg['local_model']
    monkeypatch.setattr(sys, 'argv', ['dev.py', 'configure', '--disable-model'])
    dev.main()
    assert 'local_model' not in json.loads((tmp_path / 'settings.json').read_text())


@pytest.mark.parametrize('raw', [b'', b'one\ntwo', b'\xffnot-utf8', b'x' * 16385])
def test_up_content_preflight_fails_before_stop_in_real_child(setup, monkeypatch, tmp_path, capsys, raw):
    cfg, key = setup
    key.write_bytes(raw)
    (tmp_path / 'settings.json').write_text(json.dumps(cfg))
    monkeypatch.setenv('HOME', str(tmp_path.resolve()))
    monkeypatch.setattr(dev, 'LOCAL', tmp_path)
    monkeypatch.setattr(dev, 'source_files', lambda: {})
    monkeypatch.setattr(dev, 'config', lambda **kwargs: cfg)
    monkeypatch.setattr(dev, 'stop_apps', lambda: pytest.fail('must not stop existing services'))
    with pytest.raises(RuntimeError) as error:
        dev.up()
    assert str(error.value) == model.ERROR
    assert capsys.readouterr() == ('', '')


def test_valid_preflight_reaches_stop_without_parent_key_read(setup, monkeypatch, tmp_path, capsys):
    cfg, _ = setup
    (tmp_path / 'settings.json').write_text(json.dumps(cfg))
    monkeypatch.setenv('HOME', str(tmp_path.resolve()))
    monkeypatch.setattr(dev, 'LOCAL', tmp_path)
    monkeypatch.setattr(dev, 'source_files', lambda: {})
    monkeypatch.setattr(dev, 'config', lambda **kwargs: cfg)
    monkeypatch.setattr(model, 'apply_model_config', lambda *a: pytest.fail('parent read'))

    class StopReached(Exception):
        pass

    def stop():
        raise StopReached

    monkeypatch.setattr(dev, 'stop_apps', stop)
    with pytest.raises(StopReached):
        dev.up()
    assert capsys.readouterr() == ('', '')


def test_disabled_preflight_does_not_launch_child(setup, monkeypatch):
    cfg, _ = setup
    cfg.pop('local_model')
    monkeypatch.setattr(dev.subprocess, 'run', lambda *a, **k: pytest.fail('unexpected child'))
    dev.preflight_model(cfg)


@pytest.mark.parametrize('failure', ['timeout', 'exit', 'oserror'])
def test_preflight_process_failures_and_io_are_closed(setup, monkeypatch, failure):
    cfg, _ = setup

    def run(command, **kwargs):
        assert SYNTHETIC not in str(command)
        assert SYNTHETIC not in str(kwargs)
        assert kwargs['stdin'] == kwargs['stdout'] == kwargs['stderr'] == dev.subprocess.DEVNULL
        assert kwargs['timeout'] == 10
        if failure == 'timeout':
            raise dev.subprocess.TimeoutExpired(command, 10, output=SYNTHETIC)
        if failure == 'oserror':
            raise OSError(SYNTHETIC)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(dev.subprocess, 'run', run)
    with pytest.raises(RuntimeError) as error:
        dev.preflight_model(cfg)
    assert str(error.value) == model.ERROR
