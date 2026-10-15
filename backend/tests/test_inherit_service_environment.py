import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


def load():
    path = Path(__file__).parents[1] / "deploy/inherit_service_environment.py"
    spec = importlib.util.spec_from_file_location("inherit_service_environment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_inherits_complete_order_and_appends_only_new_files():
    calls = []

    def systemctl(arguments, **options):
        calls.append((arguments, options))
        if arguments[0] == "busctl":
            return SimpleNamespace(stdout=json.dumps({"type": "as", "data": []}))
        return SimpleNamespace(stdout="/etc/base.env (ignore_errors=no)\n"
                               "/etc/visit-entry.env (ignore_errors=no)\n"
                               "/etc/report.env (ignore_errors=no)\n"
                               "/etc/optional.env (ignore_errors=yes)\n")

    command = load().inherited_command(
        "sales-worker.service", "advice-prepare", ["/release/.venv/bin/python", "/release/prepare.py"],
        extra_files=["/etc/report.env", "/etc/new-advice.env"], runner=systemctl,
    )
    assert calls[0][0] == ["systemctl", "show", "sales-worker.service", "--property=EnvironmentFiles", "--value"]
    assert [arg for arg in command if arg.startswith("--property=EnvironmentFile=")] == [
        "--property=EnvironmentFile=/etc/base.env", "--property=EnvironmentFile=/etc/visit-entry.env",
        "--property=EnvironmentFile=/etc/report.env", "--property=EnvironmentFile=-/etc/optional.env",
        "--property=EnvironmentFile=/etc/new-advice.env",
    ]
    assert command[-2:] == ["/release/.venv/bin/python", "/release/prepare.py"]
    assert "--property=RuntimeMaxSec=120" in command
    assert "--property=TimeoutStopSec=5" in command
    assert calls[0][1]["timeout"] == 10


@pytest.mark.parametrize("value", ["", "/etc/a.env", "/etc/a.env (ignore_errors=maybe)",
                                   r"/etc/a\x20b.env (ignore_errors=no)",
                                   "/etc/a.env (ignore_errors=no) unexplained"])
def test_unknown_environment_metadata_fails_closed(value):
    with pytest.raises(ValueError):
        load().environment_files(value)


def test_first_deployment_uses_inherited_launcher_instead_of_fixed_subset():
    script = (Path(__file__).parents[1] / "deploy/deploy-integrated-release.sh").read_text()
    assert '"$NEW/backend/deploy/inherit_service_environment.py"' in script
    assert "--service sales-worker.service" in script
    assert 'systemd-run --unit="salegent-advice-config-' not in script


def environment_runner(*, direct=(), passed=(), unset=(), properties=None):
    values = {"Environment": list(direct), "PassEnvironment": list(passed), "UnsetEnvironment": list(unset)}
    def run(arguments, **options):
        if arguments[0] == "systemctl":
            return SimpleNamespace(stdout="/etc/base.env (ignore_errors=no)")
        assert arguments[0] == "busctl"
        assert arguments[5] == "/org/freedesktop/systemd1/unit/sales_2dapi_2eservice"
        if properties is not None:
            properties.append(arguments[-1])
        return SimpleNamespace(stdout=json.dumps({"type": "as", "data": values[arguments[-1]]}))
    return run


@pytest.mark.parametrize("limit", [0, -1, 3601, "120", None])
def test_invalid_probe_lifetime_is_rejected_before_systemd(limit):
    def never(*args, **kwargs):
        raise AssertionError("Invalid limit must not launch or inspect a service")
    with pytest.raises(ValueError, match="runtime"):
        load().inherited_command("sales-api.service", "limit-check", ["/python"],
                                 max_runtime=limit, runner=never)


def test_direct_environment_pass_and_final_unset_are_preserved_without_secret_argv(tmp_path):
    private_file = tmp_path / "direct.env"
    secret = 'quoted "value" with space\n$literal=content'
    command = load().inherited_command(
        "sales-api.service", "auth-check", ["/release/python", "/release/auth.py"],
        runner=environment_runner(direct=["APP_ENV=production", "ACCESS_TOKEN_SECRET=" + secret],
                                  passed=["AUTH_MODE"], unset=["AUTH_MODE", "OTHER_VALUE"]),
        direct_environment_file=private_file,
    )
    assert 'APP_ENV="production"' in private_file.read_text()
    assert 'ACCESS_TOKEN_SECRET=' in private_file.read_text()
    assert private_file.stat().st_mode & 0o777 == 0o600
    assert "--property=PassEnvironment=AUTH_MODE" in command
    assert "--property=UnsetEnvironment=AUTH_MODE" in command
    assert "--property=UnsetEnvironment=OTHER_VALUE" in command
    assert secret not in repr(command)
    assert command.index("--property=EnvironmentFile=" + str(private_file)) < command.index("--property=EnvironmentFile=/etc/base.env")
    assert command.index("--property=EnvironmentFile=/etc/base.env") < command.index("--property=UnsetEnvironment=AUTH_MODE")


@pytest.mark.parametrize("values", [dict(unset=["AUTH_MODE=secret-not-for-argv"]),
                                    dict(passed=["AUTH_MODE=secret-not-for-argv"]),
                                    dict(direct=["INVALID NAME=secret-not-for-argv"])])
def test_unsupported_settings_fail_closed_without_echoing_values(values, tmp_path):
    with pytest.raises(ValueError) as failure:
        load().inherited_command("sales-api.service", "auth-check", ["/python"],
                                 runner=environment_runner(**values), direct_environment_file=tmp_path / "direct.env")
    assert "secret-not-for-argv" not in str(failure.value)


@pytest.mark.parametrize("output", ['{"type":"as","data":null}', '{"type":"s","data":"secret"}',
                                     '{"type":"as","data":[17]}', 'secret-not-json'])
def test_bus_metadata_must_be_losslessly_typed_json(output):
    def run(*args, **kwargs):
        return SimpleNamespace(stdout=output)
    with pytest.raises(ValueError) as failure:
        load().service_property("sales-api.service", "Environment", runner=run)
    assert "secret" not in str(failure.value)


def test_bus_failure_stays_bounded_and_does_not_print_captured_output():
    def run(arguments, **options):
        assert options["timeout"] == 10
        raise subprocess.CalledProcessError(1, arguments, output="captured-secret")
    with pytest.raises(ValueError) as failure:
        load().service_property("sales-api.service", "Environment", runner=run)
    assert "captured-secret" not in str(failure.value)


def test_launcher_removes_private_environment_file_after_failed_probe(monkeypatch, tmp_path, capsys):
    import sys
    import tempfile
    module = load()
    real_directory = tempfile.TemporaryDirectory
    monkeypatch.setattr(module.tempfile, "TemporaryDirectory",
                        lambda **options: real_directory(prefix=options["prefix"], dir=tmp_path))
    metadata = environment_runner(direct=["APP_ENV=production", "ACCESS_TOKEN_SECRET=private-fixture"])
    files = []
    def run(arguments, **options):
        if arguments[0] != "systemd-run":
            return metadata(arguments, **options)
        files.extend(Path(arg.split("=", 2)[2]) for arg in arguments
                     if arg.startswith("--property=EnvironmentFile=") and "salegent-service-env-" in arg)
        assert len(files) == 1 and files[0].exists()
        assert files[0].stat().st_mode & 0o777 == 0o600
        assert "private-fixture" not in repr(arguments)
        return SimpleNamespace(returncode=42)
    monkeypatch.setattr(module.subprocess, "run", run)
    # inherited_command's default runner was bound at import; pass current runner
    # explicitly through a tiny adapter, matching CLI process startup semantics.
    original = module.inherited_command
    monkeypatch.setattr(module, "inherited_command", lambda *args, **kwargs: original(*args, **kwargs, runner=run))
    monkeypatch.setattr(sys, "argv", ["inherit_service_environment.py", "--service", "sales-api.service",
                                     "--unit", "private-check", "--", "/python"])
    assert module.main() == 42
    assert all(not path.exists() for path in files)
    assert "private-fixture" not in repr(capsys.readouterr())


def identity_runner(*, user="sales-backend", group="sales-backend", dynamic=False, count=7):
    def run(arguments, **options):
        if arguments[0] == "systemctl":
            return SimpleNamespace(stdout='\n'.join(f"/etc/source-{i}.env (ignore_errors=no)" for i in range(count)))
        kind, data = {"User": ("s", user), "Group": ("s", group), "DynamicUser": ("b", dynamic),
                      "Environment": ("as", []), "PassEnvironment": ("as", []),
                      "UnsetEnvironment": ("as", ["IGNORED"])}[arguments[-1]]
        return SimpleNamespace(stdout=json.dumps({"type": kind, "data": data}))
    return run


@pytest.mark.parametrize("count", [7, 14])
def test_actual_service_identity_retains_each_ordered_environment(monkeypatch, count):
    module = load()
    monkeypatch.setattr(module.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=991))
    monkeypatch.setattr(module.grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=992))
    command = module.inherited_command("sales-api.service", "identity-check", ["/python", "check.py"],
        as_service_user=True, extra_files=["/etc/credentials.env"], runner=identity_runner(count=count))
    assert "--property=User=991" in command and "--property=Group=992" in command
    assert [arg for arg in command if arg.startswith("--property=EnvironmentFile=")] == [
        *(f"--property=EnvironmentFile=/etc/source-{i}.env" for i in range(count)),
        "--property=EnvironmentFile=/etc/credentials.env"]
    assert command[-8:] == [str(Path(module.__file__).with_name("verify_service_identity.py")),
                            "--uid", "991", "--gid", "992", "--", "/python", "check.py"]
    assert "--property=UnsetEnvironment=IGNORED" in command


@pytest.mark.parametrize("values", [dict(user=""), dict(group=""), dict(dynamic=True), dict(user="invalid name")])
def test_unresolved_or_dynamic_service_identity_is_rejected(values):
    with pytest.raises(ValueError, match="explicit static"):
        load().inherited_command("sales-api.service", "identity-check", ["/python"],
                                 as_service_user=True, runner=identity_runner(**values))


def test_root_service_and_missing_account_fail_closed(monkeypatch):
    module = load()
    monkeypatch.setattr(module.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=0))
    monkeypatch.setattr(module.grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=0))
    with pytest.raises(ValueError, match="non-root"):
        module.service_identity("sales-api.service", runner=identity_runner())
    def missing(name):
        raise KeyError("private-error")
    monkeypatch.setattr(module.pwd, "getpwnam", missing)
    with pytest.raises(ValueError) as failure:
        module.service_identity("sales-api.service", runner=identity_runner())
    assert "private-error" not in str(failure.value)


def test_default_launcher_does_not_assume_a_service_user():
    command = load().inherited_command("sales-api.service", "old-probe", ["/python"], runner=environment_runner())
    assert not any(arg.startswith("--property=User=") for arg in command)


def test_identity_wrapper_refuses_mismatch_before_executing_command(tmp_path):
    import os
    import sys
    script = Path(__file__).parents[1] / "deploy/verify_service_identity.py"
    marker = tmp_path / "should-not-exist"
    result = subprocess.run([sys.executable, str(script), "--uid", str(os.geteuid() + 1),
                             "--gid", str(max(1, os.getegid())), "--", sys.executable,
                             "-c", "from pathlib import Path; Path(__import__('sys').argv[1]).touch()", str(marker)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0 and "SERVICE_PROBE_IDENTITY_MISMATCH" in result.stderr
    assert not marker.exists()


def test_identity_wrapper_executes_as_verified_current_user():
    import os
    import sys
    if os.geteuid() == 0 or os.getegid() == 0:
        pytest.skip("Root is deliberately not a valid service probe identity")
    script = Path(__file__).parents[1] / "deploy/verify_service_identity.py"
    result = subprocess.run([sys.executable, str(script), "--uid", str(os.geteuid()), "--gid", str(os.getegid()),
                             "--", sys.executable, "-c", "import os; print(os.geteuid(), os.getegid())"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert result.stdout.strip() == f"{os.geteuid()} {os.getegid()}"
