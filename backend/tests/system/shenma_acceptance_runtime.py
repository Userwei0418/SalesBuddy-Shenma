"""Start/inspect/stop this Mac's isolated Shenma acceptance stack.

An explicit state directory owns the immutable source snapshot, private secrets,
PostgreSQL cluster and Python environment. This never connects to a supplied DSN
or an existing customer database. Run setup with SHENMA_ACCEPTANCE_PASSWORD set.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[3]
MARKER = "shenma-isolated-acceptance-v1"
DB = "shenma_acceptance_20261015"
ROLE = "shenma_acceptance_runtime"
PGPORT = 55465
PORT = 18920
# PostgreSQL needs a short local socket path; pg_start checks ownership and mode.
SOCKET = Path("/tmp/shenma-acceptance-20261015")  # noqa: S108
SOURCE_MANIFEST = "source-manifest.json"
BUSINESS_SCHEMAS = (
    "platform",
    "crm",
    "activity",
    "workflow",
    "insight",
    "ops",
    "config",
    "common",
    "security",
    "agent",
)


def run(*args, **kwargs):
    # Internal callers supply executable/argv lists; no shell command is evaluated.
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)  # noqa: S603


def guard(state: Path):
    if state.is_symlink() or not state.is_absolute():
        raise ValueError("Use an absolute, non-symlink state directory")
    if not state.is_dir():
        raise ValueError("Acceptance state directory does not exist")
    if state.stat().st_uid != os.getuid():
        raise ValueError("Acceptance state directory owner mismatch")
    root = state.resolve()
    for name in (
        "marker",
        "source",
        "pgdata",
        "private.json",
        "venv",
        "imports",
        "api.pid",
        "api.log",
        "postgres.log",
        "source-revision.txt",
        "runtime-startup-check.json",
        SOURCE_MANIFEST,
    ):
        path = state / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Acceptance state path must stay inside its directory: {name}")
    marker = state / "marker"
    if not marker.exists() or marker.read_text().strip() != MARKER:
        raise ValueError("Unrecognized acceptance state directory")
    private = state / "private.json"
    if private.exists() and (private.stat().st_uid != os.getuid() or private.stat().st_mode & 0o077):
        raise ValueError("Acceptance private configuration must be owned by this user with mode 0600")


def guard_socket():
    if SOCKET.is_symlink():
        raise ValueError("Acceptance socket directory must not be a symlink")
    if SOCKET.exists() and (not SOCKET.is_dir() or SOCKET.stat().st_uid != os.getuid()):
        raise ValueError("Socket directory owner or type mismatch")


def source_manifest(state: Path) -> dict:
    """Hash only the copied source/artifact tree, never private configuration or PG data."""
    guard(state)
    source = state / "source"
    if not source.is_dir():
        raise ValueError("Acceptance source snapshot is missing")
    for relative in (
        "backend/src/sales_backend/main.py",
        "backend/tests/system/shenma_acceptance_runtime.py",
        "database/scripts/migrate.py",
        "business-web/dist/index.html",
        "business-web/dist/build.json",
    ):
        if not (source / relative).is_file():
            raise ValueError(f"Acceptance source snapshot is incomplete: {relative}")
    files = {}
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("Acceptance source snapshot must not contain symlinks")
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        files[path.relative_to(source).as_posix()] = {"sha256": digest, "bytes": path.stat().st_size}
    build_path = source / "business-web/dist/build.json"
    build = json.loads(build_path.read_text()) if build_path.exists() else {}
    return {
        "schema_version": 1,
        "source_revision": (state / "source-revision.txt").read_text().strip(),
        "web_build_revision": build.get("revision"),
        "files": files,
    }


def record_source_manifest(state: Path):
    manifest = source_manifest(state)
    (state / SOURCE_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {key: value for key, value in manifest.items() if key != "files"} | {"file_count": len(manifest["files"])}


def verify_source_manifest(state: Path):
    path = state / SOURCE_MANIFEST
    if not path.is_file():
        raise ValueError("Source manifest is missing; review the snapshot and run the snapshot command first")
    if json.loads(path.read_text()) != source_manifest(state):
        raise ValueError("Acceptance source differs from its recorded SHA256 manifest; review before restarting")


def pg_start(state: Path):
    guard(state)
    guard_socket()
    SOCKET.mkdir(mode=0o700, exist_ok=True)
    if SOCKET.stat().st_uid != os.getuid():
        raise ValueError("Socket directory owner mismatch")
    SOCKET.chmod(0o700)
    # Local PostgreSQL binary from the operator's PATH; state is one argv value.
    status = subprocess.run(  # noqa: S603
        ["pg_ctl", "-D", str(state / "pgdata"), "status"],  # noqa: S607
        capture_output=True,
    )
    if status.returncode:
        run("pg_ctl", "-D", state / "pgdata", "-l", state / "postgres.log", "-w", "start")


async def verify_runtime_role(state: Path, dsn: str) -> dict:
    """Connect as the actual API login; no maintenance connection or SET ROLE shortcut."""
    import asyncpg

    guard(state)
    guard_socket()
    connection = await asyncpg.connect(dsn, timeout=5, command_timeout=5)
    try:
        flags = dict(
            await connection.fetchrow("""SELECT current_user AS runtime_user,
            session_user AS session_user,current_database() AS database,
            current_setting('row_security') AS row_security,
            rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolcanlogin
            FROM pg_roles WHERE rolname=current_user""")
        )
        if (
            flags["runtime_user"] != ROLE
            or flags["session_user"] != ROLE
            or flags["database"] != DB
            or flags["row_security"] != "on"
            or not flags["rolcanlogin"]
            or any(flags[name] for name in ("rolsuper", "rolbypassrls", "rolcreatedb", "rolcreaterole"))
        ):
            raise ValueError("Acceptance API runtime identity or RLS configuration is unsafe")
        marker = await connection.fetchval("""SELECT shobj_description(oid,'pg_database')
            FROM pg_database WHERE datname=current_database()""")
        if marker != MARKER:
            raise ValueError("Acceptance runtime database marker mismatch")
        if await connection.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_auth_members
            WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user))"""):
            raise ValueError("Acceptance runtime must not inherit or be able to assume another database role")
        if await connection.fetchval(
            """SELECT EXISTS(SELECT 1 FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_roles r ON r.oid=c.relowner
            WHERE r.rolname=current_user AND n.nspname=ANY($1::text[]))""",
            list(BUSINESS_SCHEMAS),
        ):
            raise ValueError("Acceptance runtime must not own business tables or views")
        for table in ("platform.password_credential", "security.login_throttle", "security.feishu_credential"):
            if await connection.fetchval(
                "SELECT has_table_privilege(current_user,$1,'SELECT,INSERT,UPDATE,DELETE')", table
            ):
                raise ValueError("Acceptance runtime has direct access to a private credential table")
        for signature in (
            "security.reconcile_runtime_grants()",
            "security.authorization_grants_for(uuid,uuid)",
            "security.authorization_user_allows(text,uuid,uuid,uuid,uuid[],boolean)",
            "ops.feishu_source(uuid,text,uuid)",
            "ops.feishu_weekly_source(uuid,uuid)",
            "ops.feishu_reconcile(uuid)",
        ):
            if await connection.fetchval("SELECT has_function_privilege(current_user,$1,'EXECUTE')", signature):
                raise ValueError("Acceptance runtime has a maintenance or sync-worker-only function grant")
        if await connection.fetchval("SELECT count(*) FROM crm.customer"):
            raise ValueError("Acceptance runtime can see customers without a request authorization context")
        return {**flags, "database_marker_verified": True, "private_acl_verified": True, "unscoped_customer_count": 0}
    finally:
        await connection.close()


async def initialize(state: Path, password: str):
    import asyncpg

    sys.path[:0] = [
        str(state / "source/database/scripts"),
        str(state / "source/backend/src"),
        str(state / "source/backend/tests/system"),
    ]
    from migrate import migrate
    from shenma_acceptance_seed import seed_acceptance

    config = json.loads((state / "private.json").read_text())
    maintenance = dict(host=str(SOCKET), port=PGPORT, user=getpass.getuser())
    admin = await asyncpg.connect(database="postgres", **maintenance)
    try:
        actual_directory = Path(await admin.fetchval("SHOW data_directory")).resolve()
        if actual_directory != (state / "pgdata").resolve():
            raise ValueError("Connected PostgreSQL cluster does not belong to this acceptance state")
        if int(await admin.fetchval("SHOW port")) != PGPORT or await admin.fetchval("SHOW listen_addresses"):
            raise ValueError("Acceptance PostgreSQL must use its reserved port and Unix socket only")
        if not await admin.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", DB):
            await admin.execute(f"CREATE DATABASE {DB} TEMPLATE template0 ENCODING 'UTF8'")
            await admin.execute(f"COMMENT ON DATABASE {DB} IS '{MARKER}'")
        if not await admin.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", ROLE):
            # Secret is locally generated hex; never accepted from shell/remote input.
            await admin.execute(
                f"CREATE ROLE {ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                f"NOINHERIT NOBYPASSRLS PASSWORD '{config['db_password']}'"
            )
    finally:
        await admin.close()
    conn = await asyncpg.connect(database=DB, **maintenance)
    try:
        if (
            await conn.fetchval(
                "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()"
            )
            != MARKER
        ):
            raise ValueError("Acceptance database marker mismatch")
        migrations = await migrate(conn)
        for schema in (
            "platform",
            "crm",
            "activity",
            "workflow",
            "insight",
            "ops",
            "config",
            "common",
            "security",
            "agent",
        ):
            await conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO {ROLE}")
            await conn.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {schema} TO {ROLE}")
            await conn.execute(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA {schema} TO {ROLE}")
            await conn.execute(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO {ROLE}")
        await conn.execute(f"REVOKE INSERT,UPDATE,DELETE ON crm.customer_sales_member FROM {ROLE}")
        await conn.execute(f"REVOKE ALL ON platform.password_credential,security.login_throttle FROM {ROLE}")
        await conn.fetchval("SELECT security.reconcile_runtime_grants()")
        await conn.execute(f"REVOKE ALL ON security.feishu_credential FROM {ROLE}")
        for signature in (
            "ops.feishu_source(uuid,text,uuid)",
            "ops.feishu_weekly_source(uuid,uuid)",
            "ops.feishu_reconcile(uuid)",
        ):
            if await conn.fetchval("SELECT to_regprocedure($1) IS NOT NULL", signature):
                await conn.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM {ROLE}")
        result = await seed_acceptance(conn, password=password)
        result["migrations"] = migrations
        (state / "seed-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await conn.close()
    runtime = await asyncpg.connect(
        database=DB, host=str(SOCKET), port=PGPORT, user=ROLE, password=config["db_password"]
    )
    try:
        flags = dict(
            await runtime.fetchrow(
                "SELECT current_user AS runtime_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
        )
        assert not flags["rolsuper"] and not flags["rolbypassrls"]
        assert not await runtime.fetchval(
            "SELECT has_table_privilege(current_user,'platform.password_credential','SELECT')"
        )
        assert not await runtime.fetchval(
            "SELECT has_table_privilege(current_user,'security.feishu_credential','SELECT')"
        )
        assert not await runtime.fetchval(
            "SELECT has_function_privilege(current_user,'security.reconcile_runtime_grants()','EXECUTE')"
        )
        assert await runtime.fetchval("SELECT count(*) FROM crm.customer") == 0
        (state / "runtime-role-check.json").write_text(
            json.dumps(
                {**flags, "credential_read_denied": True, "grant_reconcile_denied": True, "unscoped_customer_count": 0},
                indent=2,
            )
        )
    finally:
        await runtime.close()
    print(json.dumps({"database": DB, "accounts": [a["login"] for a in result["accounts"]], "role_checks": flags}))


def setup(state: Path):
    # Do not leave the seed password in PostgreSQL or dependency subprocess environments.
    password = os.environ.pop("SHENMA_ACCEPTANCE_PASSWORD", None)
    if not password:
        raise ValueError("SHENMA_ACCEPTANCE_PASSWORD is required for setup")
    if state.exists():
        guard(state)
    else:
        state.mkdir(parents=True, mode=0o700)
        (state / "marker").write_text(MARKER)
    state.chmod(0o700)
    if not (state / "source").exists():
        for relative in ("backend/src", "database", "business-web/dist"):
            shutil.copytree(
                ROOT / relative,
                state / "source" / relative,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
            )
        destination = state / "source/backend/tests/system"
        destination.mkdir(parents=True)
        shutil.copy2(Path(__file__), destination / Path(__file__).name)
        shutil.copy2(Path(__file__).with_name("shenma_acceptance_seed.py"), destination / "shenma_acceptance_seed.py")
        # Fixed read-only Git argv against this repository, without shell expansion.
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=ROOT,
            text=True,
        ).strip()
        (state / "source-revision.txt").write_text(revision + "\n")
        (state / "source/REVISION").write_text(revision + "\n")
        record_source_manifest(state)
    else:
        verify_source_manifest(state)
    if not (state / "venv").exists():
        source_python = os.environ.get("SHENMA_ACCEPTANCE_SOURCE_PYTHON", sys.executable)
        # Operator-selected local Python is passed as one --python argument, not code.
        packages = subprocess.check_output(  # noqa: S603
            ["uv", "pip", "freeze", "--python", source_python],  # noqa: S607
            text=True,
        )
        (state / "requirements.txt").write_text(packages)
        run("uv", "venv", "--python", source_python, state / "venv")
        run("uv", "pip", "install", "--python", state / "venv/bin/python", "-r", state / "requirements.txt")
    if not (state / "private.json").exists():
        path = state / "private.json"
        with path.open("x") as handle:
            path.chmod(0o600)
            json.dump({"db_password": secrets.token_hex(32), "jwt_secret": secrets.token_hex(48)}, handle)
    if not (state / "pgdata").exists():
        run(
            "initdb",
            "-D",
            state / "pgdata",
            "--encoding=UTF8",
            "--locale=C",
            "--auth-local=peer",
            "--auth-host=scram-sha-256",
        )
        with (state / "pgdata/postgresql.conf").open("a") as handle:
            handle.write(f"\nlisten_addresses = ''\nport = {PGPORT}\nunix_socket_directories = '{SOCKET}'\n")
        (state / "pgdata/pg_hba.conf").write_text(
            f"local all {getpass.getuser()} peer\nlocal {DB} {ROLE} scram-sha-256\nlocal all all reject\n"
        )
    pg_start(state)
    asyncio.run(initialize(state, password))


def serve(state: Path):
    guard(state)
    guard_socket()
    verify_source_manifest(state)
    config = json.loads((state / "private.json").read_text())
    imports = state / "imports"
    imports.mkdir(mode=0o700, exist_ok=True)
    # No inherited customer credentials, model keys or Feishu bindings.
    keep = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TMPDIR") if key in os.environ}
    os.environ.clear()
    os.environ.update(keep)
    os.environ.update(
        {
            "APP_ENV": "acceptance",
            "AUTH_MODE": "password",
            "DEMO_WORKSPACE": "shenma-acceptance",
            "DATABASE_URL": f"postgresql://{ROLE}:{config['db_password']}@/{DB}?host={quote(str(SOCKET))}&port={PGPORT}",
            "ACCESS_TOKEN_SECRET": config["jwt_secret"],
            "ACCESS_TOKEN_ISSUER": "shenma-isolated-acceptance",
            "ACCESS_TOKEN_AUDIENCE": "shenma-isolated-web-mini",
            "MODEL_CONNECTIONS_LOCKED": "true",
            "VISIT_IMPORT_ROOT": str(imports),
            "PYTHONDONTWRITEBYTECODE": "1",
            "SALES_RELEASE_REVISION": (state / "source-revision.txt").read_text().strip(),
        }
    )

    # Python audit defense, not an OS sandbox: deny DNS and outbound connections
    # except the exact local PostgreSQL socket; no application Worker is launched.
    def deny_network(event, args):
        if event == "socket.getaddrinfo":
            raise OSError("External networking is disabled in isolated acceptance")
        if event == "socket.connect":
            sock, address = args
            if (
                sock.family != socket.AF_UNIX
                or Path(os.fsdecode(address)).resolve() != (SOCKET / f".s.PGSQL.{PGPORT}").resolve()
            ):
                raise OSError("Outbound connection is disabled in isolated acceptance")
        if event in {"socket.sendto", "socket.sendmsg"}:
            raise OSError("Outbound datagrams are disabled in isolated acceptance")

    sys.addaudithook(deny_network)
    role_check = asyncio.run(verify_runtime_role(state, os.environ["DATABASE_URL"]))
    (state / "runtime-startup-check.json").write_text(json.dumps(role_check, indent=2) + "\n")
    sys.path.insert(0, str(state / "source/backend/src"))
    import uvicorn
    from fastapi import Response
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from sales_backend.main import app, health_ready

    async def isolated_connection_status(request):
        # Readiness retains a truthful model-disabled 503. The login badge reports
        # whether this local business API is usable, with the limitation explicit.
        health = await health_ready(Response())
        checks = health["checks"]
        return JSONResponse(
            {
                "configured": True,
                "reachable": bool(checks["database"] and checks["auth_configured"]),
                "checks": checks,
                "environment": "本机隔离验收 · AI/飞书关闭",
                "label": "本机隔离验收 · AI/飞书关闭",
                "apiPrefix": "/api/v1",
                "acceptanceMarker": MARKER,
                "sourceRevision": os.environ["SALES_RELEASE_REVISION"],
                "localQuickLogin": {"available": False},
            }
        )

    app.router.routes.insert(0, Route("/connection-status", isolated_connection_status, methods=["GET"]))

    uvicorn.run(app, host="127.0.0.1", port=PORT, loop="asyncio", access_log=False, proxy_headers=False)


def local_api_status() -> dict:
    """Read only fixed loopback URLs, without inherited HTTP proxy settings."""
    opener = build_opener(ProxyHandler({}))
    report = {"live": False, "business_ready": False, "acceptance_identity": False}
    try:
        with opener.open(f"http://127.0.0.1:{PORT}/api/v1/health/live", timeout=1) as response:
            report["live"] = response.status == 200 and json.load(response).get("status") == "ok"
        with opener.open(f"http://127.0.0.1:{PORT}/connection-status", timeout=1) as response:
            connection = json.load(response)
        report["acceptance_identity"] = connection.get("acceptanceMarker") == MARKER
        report["business_ready"] = bool(connection.get("reachable")) and report["acceptance_identity"]
        report["checks"] = connection.get("checks", {})
        report["source_revision"] = connection.get("sourceRevision")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        report["error"] = "Local acceptance API is not responding with a valid status"
    return report


def wait_for_start(process, state: Path) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Acceptance API exited during startup; inspect the local api.log")
        report = local_api_status()
        if report["live"] and report["business_ready"]:
            if report.get("source_revision") != (state / "source-revision.txt").read_text().strip():
                raise RuntimeError("Listening acceptance API has an unexpected source revision")
            return report
        time.sleep(0.2)
    raise RuntimeError("Acceptance API did not become usable within 10 seconds; inspect the local api.log")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("setup", "snapshot", "serve", "start", "status", "stop"))
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()
    state = args.state
    if args.command == "setup":
        setup(state)
        return
    guard(state)
    if args.command == "snapshot":
        print(json.dumps(record_source_manifest(state), ensure_ascii=False))
    elif args.command == "serve":
        serve(state)
    elif args.command == "start":
        verify_source_manifest(state)
        pg_start(state)
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", PORT)) == 0:
                raise RuntimeError(f"Port {PORT} already occupied; inspect status before starting")
        with (state / "api.log").open("ab") as log:
            # Execute only this launcher's local state Python and source file, no shell.
            process = subprocess.Popen(  # noqa: S603
                [
                    str(state / "venv/bin/python"),
                    str(state / "source/backend/tests/system/shenma_acceptance_runtime.py"),
                    "serve",
                    "--state",
                    str(state),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                env={key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TMPDIR") if key in os.environ},
            )
        (state / "api.pid").write_text(str(process.pid))
        try:
            report = wait_for_start(process, state)
        except Exception:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            (state / "api.pid").unlink(missing_ok=True)
            raise
        print(
            json.dumps(
                {
                    "pid": process.pid,
                    "url": f"http://127.0.0.1:{PORT}/workspace/?mode=live",
                    "python_outbound_connections": "local PostgreSQL socket only",
                    **report,
                }
            )
        )
    elif args.command == "status":
        run("pg_ctl", "-D", state / "pgdata", "status")
        print("API PID:", (state / "api.pid").read_text() if (state / "api.pid").exists() else "not started")
        print(json.dumps(local_api_status(), ensure_ascii=False))
    else:
        pid_file = state / "api.pid"
        if pid_file.exists():
            pid = int(pid_file.read_text())
            # Fixed local ps command; stored PID is parsed as an integer above.
            command = subprocess.run(  # noqa: S603
                ["ps", "-p", str(pid), "-o", "command="],  # noqa: S607
                capture_output=True,
                text=True,
            ).stdout
            if str(state) not in command or "shenma_acceptance_runtime.py serve" not in command:
                raise RuntimeError("Stored API PID is stale; refusing to stop an unrelated process")
            os.kill(pid, 15)
            pid_file.unlink()
        run("pg_ctl", "-D", state / "pgdata", "-m", "fast", "-w", "stop")


if __name__ == "__main__":
    main()
