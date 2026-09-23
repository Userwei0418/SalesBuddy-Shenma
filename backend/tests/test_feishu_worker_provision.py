import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('provision_feishu_worker',
    Path(__file__).parents[1] / 'deploy/provision_feishu_worker.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_new_password_only_flows_through_private_file_and_stdin(tmp_path, monkeypatch):
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module.secrets, 'token_urlsafe', lambda count: 'fixture-secret')
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        assert 'fixture-secret' not in repr(command)
        return SimpleNamespace(returncode=0, stdout='0\n')

    path = tmp_path / 'worker.env'
    result = module.provision(path, role='fixture_worker', database='fixture_db',
                              socket='/var/run/postgresql', port=5432, runner=runner)
    assert result == {'provisioned': True, 'role': 'fixture_worker', 'service_started': False}
    assert path.stat().st_mode & 0o777 == 0o600
    assert 'fixture-secret' in path.read_text()
    assert len(calls) == 2 and 'NOBYPASSRLS' in calls[1][1]['input']
    with pytest.raises(FileExistsError):
        module.provision(path, role='fixture_worker', database='fixture_db',
                         socket='/var/run/postgresql', port=5432, runner=runner)
    assert path.read_text().count('fixture-secret') == 1
    assert len(calls) == 3  # only read-only preflight; no second role mutation


def test_failed_role_creation_removes_only_new_file(tmp_path, monkeypatch):
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    count = 0

    def runner(command, **kwargs):
        nonlocal count
        count += 1
        return SimpleNamespace(returncode=0 if count == 1 else 1, stdout='0\n')

    path = tmp_path / 'worker.env'
    with pytest.raises(ValueError, match='provisioning failed'):
        module.provision(path, role='fixture_worker', database='fixture_db',
                         socket='/var/run/postgresql', port=5432, runner=runner)
    assert not path.exists()
