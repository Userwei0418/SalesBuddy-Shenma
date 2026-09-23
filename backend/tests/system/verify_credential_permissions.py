"""Bounded Linux root-only private-file regression using an existing unprivileged UID.

Only creates/removes its own temporary fixture files; no accounts, services,
production environment, keys or database are read or changed.
"""
import base64
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile


def stage_probe(backend: Path, root: Path) -> Path:
    """Give the child only an owned, traversable copy of the production probe.

    GitHub runner checkout ancestors may be private to the runner account. Do
    not chmod them or let their permissions masquerade as a keyring rejection.
    Copy source only; dependencies remain in the selected Python installation.
    """
    staged = root / "backend"
    source = staged / "src" / "sales_backend"
    shutil.copytree(backend / "src" / "sales_backend", source,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for relative in ("scripts/runtime_config_credentials.py", "deploy/verify_service_identity.py"):
        target = staged / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(backend / relative, target)
    for path in (staged, *staged.rglob("*")):
        path.chmod(0o755 if path.is_dir() else 0o644)
    return staged


def diagnostic(phase, result):
    # The fixture contains no real credentials, but do not print captured CLI
    # streams: expose process/bootstrap failure separately without leaking values.
    return (f"{phase}: rc={result.returncode}, stdout_bytes={len(result.stdout.encode())}, "
            f"stderr_bytes={len(result.stderr.encode())}")


def main():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise SystemExit("Linux root is required for the isolated UID permission test")
    account = pwd.getpwnam("nobody")
    assert account.pw_uid > 0 and account.pw_gid > 0
    backend = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="salegent-credential-permissions-") as directory:
        root = Path(directory)
        root.chmod(0o755)
        staged = stage_probe(backend, root)
        keyring = root / "fixture-keyring.json"
        keyring.write_text(json.dumps({"keys": {"permission-fixture": base64.b64encode(b"p" * 32).decode()}}))
        keyring.chmod(0o600)
        environment = {"PATH": os.defpath, "PYTHONPATH": str(staged / "src"), "APP_ENV": "test",
                       "AUTH_MODE": "password", "CONFIG_CREDENTIAL_KEY_ID": "permission-fixture",
                       "CONFIG_CREDENTIAL_KEYRING_FILE": str(keyring)}
        command = [sys.executable, str(staged / "scripts/runtime_config_credentials.py"), "keyring-check"]
        def execute(*, privileged=False):
            identity = {} if privileged else {"user": account.pw_uid, "group": account.pw_gid, "extra_groups": []}
            checked = command if privileged else [
                sys.executable, str(staged / "deploy/verify_service_identity.py"),
                "--uid", str(account.pw_uid), "--gid", str(account.pw_gid), "--", *command]
            return subprocess.run(checked, env=environment, cwd=root, capture_output=True, text=True,
                                  timeout=15, **identity)
        # Reproduce the false positive, then prove the deployment's actual UID
        # check rejects the very same root-owned 0600 file.
        root_result = execute(privileged=True)
        assert root_result.returncode == 0 and json.loads(root_result.stdout)["uid"] == 0, diagnostic("root fixture initialization", root_result)
        denied = execute()
        assert denied.returncode == 1 and not denied.stdout, diagnostic("service UID denial", denied)
        assert json.loads(denied.stderr)["code"] == "RUNTIME_CONFIG_MAINTENANCE_FAILED"
        os.chown(keyring, account.pw_uid, account.pw_gid)
        successful = execute()
        assert successful.returncode == 0, diagnostic("service-owned 0600", successful)
        observed = json.loads(successful.stdout)
        assert observed["uid"] == account.pw_uid and observed["gid"] == account.pw_gid
        keyring.chmod(0o640)
        rejected_mode = execute()
        assert rejected_mode.returncode == 1 and not rejected_mode.stdout, diagnostic("0640 rejection", rejected_mode)
        assert json.loads(rejected_mode.stderr)["code"] == "RUNTIME_CONFIG_MAINTENANCE_FAILED"
        print(json.dumps({"passed": 4, "checks": ["root_false_positive_reproduced", "service_uid_denies_root_file",
                                                  "service_owned_0600_ready", "group_readable_0640_rejected"]}))


if __name__ == "__main__":
    main()
