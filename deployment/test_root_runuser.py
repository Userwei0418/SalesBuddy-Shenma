"""Root deployment identity switches must not request another sudoers grant."""

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent


@pytest.mark.parametrize("filename,expected", [
    ("prepare-whole-upgrade.py", 6), ("upgrade-sales.py", 8), ("preflight-runtime.py", 1),
])
def test_all_nested_identity_commands_use_fixed_runuser_and_delimiter(filename, expected):
    tree = ast.parse((ROOT / filename).read_text())
    switches = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant):
            first = node.elts[0].value
            assert first != "sudo", (filename, node.lineno)
            if first == "/usr/sbin/runuser":
                prefix = [entry.value for entry in node.elts[:4]]
                assert prefix in [
                    ["/usr/sbin/runuser", "-u", "postgres", "--"],
                    ["/usr/sbin/runuser", "-u", "shenma-sales", "--"],
                ]
                switches.append(node)
        if isinstance(node, ast.Call):
            assert not any(keyword.arg == "shell" and isinstance(keyword.value, ast.Constant)
                           and keyword.value.value is True for keyword in node.keywords)
    assert len(switches) == expected


def test_postgres_query_keeps_peer_identity_without_changing_query_contract(monkeypatch):
    spec = importlib.util.spec_from_file_location("runuser_upgrade_under_test", ROOT / "upgrade-sales.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def check_output(args, **kwargs):
        calls.append((args, kwargs))
        return "postgres\n"

    monkeypatch.setattr(module.subprocess, "check_output", check_output)
    assert module.sql("postgres", "SELECT current_user") == "postgres"
    assert calls == [([
        "/usr/sbin/runuser", "-u", "postgres", "--", "psql", "-X", "-v", "ON_ERROR_STOP=1",
        "-At", "-d", "postgres", "-c", "SELECT current_user",
    ], {"text": True})]


def test_single_job_identity_switch_has_no_nested_sudo_or_runuser():
    helper = ROOT.parent / "backend/tests/system/shenma_whole_single_job.py"
    source = helper.read_text()
    assert 'os.seteuid(postgres.pw_uid)' in source
    assert 'os.setgroups([])' in source and 'os.setgid(runtime.pw_gid)' in source
    assert 'os.setuid(runtime.pw_uid)' in source
    tree = ast.parse(source)
    assert not any(isinstance(node, ast.Constant) and node.value in {"sudo", "/usr/sbin/runuser"}
                   for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str))


@pytest.mark.parametrize("filename", ["prepare-whole-upgrade.py", "upgrade-sales.py"])
def test_inherited_private_umask_cannot_make_new_release_or_venv_private(tmp_path, monkeypatch, filename):
    import hashlib
    import io
    import json
    import os
    import tarfile
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location("umask_phase_under_test", ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    upgrade = module.upgrade if filename.startswith("prepare") else module
    old_revision, new_revision = "a" * 40, "b" * 40
    install = tmp_path / "sales"
    old = install / "releases" / old_revision
    (old / "database").mkdir(parents=True)
    (old / "database/old.sql").write_text("-- preserved\n")
    (old / "REVISION").write_text(old_revision)
    (old / "backend/.venv/bin").mkdir(parents=True)
    (old / "backend/.venv/bin/python").write_text("synthetic interpreter")
    (install / "current").symlink_to(old)

    def archive(path, entries):
        with tarfile.open(path, "w") as bundle:
            for name, body in entries.items():
                entry = tarfile.TarInfo(name)
                entry.size, entry.mode = len(body), 0o644
                bundle.addfile(entry, io.BytesIO(body))

    source, wheels = tmp_path / "source.tar", tmp_path / "wheels.tar"
    prefix = "SalesBuddy-Shenma-source-" + new_revision[:12] + "/"
    archive(source, {prefix + name: body for name, body in {
        "REVISION": new_revision.encode(), "database/old.sql": b"-- preserved\n",
        "backend/package/module.py": b"# public source\n",
        "frontend/project.config.json": json.dumps({"appid": "wx2824bdeb58528fd8"}).encode(),
        "frontend/web/dist/index.html": b"<html>synthetic</html>",
    }.items()})
    archive(wheels, {"synthetic.whl": b"offline-wheel", "SHA256SUMS": b""})
    wheel_sha = hashlib.sha256(wheels.read_bytes()).hexdigest()
    real_path = Path

    def path(value):
        return install if str(value) == "/opt/shenma-sales" else real_path(value)

    monkeypatch.setattr(module, "Path", path)
    monkeypatch.setattr(upgrade, "Path", path)
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.socket, "gethostname", lambda: "salesbuddy")
    monkeypatch.setattr(module.subprocess, "check_output", lambda *args, **kwargs: "172.22.9.234\n")
    monkeypatch.setattr(upgrade, "sql", lambda *args: "V159")
    monkeypatch.setattr(upgrade, "validate_preflight", lambda *args: {"status": "passed"})
    calls = []

    class StopBeforeAnyBackup(RuntimeError):
        pass

    def run(args, **kwargs):
        calls.append(list(map(str, args)))
        if args[0].endswith("/uv") and args[1] == "venv":
            target = real_path(args[-1]) / "bin"
            target.mkdir(parents=True)
            (target / "python").write_text("synthetic executable")
        if args[:4] == ["/usr/sbin/runuser", "-u", "shenma-sales", "--"]:
            raise StopBeforeAnyBackup()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(upgrade, "run", run)
    argv = [filename, "--archive", str(source), "--archive-sha256", hashlib.sha256(source.read_bytes()).hexdigest(),
            "--wheels", str(wheels), "--wheels-sha256", wheel_sha,
            "--revision", new_revision, "--expected-current", old_revision]
    monkeypatch.setattr("sys.argv", argv)
    inherited = os.umask(0o077)
    try:
        with pytest.raises(StopBeforeAnyBackup):
            module.main()
        candidate = install / "releases" / new_revision
        for relative in (".", "backend", "backend/package", "backend/.venv", "backend/.venv/bin"):
            assert ((candidate / relative).stat().st_mode & 0o777) == 0o755
        assert (candidate / "backend/package/module.py").stat().st_mode & 0o777 == 0o644
        dependencies = install / "dependencies" / wheel_sha[:16]
        assert dependencies.stat().st_mode & 0o777 == 0o755
        assert (dependencies / "synthetic.whl").stat().st_mode & 0o777 == 0o644
        installs = [call for call in calls if "pip" in call]
        assert len(installs) == 2
        assert all(call[1:4] == ["--no-cache", "pip", "install"] for call in installs)
    finally:
        os.umask(inherited)


@pytest.mark.parametrize("filename", ["prepare-whole-upgrade.py", "upgrade-sales.py"])
def test_private_creation_mask_precedes_backup_directory(filename):
    tree = ast.parse((ROOT / filename).read_text())
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    masks, backup_lines = [], []
    for node in ast.walk(main):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "os" and node.func.attr == "umask":
            masks.append((node.lineno, node.args[0].value))
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "backup" and node.func.attr == "mkdir":
            backup_lines.append(node.lineno)
    assert len(backup_lines) == 1
    assert sorted(value for line, value in masks) == [0o022, 0o077]
    prior_mask = max((line, value) for line, value in masks if line < backup_lines[0])
    assert prior_mask[1] == 0o077
