"""Upgrade an existing Shenma release with a verified backup and database restore.

Run as root on salesbuddy. Source and dependency archives are transferred first;
this command never downloads code, changes customer configuration or initializes
accounts. Failed migrations retain the failed database and recover the verified
pre-upgrade copy while services are stopped.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tarfile
import time
import urllib.request


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def sql(database, query):
    return subprocess.check_output(["/usr/sbin/runuser", "-u", "postgres", "--", "psql", "-X", "-v", "ON_ERROR_STOP=1",
                                    "-At", "-d", database, "-c", query], text=True).strip()


def unpack(archive, destination, prefix=""):
    with tarfile.open(archive) as source:
        members = source.getmembers()
        for member in members:
            assert member.isfile() and member.name.startswith(prefix)
            relative = Path(member.name[len(prefix):])
            assert not relative.is_absolute() and ".." not in relative.parts
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            target.chmod(member.mode & 0o755)


def switch(root, name, target):
    temporary = root / (name + ".upgrade-next")
    assert not temporary.exists() and not temporary.is_symlink()
    temporary.symlink_to(target)
    os.replace(temporary, root / name)


CORE_SERVICES = ("shenma-api", "shenma-worker")
FEISHU_SERVICE = "shenma-feishu-worker.service"
PRESERVED_TABLES = (
    "platform.workspace", "platform.user_ref", "platform.password_credential",
    "platform.role_binding", "platform.team", "platform.team_membership",
    "config.account_authorization", "config.account_permission_override", "config.account_permission_role",
    "crm.customer", "crm.customer_ownership", "crm.customer_sales_member", "crm.contact",
    "crm.opportunity", "crm.opportunity_participant", "activity.visit", "activity.visit_opportunity",
    "workflow.task", "workflow.task_assignee", "insight.weekly_report", "insight.weekly_report_revision",
    "config.feishu_connection", "security.feishu_credential", "ops.feishu_record_map",
    "security.deployment_account_quota", "security.isolated_demo_account_quota",
)


def preserved_snapshot(database, columns=None):
    """Compare original columns; legitimate new columns must not hide old-row changes."""
    if columns is None:
        columns = {}
        for table in PRESERVED_TABLES:
            schema, name = table.split(".")
            names = sql(database, "SELECT string_agg(quote_ident(column_name),',' ORDER BY ordinal_position) "
                        "FROM information_schema.columns WHERE table_schema='" + schema + "' AND table_name='" + name + "'")
            if names:
                columns[table] = names
    rows = {}
    for table, fields in columns.items():
        query = ("SELECT md5(coalesce(string_agg(to_jsonb(t)::text,E'\\n' ORDER BY to_jsonb(t)::text),'')) "
                 f"FROM (SELECT {fields} FROM {table}) t")
        rows[table] = {"digest": sql(database, query), "count": sql(database, f"SELECT count(*) FROM {table}")}
    return {"columns": columns, "rows": rows}


def validate_preflight(path, args):
    if not path or not path.is_file():
        raise RuntimeError("V399 requires a successful server database-clone preflight receipt")
    receipt = json.loads(path.read_text())
    for name in ("revision", "expected_current", "expected_schema", "target_schema", "archive_sha256", "wheels_sha256"):
        if receipt.get(name) != getattr(args, name):
            raise RuntimeError("Preflight receipt mismatch: " + name)
    if receipt.get("status") != "passed" or not receipt.get("existing_rows_unchanged") or not receipt.get("migration_repeat_unchanged"):
        raise RuntimeError("Preflight verification did not pass")
    return receipt


def existing_services():
    """Keep upgrades compatible with installations that have no Feishu worker."""
    load_state = subprocess.check_output(
        ["systemctl", "show", "--property=LoadState", "--value", FEISHU_SERVICE], text=True).strip()
    if load_state == "not-found":
        return CORE_SERVICES, CORE_SERVICES
    if load_state != "loaded":
        raise RuntimeError("Feishu worker unit must be loaded or absent before upgrading")
    active = subprocess.run(["systemctl", "is-active", "--quiet", FEISHU_SERVICE]).returncode == 0
    # An installed but deliberately stopped worker stays stopped. All existing
    # workers are stopped before backup/migration, and rollback uses this same
    # pre-upgrade service inventory rather than guessing from the new release.
    return (*CORE_SERVICES, FEISHU_SERVICE), (*CORE_SERVICES, FEISHU_SERVICE) if active else CORE_SERVICES


def healthy(revision, services=CORE_SERVICES):
    for _ in range(30):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/api/v1/health/version", timeout=3) as response:
                version = json.load(response)
            with urllib.request.urlopen("http://127.0.0.1:8080/api/v1/health/ready", timeout=3) as response:
                assert response.status == 200
            assert version["revision"] == revision
            # systemctl with multiple units succeeds when *any* is active.
            for service in services:
                run(["systemctl", "is-active", "--quiet", service])
            return version
        except Exception:
            time.sleep(1)
    raise RuntimeError("New release health verification failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive", "wheels"):
        parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--" + name + "-sha256", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--expected-current", required=True)
    parser.add_argument("--expected-schema", default="V159", choices=("V125", "V151", "V152", "V153", "V154", "V155", "V156", "V157", "V158", "V159", "V160", "V161"))
    parser.add_argument("--target-schema", default="V399", choices=("V151", "V152", "V153", "V154", "V155", "V156", "V157", "V158", "V159", "V399"))
    parser.add_argument("--preflight-receipt", type=Path)
    args = parser.parse_args()
    if int(args.target_schema[1:]) < int(args.expected_schema[1:]):
        parser.error("Schema downgrade is not supported; target must be at least the current schema")
    assert os.geteuid() == 0 and socket.gethostname() == "salesbuddy"
    assert "172.22.9.234" in subprocess.check_output(["hostname", "-I"], text=True).split()
    # Public source/wheels/venv must be readable by the service OS user even
    # when a private root launcher inherited umask 077. Secrets switch below.
    os.umask(0o022)
    assert re.fullmatch("[a-f0-9]{40}", args.revision)
    root = Path("/opt/shenma-sales")
    old = (root / "current").resolve()
    assert (old / "REVISION").read_text().strip() == args.expected_current
    assert sql("shenma_sales", "SELECT max(version) FROM ops.schema_migration") == args.expected_schema
    for name in ("archive", "wheels"):
        assert hashlib.sha256(getattr(args, name).read_bytes()).hexdigest() == getattr(args, name + "_sha256")
    if args.target_schema == "V399":
        validate_preflight(args.preflight_receipt, args)
    new = root / "releases" / args.revision
    if args.target_schema != "V399":
        assert not new.exists()
    if not new.exists():
        new.mkdir(mode=0o755)
        unpack(args.archive, new, "SalesBuddy-Shenma-source-" + args.revision[:12] + "/")
    run(["sha256sum", "-c", "SHA256SUMS"], cwd=new, stdout=subprocess.DEVNULL)
    for file in [*(old / "database").glob("*.sql"), *(old / "database/migrations").glob("*.sql")]:
        assert file.read_bytes() == (new / file.relative_to(old)).read_bytes(), "Historical SQL changed"
    assert (new / "REVISION").read_text().strip() == args.revision
    assert json.loads((new / "frontend/project.config.json").read_text())["appid"] == "wx2824bdeb58528fd8"
    wheels = root / "dependencies" / args.wheels_sha256[:16]
    if not wheels.exists():
        wheels.mkdir(parents=True)
        unpack(args.wheels, wheels)
    run(["sha256sum", "-c", "SHA256SUMS"], cwd=wheels, stdout=subprocess.DEVNULL)
    uv = "/opt/shenma-tools/uv-x86_64-unknown-linux-gnu/uv"
    python = new / "backend/.venv/bin/python"
    if not python.exists():
        run([uv, "venv", "--python", str((old / "backend/.venv/bin/python").resolve()), str(python.parent.parent)])
    run([uv, "--no-cache", "pip", "install", "--python", str(python), "--no-index", "--find-links", str(wheels),
         "-r", str(new / "deployment/runtime-requirements.txt")])
    run([uv, "--no-cache", "pip", "install", "--python", str(python), "--no-index", "--find-links", str(wheels),
         "--no-deps", "-e", str(new / "backend")])
    shutil.copyfile(new / "REVISION", new / "backend/REVISION")
    run(["/usr/sbin/runuser", "-u", "shenma-sales", "--", str(python), "-c",
         "import sales_backend,pypinyin;from pathlib import Path;assert Path(sales_backend.__file__).resolve().is_relative_to(Path(" + repr(str(new)) + "))"], cwd=new)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path("/var/backups/shenma-sales") / (stamp + "-" + args.target_schema.lower())
    os.umask(0o077)
    backup.mkdir(parents=True, mode=0o700)
    restored = "shenma_restore_" + stamp.lower()
    failed = "shenma_failed_" + stamp.lower()
    restored_created = False
    migration_started = False
    stop_services, start_services = existing_services()
    stopped = False
    switched = False
    try:
        stopped = True
        run(["systemctl", "stop", *stop_services])
        with (backup / "database.dump").open("wb") as stream:
            run(["/usr/sbin/runuser", "-u", "postgres", "--", "pg_dump", "-Fc", "shenma_sales"], stdout=stream)
        run(["tar", "-czf", str(backup / "uploads.tar.gz"), "-C", "/var/lib/sales-backend", "."])
        run(["tar", "-czf", str(backup / "runtime-secrets.tar.gz"), "-C", "/etc", "shenma-sales"])
        if Path("/var/lib/shenma-model-key").is_dir():
            run(["tar", "-czf", str(backup / "unified-model-key.tar.gz"), "-C", "/var/lib", "shenma-model-key"])
        run(["tar", "-czf", str(backup / "provision-secrets.tar.gz"), "-C", "/var/lib", "shenma-provision"])
        (backup / "previous-release.txt").write_text(str(old) + "\n")
        run(["/usr/sbin/runuser", "-u", "postgres", "--", "createdb", "-T", "template0", restored])
        restored_created = True
        with (backup / "database.dump").open("rb") as stream:
            run(["/usr/sbin/runuser", "-u", "postgres", "--", "pg_restore", "--exit-on-error", "-d", restored], stdin=stream)
        tables = sql("shenma_sales", "SELECT quote_ident(schemaname)||'.'||quote_ident(tablename) FROM pg_tables WHERE schemaname IN ('platform','crm','activity','workflow','insight','ops','config','security','agent') ORDER BY 1").splitlines()
        for table in tables:
            assert sql("shenma_sales", f"SELECT count(*) FROM {table}") == sql(restored, f"SELECT count(*) FROM {table}"), table
        assert sql(restored, "SELECT max(version) FROM ops.schema_migration") == args.expected_schema
        (backup / "SHA256SUMS").write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n" for p in sorted(backup.iterdir()) if p.is_file()))
        migration_started = True
        with (backup / "migration.json").open("w") as stream:
            run(["/usr/sbin/runuser", "-u", "postgres", "--", "env", "DATABASE_URL=postgresql:///shenma_sales?host=/var/run/postgresql",
                 str(python), str(new / "database/scripts/migrate.py")], stdout=stream)
        assert sql("shenma_sales", "SELECT max(version) FROM ops.schema_migration") == args.target_schema
        # Fingerprint only columns that existed before this upgrade, retaining
        # identity hashes, ownership, authored content and customer connections.
        before = preserved_snapshot(restored)
        after = preserved_snapshot("shenma_sales", before["columns"])
        assert before == after, "Existing identity, business or customer configuration rows changed"
        with (backup / "migration-repeat.json").open("w") as stream:
            run(["/usr/sbin/runuser", "-u", "postgres", "--", "env", "DATABASE_URL=postgresql:///shenma_sales?host=/var/run/postgresql",
                 str(python), str(new / "database/scripts/migrate.py")], stdout=stream)
        assert all(item["status"] == "unchanged" for item in json.loads((backup / "migration-repeat.json").read_text()))
        sql("shenma_sales", "ANALYZE")
        switch(root, "current", new)
        switched = True
        # Do not consume a queue or send notifications until the new API and
        # migrated schema are confirmed healthy. A failed API can still roll
        # back before workers create irreversible external side effects.
        run(["systemctl", "start", "shenma-api"])
        healthy(args.revision, ("shenma-api",))
        workers = tuple(service for service in start_services if service != "shenma-api")
        if workers:
            run(["systemctl", "start", *workers])
        version = healthy(args.revision, start_services)
        switch(root, "previous", old)
        receipt = {"revision": args.revision, "previous": args.expected_current, "database": args.target_schema,
                   "backup": str(backup), "services": list(start_services), "restore_table_counts_verified": len(tables),
                   "existing_rows_unchanged": True, "migration_repeat_unchanged": True, "version": version}
        (backup / "upgrade.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt), flush=True)
    except BaseException:
        if stopped:
            run(["systemctl", "stop", *stop_services])
            if migration_started and restored_created:
                sql("postgres", "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='shenma_sales' AND pid<>pg_backend_pid()")
                sql("postgres", f'ALTER DATABASE shenma_sales RENAME TO "{failed}"')
                sql("postgres", f'ALTER DATABASE "{restored}" RENAME TO shenma_sales')
                restored_created = False
            if switched:
                switch(root, "current", old)
            run(["systemctl", "start", *start_services])
            healthy(args.expected_current, start_services)
        raise
    finally:
        if restored_created:
            run(["/usr/sbin/runuser", "-u", "postgres", "--", "dropdb", restored])


if __name__ == "__main__":
    main()
