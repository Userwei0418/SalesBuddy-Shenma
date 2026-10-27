"""Guard against local/production confusion, including uncommitted changes."""
import importlib.util
import json
import socket
import subprocess
import sys
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
import pytest

from sales_backend.release_identity import release_identity

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('salesbuddy_local_dev', ROOT / 'tools/local/dev.py')
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)


def test_sensitive_files_are_not_snapshot_inputs():
    for name in ['backend/.env', 'backend/src/.env.local', 'frontend/project.private.config.json',
                 'frontend/miniprogram/private.key', '.local/settings.json', 'docs/account-export.csv']:
        assert not dev.selected(name)
    assert dev.selected('frontend/miniprogram/config.js')
    assert dev.selected('backend/src/sales_backend/main.py')
    assert dev.selected('database/migrations/V153__example.sql')


def test_local_web_snapshot_builds_subpackage_routes_without_source_checkout(tmp_path):
    """Run the real Python builder with only files the LOCAL snapshot selects."""
    inputs = {name: content for name, content in dev.source_files().items()
              if name.startswith('frontend/')}
    for name, content in inputs.items():
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    result = subprocess.run(
        [sys.executable, '-I', '-c',
         "import runpy,sys;from pathlib import Path;"
         "script=Path(sys.argv[1]);sys.path.insert(0,str(script.parent));"
         "runpy.run_path(str(script),run_name='__main__')",
         str(tmp_path / 'frontend/web/scripts/build.py')],
        cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    bundle = (tmp_path / 'frontend/web/dist/bundle.js').read_text()
    data = json.loads(bundle.removeprefix('window.SALES_BUNDLE=').strip().removesuffix(';'))
    for route in ('pages/task-detail/index', 'pages/visit-entry/index', 'pages/visit-confirm/index'):
        assert route in data['config']['pages']
        assert route in data['pages']
        assert route in data['modules']


def test_check_does_not_initialize_an_environment(monkeypatch, tmp_path):
    local = tmp_path / 'not-created'
    monkeypatch.setattr(dev, 'LOCAL', local)
    with pytest.raises(RuntimeError, match='not initialized'):
        dev.config()
    assert not local.exists()


def test_fingerprint_tracks_content_and_deletions_not_dictionary_order():
    assert dev.fingerprint({'a': b'1', 'b': b'2'}) == dev.fingerprint({'b': b'2', 'a': b'1'})
    assert dev.fingerprint({'a': b'1'}) != dev.fingerprint({'a': b'2'})
    assert dev.fingerprint({'a': b'1'}) != dev.fingerprint({'a': b'1', 'b': b'2'})


def test_committed_build_reads_git_blobs_and_rejects_dirty_or_untracked_runtime(tmp_path):
    def git(*args):
        subprocess.run(['git','-C',str(tmp_path),*args], check=True, capture_output=True)
    git('init')
    git('config','user.email','local-test@example.invalid')
    git('config','user.name','Local test')
    source=tmp_path/'backend/src/example.py'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'first\n\x00binary input')
    git('add','backend');git('commit','-m','fixture')
    assert dev.committed_source_files(tmp_path)=={'backend/src/example.py':source.read_bytes()}
    (tmp_path/'unrelated-note.md').write_text('not part of runtime')
    assert dev.committed_source_files(tmp_path)==dev.source_files(tmp_path)
    source.write_text('modified')
    with pytest.raises(RuntimeError,match='requires all runtime'):dev.committed_source_files(tmp_path)
    git('add','backend');git('commit','-m','update')
    assert dev.committed_source_files(tmp_path)['backend/src/example.py']==b'modified'
    new=source.with_name('new.py');new.write_text('new runtime')
    with pytest.raises(RuntimeError,match='requires all runtime'):dev.committed_source_files(tmp_path)
    new.unlink();source.unlink()
    with pytest.raises(RuntimeError,match='requires all runtime'):dev.committed_source_files(tmp_path)
    source.symlink_to(tmp_path/'unrelated-note.md')
    git('add','backend');git('commit','-m','symlink fixture')
    with pytest.raises(RuntimeError,match='regular file'):dev.committed_source_files(tmp_path)


def test_local_port_probe_refuses_a_real_listener():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        with pytest.raises(RuntimeError, match='occupied'):
            dev.port_free(listener.getsockname()[1])


def test_ide_defaults_are_normalized_but_compiler_and_appid_changes_are_rejected(tmp_path):
    config = tmp_path / 'project.config.json'
    project = {'appid': 'test', 'setting': {'urlCheck': False}}
    config.write_text(json.dumps(project))
    baseline = dev.frontend_fingerprint(tmp_path)
    project['setting']['disableSWC'] = True
    config.write_text(json.dumps(project, indent=4))
    assert dev.frontend_fingerprint(tmp_path) == baseline
    project['setting']['disableSWC'] = False
    config.write_text(json.dumps(project))
    assert dev.frontend_fingerprint(tmp_path) != baseline
    project['setting'].pop('disableSWC')
    project['appid'] = 'another'
    config.write_text(json.dumps(project))
    assert dev.frontend_fingerprint(tmp_path) != baseline


def test_child_environment_excludes_production_credentials_and_proxy(monkeypatch):
    for name in ['DATABASE_URL', 'PGHOST', 'SENSEAUDIO_API_KEY', 'HTTP_PROXY', 'AGENT_FDE_BASE_URL']:
        monkeypatch.setenv(name, 'must-not-inherit')
    env = dev.clean_env()
    assert 'must-not-inherit' not in env.values()


def test_health_check_reaches_loopback_without_reading_system_proxies(monkeypatch):
    class Probe(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"local"}')

        def log_message(self, *args):
            pass

    def unexpected_proxy_lookup():
        raise AssertionError('Loopback must not depend on system proxy configuration')

    server = HTTPServer(('127.0.0.1', 0), Probe)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(dev, 'BASE', f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setattr(dev.urllib.request, 'getproxies', unexpected_proxy_lookup)
    try:
        assert dev.fetch('/health') == {'status': 'local'}
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_production_appid_is_rejected_before_local_configuration_write():
    production = json.loads((ROOT / 'frontend/project.config.json').read_text())['appid']
    for appid in (production, 'invalid'):
        with pytest.raises(RuntimeError, match='never the production AppID'):
            dev.configure_test_appid({}, appid)


def test_local_identity_only_activates_in_explicit_development(tmp_path, monkeypatch):
    (tmp_path / 'REVISION').write_text('a' * 40)
    (tmp_path / 'LOCAL_BUILD.json').write_text(json.dumps({
        'environment': 'local', 'build_id': 'b' * 64, 'revision': 'a' * 40}))
    monkeypatch.setenv('APP_ENV', 'production')
    assert release_identity(tmp_path)['mode'] == 'release'
    assert 'local_build_id' not in release_identity(tmp_path)
    monkeypatch.setenv('APP_ENV', 'development')
    assert release_identity(tmp_path)['mode'] == 'development'
    assert release_identity(tmp_path)['local_build_id'] == 'b' * 64


def test_git_read_timeout_leaves_the_existing_stack_running(monkeypatch):
    stopped=[]
    def timeout(args,**kwargs):
        assert kwargs['timeout']==60
        raise subprocess.TimeoutExpired(args,60)
    monkeypatch.setattr(dev,'run',timeout)
    monkeypatch.setattr(dev,'stop_apps',lambda:stopped.append(True))
    with pytest.raises(RuntimeError,match='Git source read timed out'):
        dev.up(committed=True)
    assert not stopped


def test_runtime_edit_during_blob_read_is_rejected_before_stopping_services(tmp_path,monkeypatch):
    def git(*args):
        subprocess.run(['git','-C',str(tmp_path),*args],check=True,capture_output=True)
    git('init');git('config','user.email','local-test@example.invalid');git('config','user.name','Local test')
    source=tmp_path/'backend/src/example.py';source.parent.mkdir(parents=True);source.write_text('before')
    git('add','backend');git('commit','-m','fixture')
    original=dev.run
    def edit_during_read(args,**kwargs):
        result=original(args,**kwargs)
        if 'cat-file' in args:source.write_text('edited while reading Git')
        return result
    monkeypatch.setattr(dev,'run',edit_during_read)
    with pytest.raises(RuntimeError,match='runtime files changed'):
        dev.committed_source_files(tmp_path)
    assert source.read_text()=='edited while reading Git'


@pytest.fixture
def running_local_build(tmp_path, monkeypatch):
    local = tmp_path/'local'; local.mkdir()
    target = local/'builds'/'old-build'
    source = target/'backend/src/main.py'; source.parent.mkdir(parents=True); source.write_text('old running source')
    front = local/'frontend'; front.mkdir()
    (front/'project.config.json').write_text(json.dumps({'appid':'touristappid'}))
    expected = {'source_mode':'commit', 'revision':'old-revision', 'build_id':'old-build',
                'generated_frontend_sha256':dev.frontend_fingerprint(front),
                'runtime_snapshot_sha256':dev.fingerprint({'backend/src/main.py':source.read_bytes()})}
    (local/'build.json').write_text(json.dumps(expected))
    for service in ('api','worker'):
        (local/(service+'.pid.json')).write_text(json.dumps({'pid':99999,'script':'run_service.py','build':'old-build'}))
    monkeypatch.setattr(dev, 'LOCAL', local)
    monkeypatch.setattr(dev, 'config', lambda: {})
    monkeypatch.setattr(dev, 'fetch', lambda path: {'local_build_id':'old-build','mode':'development'})
    monkeypatch.setattr(dev, 'local_ready', lambda: {'checks':{'database':True,'auth_configured':True}})
    monkeypatch.setattr(dev, 'run', lambda args, **kwargs: SimpleNamespace(stdout=b'new-revision\n'))
    monkeypatch.setattr(dev.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0,stdout='run_service.py'))
    monkeypatch.setattr(dev, 'python', lambda: '/synthetic/python')
    return local, source


def test_pre_upgrade_backup_verifies_old_runtime_but_cannot_authorize_current_acceptance(running_local_build, capsys):
    with pytest.raises(RuntimeError, match='source revision changed'):
        dev.doctor()
    dev.doctor(require_current_source=False)
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'running_build_verified'
    assert report['current_source_checked'] is False


@pytest.mark.parametrize('changed', ['snapshot', 'frontend', 'backend'])
def test_old_build_backup_still_refuses_runtime_drift(running_local_build, monkeypatch, changed):
    local, source = running_local_build
    if changed == 'snapshot':
        source.write_text('tampered')
    elif changed == 'frontend':
        mini = local/'frontend'/'miniprogram'; mini.mkdir()
        (mini/'injected.js').write_text('tampered')
    else:
        monkeypatch.setattr(dev, 'fetch', lambda path: {'local_build_id':'different-build','mode':'development'})
    with pytest.raises(RuntimeError):
        dev.doctor(require_current_source=False)
