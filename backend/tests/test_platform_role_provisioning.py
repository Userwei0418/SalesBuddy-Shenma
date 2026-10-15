"""Credential durability around uncertain platform-role commits; no real SQL."""
import importlib.util
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    'platform_role_provisioning', Path(__file__).parents[1] / 'deploy/provision_platform_runtime.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.fixture
def root_user(monkeypatch):
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)


@pytest.mark.parametrize('outcome', ['success', 'reported_failure', 'lost_commit_receipt'])
def test_dispatched_sql_keeps_private_durable_credential(root_user, tmp_path, monkeypatch, outcome):
    private_file = tmp_path / 'platform.env'
    flushed, calls = [], []
    real_fsync = os.fsync

    def fsync(fd):
        flushed.append(os.fstat(fd).st_mode)
        real_fsync(fd)

    monkeypatch.setattr(module.os, 'fsync', fsync)

    def runner(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            return SimpleNamespace(returncode=0, stdout='0\n')
        assert len(flushed) == 2  # credential contents and parent directory
        assert private_file.stat().st_mode & 0o777 == 0o600
        assert private_file.read_text().startswith('PLATFORM_DATABASE_URL=')
        if outcome == 'lost_commit_receipt':
            raise subprocess.TimeoutExpired('synthetic-psql', 1)
        return SimpleNamespace(returncode=int(outcome == 'reported_failure'))

    args = dict(role='synthetic_platform', database='synthetic_db', socket='/tmp', port=5432, runner=runner)
    if outcome == 'success':
        result = module.provision(private_file, **args)
        assert result == {'provisioned': True, 'role': 'synthetic_platform', 'service_started': False}
    else:
        with pytest.raises((ValueError, subprocess.TimeoutExpired)):
            module.provision(private_file, **args)
    saved = private_file.read_bytes()
    assert saved and private_file.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        # Refuse overwrite; do not regenerate a credential after uncertainty.
        module.provision(private_file, **{**args, 'runner': lambda *a, **k: SimpleNamespace(returncode=0, stdout='0')})
    assert private_file.read_bytes() == saved
    assert len(calls) == 2


def test_failure_before_dispatch_removes_only_new_file(root_user, tmp_path, monkeypatch):
    path = tmp_path / 'platform.env'
    calls = []

    def runner(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='0')

    def failed_sync(fd):
        raise OSError('synthetic storage failure')

    monkeypatch.setattr(module.os, 'fsync', failed_sync)
    with pytest.raises(OSError):
        module.provision(path, role='synthetic_platform', database='synthetic_db', socket='/tmp', port=5432, runner=runner)
    assert len(calls) == 1 and not path.exists()


def test_existing_role_does_not_create_file_or_reset_password(root_user, tmp_path):
    path = tmp_path / 'platform.env'
    with pytest.raises(ValueError, match='Role exists'):
        module.provision(path, role='synthetic_platform', database='synthetic_db', socket='/tmp', port=5432,
                         runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout='1'))
    assert not path.exists()
