"""Human login guidance uses browser fixture credentials, never database secrets."""
import copy
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/local'))
spec = importlib.util.spec_from_file_location('local_account_bootstrap', ROOT / 'tools/local/bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


@pytest.fixture
def accounts():
    return [{'workspace': 'local-' + prefix.lower(), 'account': prefix + suffix, 'role': role}
            for prefix in ('A', 'B')
            for suffix, role in (('ADMIN', 'administrator'), ('SALES', 'sales'), ('MANAGER', 'manager'),
                                 ('FDE', 'fde'), ('OPS', 'operations'))]


def test_guide_explains_both_login_paths_and_the_shared_initial_browser_password(accounts):
    original = copy.deepcopy(accounts)
    guide = bootstrap.render_account_guide(accounts, 'synthetic-browser-password')
    assert 'http://127.0.0.1:18800/admin' in guide
    assert '填写公司码、账号和密码' in guide
    for row in accounts:
        assert '| ' + row['workspace'] + ' | ' + row['account'] + ' | ' + row['role'] + ' |' in guide
    platform = guide.split('## 平台管理\n', 1)[1]
    assert 'http://127.0.0.1:18800/platform' in platform and '`PLATFORMADMIN`' in platform
    assert '与上方“本机初始浏览器登录密码”相同' in platform
    assert guide.count('synthetic-browser-password') == 1
    assert 'python3 tools/local/dev.py code' in platform and '平台登录不需要公司码' in platform
    assert '动态码只能使用一次' in platform and '等待下一轮新码' in platform
    assert '已人工改密的账号请使用新密码' in guide
    assert '启动或升级不会重置账号密码或业务数据' in guide
    assert '不能登录生产' in guide and accounts == original


def test_guide_only_renders_selected_account_fields(accounts):
    # An augmented fixture must not accidentally serialize internal metadata.
    accounts[0].update(platform_password='synthetic-database-only',
                       token_secret='synthetic-signing-only', platform_totp_seed='synthetic-mfa-only')
    guide = bootstrap.render_account_guide(accounts, 'synthetic-browser-only')
    assert 'synthetic-browser-only' in guide
    for value in ('synthetic-database-only', 'synthetic-signing-only', 'synthetic-mfa-only'):
        assert value not in guide


@pytest.mark.parametrize('existing', [False, True])
def test_guide_write_is_private_complete_and_silent(tmp_path, accounts, existing, capsys):
    path = tmp_path / 'TEST_ACCOUNTS.md'
    if existing:
        path.write_text('previous guide')
        path.chmod(0o644)
    bootstrap.write_account_guide(path, accounts, 'synthetic-browser-only')
    assert path.read_text() == bootstrap.render_account_guide(accounts, 'synthetic-browser-only')
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [path]
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('failure', ['fsync', 'replace'])
def test_interrupted_guide_write_retains_previous_guide_and_removes_temporary(
        tmp_path, accounts, monkeypatch, failure):
    path = tmp_path / 'TEST_ACCOUNTS.md'
    path.write_text('previous guide remains usable')
    def interrupted(*args):
        raise OSError('synthetic interrupted write')
    monkeypatch.setattr(bootstrap.os, failure, interrupted)
    with pytest.raises(OSError, match='synthetic interrupted write'):
        bootstrap.write_account_guide(path, accounts, 'synthetic-browser-only')
    assert path.read_text() == 'previous guide remains usable'
    assert list(tmp_path.iterdir()) == [path]


def test_guide_refuses_a_symlink_without_changing_its_target(tmp_path, accounts):
    target = tmp_path / 'unrelated'
    target.write_text('must not change')
    path = tmp_path / 'TEST_ACCOUNTS.md'
    path.symlink_to(target)
    with pytest.raises(RuntimeError, match='must not be a symlink'):
        bootstrap.write_account_guide(path, accounts, 'synthetic-browser-only')
    assert path.is_symlink() and target.read_text() == 'must not change'
    assert set(tmp_path.iterdir()) == {path, target}
