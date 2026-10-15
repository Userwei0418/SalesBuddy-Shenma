"""Run the candidate API on a marked server clone, never a second customer service.

Run as root on the sales host after prepare-whole-upgrade.py. The API only listens
on loopback; no general Worker/FeishuWorker is started. Secrets stay on the host.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from urllib.parse import urlsplit, urlunsplit

UNIT = "shenma-preflight-api.service"
UNIT_PATH = Path("/run/systemd/system") / UNIT
MARKER = "shenma-whole-121-validation"


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status", "stop", "job"))
    parser.add_argument("--receipt", required=True, type=Path)
    for name in ("job-id", "workspace-id", "user-id", "aggregate-id", "job-type"):
        parser.add_argument("--" + name)
    args = parser.parse_args()
    assert os.geteuid() == 0 and os.uname().nodename == "salesbuddy"
    receipt = json.loads(args.receipt.read_text())
    assert receipt["status"] == "passed" and receipt["production_services_changed"] is False
    database = receipt["validation_database"]
    assert re.fullmatch(r"shenma_preflight_[0-9]{8}t[0-9]{6}z", database)
    marker = subprocess.check_output(["sudo", "-u", "postgres", "psql", "-X", "-At", "-d", database,
        "-c", "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()"], text=True).strip()
    assert marker == MARKER
    revision = receipt["revision"]
    assert re.fullmatch(r"[a-f0-9]{40}", revision)
    release = Path("/opt/shenma-sales/releases") / revision
    assert release != Path("/opt/shenma-sales/current").resolve(), "Refusing to treat the live release as preflight"
    assert (release / "REVISION").read_text().strip() == revision
    private = Path("/var/lib/shenma-preflight") / database
    if args.action == "start":
        assert not UNIT_PATH.exists(), "A preflight API unit already exists"
        private.mkdir(mode=0o700, parents=True, exist_ok=True)
        raw = Path("/etc/shenma-sales/runtime.env").read_text()
        values = dict(line.split("=", 1) for line in raw.splitlines() if line and not line.startswith("#") and "=" in line)
        original = urlsplit(values["DATABASE_URL"].strip('"\''))
        assert original.path == "/shenma_sales"
        dsn = urlunsplit(original._replace(path="/" + database))
        overrides = {"DATABASE_URL": dsn, "ACCESS_TOKEN_SECRET": secrets.token_urlsafe(48),
                     "MODEL_KEY_ROTATION_COMMAND_JSON": "[]", "WORKER_ID": "shenma-preflight-never-poll"}
        envfile = private / "override.env"
        envfile.write_text("".join(k + "=" + v + "\n" for k, v in overrides.items()))
        envfile.chmod(0o600)
        content = f"""[Unit]
Description=Temporary Shenma upgrade clone API ({MARKER})
[Service]
User=shenma-sales
Group=shenma-sales
WorkingDirectory={release}/backend
EnvironmentFile=/etc/shenma-sales/runtime.env
EnvironmentFile={envfile}
ExecStart={release}/backend/.venv/bin/python -m uvicorn sales_backend.main:app --host 127.0.0.1 --port 18081 --workers 1
Restart=no
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={private}
"""
        # No worker service or public nginx route is created.
        UNIT_PATH.write_text(content)
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "start", UNIT])
        print(json.dumps({"unit": UNIT, "database": database, "address": "127.0.0.1:18081", "revision": revision}))
    elif args.action == "stop":
        if UNIT_PATH.exists():
            assert MARKER in UNIT_PATH.read_text()
            run(["systemctl", "stop", UNIT])
            UNIT_PATH.unlink()
            run(["systemctl", "daemon-reload"])
        print(json.dumps({"unit": UNIT, "stopped": True}))
    elif args.action == "status":
        run(["systemctl", "is-active", UNIT])
    else:
        # The helper establishes peer maintenance auth, checks the marker and
        # permanently drops to the service OS user before running model code.
        # No credentials appear in argv.
        job_args = []
        for name in ("job-id", "workspace-id", "user-id", "aggregate-id", "job-type"):
            value = getattr(args, name.replace("-", "_"))
            assert value, "Exact job parameters are required"
            job_args.extend(("--" + name, value))
        env = os.environ.copy()
        for filename in (Path("/etc/shenma-sales/runtime.env"), private / "override.env"):
            for line in filename.read_text().splitlines():
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env[key] = value.strip('"\'')
        env["SHENMA_PREFLIGHT_RUNTIME_DSN"] = env["DATABASE_URL"]
        env["SHENMA_PREFLIGHT_MAINTENANCE_DSN"] = "postgresql://postgres@/" + database + "?host=/var/run/postgresql"
        env["SHENMA_PREFLIGHT_PEER_MAINTENANCE"] = "1"
        run([str(release / "backend/.venv/bin/python"), str(release / "backend/tests/system/shenma_whole_single_job.py"),
             *job_args], env=env, cwd=release / "backend")


if __name__ == "__main__":
    main()
