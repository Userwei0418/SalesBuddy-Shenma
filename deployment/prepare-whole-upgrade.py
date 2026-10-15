"""Prepare a paired release and verify migrations on a live database snapshot.

This does not stop or switch production services. Private dumps and validation
receipts stay on the sales host. The temporary database is removed after checks.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess

SPEC = importlib.util.spec_from_file_location("upgrade_sales", Path(__file__).with_name("upgrade-sales.py"))
upgrade = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upgrade)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive", "wheels"):
        parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--" + name + "-sha256", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--expected-current", required=True)
    parser.add_argument("--expected-schema", default="V159", choices=("V159", "V160", "V161"))
    parser.add_argument("--target-schema", default="V399", choices=("V399",))
    parser.add_argument("--keep-verified-clone", action="store_true", help="Retain a verified clone for loopback API acceptance; never run general workers against it")
    args = parser.parse_args()
    assert os.geteuid() == 0 and socket.gethostname() == "salesbuddy"
    assert "172.22.9.234" in subprocess.check_output(["hostname", "-I"], text=True).split()
    # Public source/wheels/venv must be readable by the service OS user even
    # when a private root launcher inherited umask 077. Secrets switch below.
    os.umask(0o022)
    assert re.fullmatch("[a-f0-9]{40}", args.revision)
    root = Path("/opt/shenma-sales")
    old = (root / "current").resolve()
    assert (old / "REVISION").read_text().strip() == args.expected_current
    assert upgrade.sql("shenma_sales", "SELECT max(version) FROM ops.schema_migration") == args.expected_schema
    for name in ("archive", "wheels"):
        assert hashlib.sha256(getattr(args, name).read_bytes()).hexdigest() == getattr(args, name + "_sha256")
    new = root / "releases" / args.revision
    # Never reuse a failed/unverified source extraction.
    assert not new.exists(), "Candidate release directory already exists"
    new.mkdir(mode=0o755)
    upgrade.unpack(args.archive, new, "SalesBuddy-Shenma-source-" + args.revision[:12] + "/")
    upgrade.run(["sha256sum", "-c", "SHA256SUMS"], cwd=new, stdout=subprocess.DEVNULL)
    assert (new / "REVISION").read_text().strip() == args.revision
    assert json.loads((new / "frontend/project.config.json").read_text())["appid"] == "wx2824bdeb58528fd8"
    assert (new / "frontend/web/dist/index.html").is_file()
    for file in [*(old / "database").glob("*.sql"), *(old / "database/migrations").glob("*.sql")]:
        assert file.read_bytes() == (new / file.relative_to(old)).read_bytes(), "Historical SQL changed"
    wheels = root / "dependencies" / args.wheels_sha256[:16]
    if not wheels.exists():
        wheels.mkdir(parents=True)
        upgrade.unpack(args.wheels, wheels)
    upgrade.run(["sha256sum", "-c", "SHA256SUMS"], cwd=wheels, stdout=subprocess.DEVNULL)
    uv = "/opt/shenma-tools/uv-x86_64-unknown-linux-gnu/uv"
    python = new / "backend/.venv/bin/python"
    upgrade.run([uv, "venv", "--python", str((old / "backend/.venv/bin/python").resolve()), str(python.parent.parent)])
    upgrade.run([uv, "--no-cache", "pip", "install", "--python", str(python), "--no-index", "--find-links", str(wheels),
                 "-r", str(new / "deployment/runtime-requirements.txt")])
    upgrade.run([uv, "--no-cache", "pip", "install", "--python", str(python), "--no-index", "--find-links", str(wheels),
                 "--no-deps", "-e", str(new / "backend")])
    shutil.copyfile(new / "REVISION", new / "backend/REVISION")
    upgrade.run(["/usr/sbin/runuser", "-u", "shenma-sales", "--", str(python), "-c", "import sales_backend.main,segno,pypinyin"], cwd=new)
    os.umask(0o077)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path("/var/backups/shenma-sales") / ("preflight-" + stamp)
    backup.mkdir(parents=True, mode=0o700)
    database = "shenma_preflight_" + stamp.lower()
    created = False
    passed = False
    try:
        with (backup / "database.dump").open("wb") as stream:
            upgrade.run(["/usr/sbin/runuser", "-u", "postgres", "--", "pg_dump", "-Fc", "shenma_sales"], stdout=stream)
        upgrade.run(["/usr/sbin/runuser", "-u", "postgres", "--", "createdb", "-T", "template0", database])
        created = True
        with (backup / "database.dump").open("rb") as stream:
            upgrade.run(["/usr/sbin/runuser", "-u", "postgres", "--", "pg_restore", "--exit-on-error", "-d", database], stdin=stream)
        upgrade.sql("postgres", f"COMMENT ON DATABASE {database} IS 'shenma-whole-121-validation'")
        before = upgrade.preserved_snapshot(database)
        for filename in ("migration.json", "migration-repeat.json"):
            with (backup / filename).open("w") as stream:
                upgrade.run(["/usr/sbin/runuser", "-u", "postgres", "--", "env", "DATABASE_URL=postgresql:///" + database + "?host=/var/run/postgresql",
                             str(python), str(new / "database/scripts/migrate.py")], stdout=stream)
        assert upgrade.sql(database, "SELECT max(version) FROM ops.schema_migration") == args.target_schema
        assert upgrade.preserved_snapshot(database, before["columns"]) == before, "Existing customer rows changed"
        assert all(row["status"] == "unchanged" for row in json.loads((backup / "migration-repeat.json").read_text()))
        receipt = {name: getattr(args, name) for name in ("revision", "expected_current", "expected_schema", "target_schema", "archive_sha256", "wheels_sha256")}
        receipt.update(status="passed", existing_rows_unchanged=True, migration_repeat_unchanged=True,
                       preserved_tables=len(before["rows"]), source_database="shenma_sales",
                       snapshot_sha256=hashlib.sha256((backup / "database.dump").read_bytes()).hexdigest(),
                       production_services_changed=False, validation_database=database if args.keep_verified_clone else None,
                       receipt=str(backup / "preflight.json"))
        (backup / "preservation.json").write_text(json.dumps(before, indent=2) + "\n")
        (backup / "preflight.json").write_text(json.dumps(receipt, indent=2) + "\n")
        passed = True
        print(json.dumps(receipt), flush=True)
    finally:
        if created and not (passed and args.keep_verified_clone):
            upgrade.run(["/usr/sbin/runuser", "-u", "postgres", "--", "dropdb", database])


if __name__ == "__main__":
    main()
