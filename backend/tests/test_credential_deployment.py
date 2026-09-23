import base64
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).parents[1]


def load(relative):
    spec = importlib.util.spec_from_file_location("credential_deployment_test", BACKEND / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def credential_settings(tmp_path, module):
    path = tmp_path / "private-keyring.json"
    path.write_text(json.dumps({"keys": {"release-key": base64.b64encode(b"t" * 32).decode()}}))
    path.chmod(0o600)
    return replace(module.get_settings(), config_credential_keyring_file=str(path),
                   config_credential_key_id="release-key", database_url="postgresql://never-connect.invalid/missing")


@pytest.mark.asyncio
async def test_premigration_keyring_check_does_not_construct_database(tmp_path, monkeypatch, capsys):
    module = load("scripts/runtime_config_credentials.py")
    settings = credential_settings(tmp_path, module)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    def forbidden(*args):
        raise AssertionError("Pre-migration check must never construct a Database")
    monkeypatch.setattr(module, "Database", forbidden)
    assert await module.bounded_run(SimpleNamespace(action="keyring-check")) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "ready", "target_encryption_key_id": "release-key",
                                         "uid": os.geteuid(), "gid": os.getegid()}
    assert not captured.err


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "mode", "wrong-key"])
async def test_keyring_preflight_failure_is_bounded_and_sanitized(tmp_path, monkeypatch, capsys, failure):
    module = load("scripts/runtime_config_credentials.py")
    settings = credential_settings(tmp_path, module)
    file = Path(settings.config_credential_keyring_file)
    if failure == "missing":
        file.unlink()
    elif failure == "mode":
        file.chmod(0o640)
    else:
        settings = replace(settings, config_credential_key_id="unavailable-key")
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    assert await module.bounded_run(SimpleNamespace(action="keyring-check")) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err) == {"status": "failed", "code": "RUNTIME_CONFIG_MAINTENANCE_FAILED"}
    assert str(file) not in captured.err and "unavailable-key" not in captured.err


@pytest.mark.parametrize("action", ["check", "reencrypt"])
def test_database_actions_require_explicit_existing_identity(monkeypatch, action):
    import sys
    module = load("scripts/runtime_config_credentials.py")
    monkeypatch.setattr(sys, "argv", ["runtime_config_credentials.py", action])
    with pytest.raises(SystemExit) as failure:
        module.main()
    assert failure.value.code == 2


def test_dependency_constraints_replace_only_reviewed_packages(tmp_path):
    module = load("deploy/runtime_dependency_constraints.py")
    lock = BACKEND / "uv.lock"
    versions = module.build_constraints(lock, {"fastapi": "old-kept", "cffi": "old-replaced",
                                               "PyCParser": "old-replaced", "pip": "ignored",
                                               "sales_assistant_backend": "ignored"})
    assert versions == {"fastapi": "old-kept", "cryptography": "46.0.7", "cffi": "2.1.1", "pycparser": "3.0"}
    file = tmp_path / "constraints.txt"
    file.write_text(''.join(f"{name}=={version}\n" for name, version in versions.items()))
    assert module.verify_constraints(lock, file, versions)["status"] == "ready"
    for bad in ({**versions, "cryptography": "46.0.8"}, {**versions, "fastapi": "upgraded"},
                {name: version for name, version in versions.items() if name != "cffi"}):
        with pytest.raises(ValueError):
            module.verify_constraints(lock, file, bad)


@pytest.mark.parametrize("bad", ["missing", "ambiguous", "changed-closure"])
def test_incomplete_or_expanded_credential_lock_requires_review(tmp_path, bad):
    module = load("deploy/runtime_dependency_constraints.py")
    packages = ['[[package]]\nname="cryptography"\nversion="46.0.7"\n',
                '[[package]]\nname="cffi"\nversion="2.1.1"\n',
                '[[package]]\nname="pycparser"\nversion="3.0"\n']
    if bad == "missing":
        packages.pop()
    elif bad == "ambiguous":
        packages.append(packages[0])
    else:
        packages[0] += 'dependencies=[{name="unreviewed"}]\n'
    file = tmp_path / "lock.toml"
    file.write_text(''.join(packages))
    with pytest.raises(ValueError):
        module.locked_dependencies(file)


def test_forward_credential_gates_surround_migration_and_switch():
    script = (BACKEND / "deploy/deploy-forward-release.sh").read_text()
    assert script.index("verify_credential_runtime before keyring-check") < script.index("STOPPED=1")
    assert script.index('verify_db "$TO_SCHEMA" "$TO_SCHEMA"') < script.index("verify_credential_runtime migrated check")
    assert script.index("verify_credential_runtime migrated check") < script.index('switch_current "$NEW"')
    assert script.index('health "$NEW"') < script.index("verify_credential_runtime after check")
    assert script.index("verify_credential_runtime after check") < script.index("FORWARD_RELEASE_READY=")
    assert '"$CREDENTIAL_WORKSPACE" --administrator "$CREDENTIAL_ADMINISTRATOR"' in script
    assert "Credential-capable release requires" in script


@pytest.mark.parametrize("action,mode", [("keyring-check", "same"), ("check", "same"),
                                       ("check", "different"), ("keyring-check", "failed")])
def test_actual_deploy_function_requires_both_service_checks(tmp_path, action, mode):
    script = (BACKEND / "deploy/deploy-forward-release.sh").read_text()
    start = script.index("verify_credential_runtime() {")
    function = script[start:script.index("\n}", start) + 2]
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/bash\nset -eu\n"
        '[[ " $* " == *" --as-service-user "* ]]\n'
        + ('[[ " $* " == *" --workspace fixture --administrator EXISTING_ADMIN "* ]]\n' if action == "check"
           else '[[ " $* " != *" --workspace "* ]]\n')
        + 'if [[ " $* " == *"sales-worker.service"* ]]; then\n'
        + ("exit 42\n" if mode == "failed" else 'echo \'{"status":"' + ("different" if mode == "different" else "ready") + '"}\'\n')
        + 'else echo \'{"status":"ready"}\'; fi\n')
    python.chmod(0o700)
    check = tmp_path / "credentials.py"
    check.touch()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    result = subprocess.run(["/bin/bash", "-c", 'set -euo pipefail\nNEW="$1"; EVIDENCE="$2"; CREDENTIAL_CHECK="$3"\n'
        'COMMIT=0123456789abcdef; CREDENTIAL_WORKSPACE=fixture; CREDENTIAL_ADMINISTRATOR=EXISTING_ADMIN\n'
        + function + '\nverify_credential_runtime before "$4"\n', "verify-test", str(tmp_path), str(evidence), str(check), action],
        capture_output=True, text=True, timeout=10)
    assert (result.returncode == 0) == (mode == "same"), result.stderr


def test_permission_probe_stages_only_source_under_traversable_owned_directory(tmp_path):
    module = load("tests/system/verify_credential_permissions.py")
    source = tmp_path / "private-checkout"
    source.mkdir(mode=0o700)
    for relative in ("src/sales_backend/config.py", "src/sales_backend/security/runtime_credentials.py",
                     "scripts/runtime_config_credentials.py", "deploy/verify_service_identity.py"):
        path = source / relative
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text("# production fixture source\n")
        path.chmod(0o600)
    cache = source / "src/sales_backend/__pycache__"
    cache.mkdir()
    (cache / "config.pyc").write_bytes(b"not a source file")
    (source / ".env").write_text("must not be staged")
    public = tmp_path / "owned-probe"
    public.mkdir(mode=0o755)
    staged = module.stage_probe(source, public)
    assert source.stat().st_mode & 0o777 == 0o700
    assert not (staged / ".env").exists() and not (staged / "src/sales_backend/__pycache__").exists()
    assert (staged / "scripts/runtime_config_credentials.py").read_text() == "# production fixture source\n"
    for path in (staged, *staged.rglob("*")):
        assert path.stat().st_mode & 0o777 == (0o755 if path.is_dir() else 0o644)


def test_required_ci_checks_continue_after_an_independent_permission_failure():
    import yaml

    workflow = yaml.safe_load((BACKEND.parent / ".github/workflows/checks.yml").read_text())
    steps = workflow["jobs"]["backend-and-database"]["steps"]
    wanted = {"python backend/scripts/export_openapi.py --check",
              "PYTHONPATH=backend:backend/src python -m pytest database/tests -q",
              "node --test frontend/tests/*.test.js",
              "python backend/tests/system/run_integration_postgres.py"}
    found = [step for step in steps if step.get("run") in wanted]
    assert len(found) == 4
    for step in found:
        assert "!cancelled()" in step["if"] and "install_dependencies.outcome == 'success'" in step["if"]
        assert not step.get("continue-on-error")
    permission = next(step for step in steps if step.get("name") == "Private keyring checks use actual unprivileged UID")
    assert not permission.get("continue-on-error")
    assert 'sudo "$(command -v python)"' in permission["run"]
