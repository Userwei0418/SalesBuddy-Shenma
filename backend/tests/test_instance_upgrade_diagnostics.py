"""Upgrade system-test diagnostics must preserve failure without printing secrets."""
import importlib.util
import json
from pathlib import Path
import stat
import subprocess

import pytest

spec = importlib.util.spec_from_file_location(
    'upgrade_system_diagnostics', Path(__file__).parent/'system/verify_instance_upgrade.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.fixture
def root(tmp_path):
    target = tmp_path/'instance'
    (target/'logs').mkdir(parents=True)
    return target


def test_failed_checkpoint_retains_actual_exit_and_only_safe_categories(root, monkeypatch, capsys):
    secret = 'private-fixture-token-never-print'
    log = root/'logs/upgrade-dependencies.log'
    log.write_text('Earlier failure: certificate verify failed\n')

    def failed(*args, **kwargs):
        with log.open('a') as stream:
            stream.write('Failed to download https://user:'+secret+'@example.test/pkg: timed out\n')
        return subprocess.CompletedProcess(args[0], 1, secret, 'RuntimeError: '+secret)

    monkeypatch.setattr(probe.subprocess, 'run', failed)
    with pytest.raises(AssertionError, match='Crash checkpoint not reached'):
        probe.invoke(root, Path('/synthetic/pg'), 'resume', crash='old_release_saved')
    output = capsys.readouterr()
    diagnostic = json.loads(output.err)
    assert not output.out and secret not in output.err
    assert diagnostic['checkpoint'] == 'old_release_saved'
    assert diagnostic['actual_exit_code'] == 1 and diagnostic['expected_exit'] == '92'
    assert diagnostic['error_types'] == ['RuntimeError']
    assert diagnostic['categories'] == ['network_timeout', 'package_download']
    stored = root.parent/'upgrade-diagnostics.jsonl'
    assert json.loads(stored.read_text()) == diagnostic
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    assert secret in (root.parent/'upgrade-driver.log').read_text()


def test_checkpoint_success_does_not_emit_or_change_required_exit_code(root, monkeypatch, capsys):
    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a[0], 92, '', ''))
    probe.invoke(root, Path('/synthetic/pg'), 'resume', crash='metadata_switched')
    assert capsys.readouterr().err == ''
    assert not (root.parent/'upgrade-diagnostics.jsonl').exists()


def test_timeout_is_reported_and_original_timeout_still_raises(root, monkeypatch, capsys):
    error = subprocess.TimeoutExpired(['private-command'], 600, stderr=b'private-credential timed out')
    def timed_out(*args, **kwargs):
        assert kwargs['timeout'] == 600
        raise error
    monkeypatch.setattr(probe.subprocess, 'run', timed_out)
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        probe.invoke(root, Path('/synthetic/pg'), 'resume', crash='old_archive_saved')
    assert raised.value is error
    output = capsys.readouterr().err
    assert 'private-credential' not in output and 'private-command' not in output
    assert json.loads(output)['timed_out'] is True
    private_log = root.parent/'upgrade-driver.log'
    assert 'private-credential' in private_log.read_text()
    assert stat.S_IMODE(private_log.stat().st_mode) == 0o600


def test_unexpected_success_remains_failure_and_expected_rejection_is_quiet(root, monkeypatch, capsys):
    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, '{}', ''))
    with pytest.raises(AssertionError, match='Unsafe upgrade unexpectedly accepted'):
        probe.invoke(root, Path('/synthetic/pg'), 'abort', expect=False)
    assert json.loads(capsys.readouterr().err)['expected_exit'] == 'nonzero'
    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, '', 'sensitive'))
    probe.invoke(root, Path('/synthetic/pg'), 'abort', expect=False)
    assert capsys.readouterr().err == ''


def test_diagnostic_skips_symlink_log_and_bounds_tail(root, tmp_path, capsys):
    outside = tmp_path/'outside'
    outside.write_text('Failed to download secret')
    log = root/'logs/upgrade-dependencies.log'
    log.symlink_to(outside)
    result = subprocess.CompletedProcess([], 1, '', '')
    assert probe.failure_diagnostic(root, 'resume', 'old_release_saved', result)['categories'] == []
    log.unlink()
    log.write_text('Failed to download private\n' + 'x'*70000)
    assert probe.failure_diagnostic(root, 'resume', 'old_release_saved', result)['categories'] == []
    assert 'private' not in capsys.readouterr().err.replace('private_log_retained_in_fixture', '')
