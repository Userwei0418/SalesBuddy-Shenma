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
    return subprocess.check_output(["sudo", "-u", "postgres", "psql", "-X", "-v", "ON_ERROR_STOP=1",
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


def healthy(revision):
    for _ in range(30):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/api/v1/health/version", timeout=3) as response:
                version = json.load(response)
            with urllib.request.urlopen("http://127.0.0.1:8080/api/v1/health/ready", timeout=3) as response:
                assert response.status == 200
            assert version["revision"] == revision
            run(["systemctl", "is-active", "--quiet", "shenma-api", "shenma-worker"])
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
    parser.add_argument("--expected-schema", default="V125", choices=("V125", "V151", "V152"))
    parser.add_argument("--target-schema", default="V153", choices=("V151", "V152", "V153"))
    args = parser.parse_args()
    assert os.geteuid() == 0 and socket.gethostname() == "salesbuddy"
    assert "172.22.9.234" in subprocess.check_output(["hostname", "-I"], text=True).split()
    assert re.fullmatch("[a-f0-9]{40}", args.revision)
    root = Path("/opt/shenma-sales")
    old = (root / "current").resolve()
    assert (old / "REVISION").read_text().strip() == args.expected_current
    assert sql("shenma_sales", "SELECT max(version) FROM ops.schema_migration") == args.expected_schema
    for name in ("archive", "wheels"):
        assert hashlib.sha256(getattr(args, name).read_bytes()).hexdigest() == getattr(args, name + "_sha256")
    new = root / "releases" / args.revision
    assert not new.exists()
    new.mkdir(mode=0o755)
    unpack(args.archive, new, "SalesBuddy-Shenma-source-" + args.revision[:12] + "/")
    run(["sha256sum", "-c", "SHA256SUMS"], cwd=new, stdout=subprocess.DEVNULL)
    for file in (old / "database").rglob("*.sql"):
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
    run([uv, "venv", "--python", str((old / "backend/.venv/bin/python").resolve()), str(python.parent.parent)])
    run([uv, "pip", "install", "--python", str(python), "--no-index", "--find-links", str(wheels),
         "-r", str(new / "deployment/runtime-requirements.txt")])
    run([uv, "pip", "install", "--python", str(python), "--no-index", "--find-links", str(wheels),
         "--no-deps", "-e", str(new / "backend")])
    shutil.copyfile(new / "REVISION", new / "backend/REVISION")
    run(["sudo", "-u", "shenma-sales", str(python), "-c",
         "import sales_backend,pypinyin;from pathlib import Path;assert Path(sales_backend.__file__).resolve().is_relative_to(Path(" + repr(str(new)) + "))"], cwd=new)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path("/var/backups/shenma-sales") / (stamp + "-" + args.target_schema.lower())
    backup.mkdir(parents=True, mode=0o700)
    os.umask(0o077)
    restored = "shenma_restore_" + stamp.lower()
    failed = "shenma_failed_" + stamp.lower()
    restored_created = False
    migration_started = False
    stopped = False
    switched = False
    try:
        run(["systemctl", "stop", "shenma-api", "shenma-worker"])
        stopped = True
        with (backup / "database.dump").open("wb") as stream:
            run(["sudo", "-u", "postgres", "pg_dump", "-Fc", "shenma_sales"], stdout=stream)
        run(["tar", "-czf", str(backup / "uploads.tar.gz"), "-C", "/var/lib/sales-backend", "."])
        run(["tar", "-czf", str(backup / "runtime-secrets.tar.gz"), "-C", "/etc", "shenma-sales"])
        run(["tar", "-czf", str(backup / "provision-secrets.tar.gz"), "-C", "/var/lib", "shenma-provision"])
        (backup / "previous-release.txt").write_text(str(old) + "\n")
        run(["sudo", "-u", "postgres", "createdb", "-T", "template0", restored])
        restored_created = True
        with (backup / "database.dump").open("rb") as stream:
            run(["sudo", "-u", "postgres", "pg_restore", "--exit-on-error", "-d", restored], stdin=stream)
        tables = sql("shenma_sales", "SELECT quote_ident(schemaname)||'.'||quote_ident(tablename) FROM pg_tables WHERE schemaname IN ('platform','crm','activity','workflow','insight','ops','config','security','agent') ORDER BY 1").splitlines()
        for table in tables:
            assert sql("shenma_sales", f"SELECT count(*) FROM {table}") == sql(restored, f"SELECT count(*) FROM {table}"), table
        assert sql(restored, "SELECT max(version) FROM ops.schema_migration") == args.expected_schema
        (backup / "SHA256SUMS").write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n" for p in sorted(backup.iterdir()) if p.is_file()))
        migration_started = True
        with (backup / "migration.json").open("w") as stream:
            run(["sudo", "-u", "postgres", "env", "DATABASE_URL=postgresql:///shenma_sales?host=/var/run/postgresql",
                 str(python), str(new / "database/scripts/migrate.py")], stdout=stream)
        assert sql("shenma_sales", "SELECT max(version) FROM ops.schema_migration") == args.target_schema
        # Identity/password/appointment/ownership rows must be exactly preserved.
        for table in ("platform.user_ref", "platform.password_credential", "platform.role_binding", "platform.team_membership", "crm.customer", "crm.customer_ownership"):
            query = f"SELECT md5(coalesce(string_agg(to_jsonb(t)::text, E'\\n' ORDER BY to_jsonb(t)::text),'')) FROM {table} t"
            assert sql("shenma_sales", query) == sql(restored, query), "Existing rows changed: " + table
        with (backup / "migration-repeat.json").open("w") as stream:
            run(["sudo", "-u", "postgres", "env", "DATABASE_URL=postgresql:///shenma_sales?host=/var/run/postgresql",
                 str(python), str(new / "database/scripts/migrate.py")], stdout=stream)
        assert all(item["status"] == "unchanged" for item in json.loads((backup / "migration-repeat.json").read_text()))
        switch(root, "current", new)
        switched = True
        run(["systemctl", "start", "shenma-api", "shenma-worker"])
        version = healthy(args.revision)
        switch(root, "previous", old)
        receipt = {"revision": args.revision, "previous": args.expected_current, "database": args.target_schema,
                   "backup": str(backup), "restore_table_counts_verified": len(tables),
                   "existing_rows_unchanged": True, "migration_repeat_unchanged": True, "version": version}
        (backup / "upgrade.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt), flush=True)
    except BaseException:
        if stopped:
            run(["systemctl", "stop", "shenma-api", "shenma-worker"])
            if migration_started and restored_created:
                sql("postgres", "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='shenma_sales' AND pid<>pg_backend_pid()")
                sql("postgres", f'ALTER DATABASE shenma_sales RENAME TO "{failed}"')
                sql("postgres", f'ALTER DATABASE "{restored}" RENAME TO shenma_sales')
                restored_created = False
            if switched:
                switch(root, "current", old)
            run(["systemctl", "start", "shenma-api", "shenma-worker"])
            healthy(args.expected_current)
        raise
    finally:
        if restored_created:
            run(["sudo", "-u", "postgres", "dropdb", restored])


if __name__ == "__main__":
    main()
