"""Run S2 HTTP and existing customer regressions in disposable local PostgreSQL.

Set PGHOST/PGPORT/PGUSER for a local maintenance connection; the shared runner
creates and removes a fresh database and NOSUPERUSER/NOBYPASSRLS runtime role.
Only synthetic fixtures are used. No application server or external service is
started: HTTP tests exercise the real ASGI routes and PostgreSQL transactions.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import signal
import sys
from urllib.parse import quote, urlencode

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "backend/tests/system"), str(ROOT / "backend/src")]
from run_integration_postgres import main


TARGETS = (
    "test_customer_creation_requests_api.py",
    "test_operations_claims_sql.py",
    "test_authorization_customers.py",
    "test_authorization_api.py",
    "test_customer_claim_pool.py",
    "test_claim_concurrency.py",
    "test_customer_profile_management.py",
    "test_console_session_binding.py",
)
PYTEST_TIMEOUT_SECONDS = 180


async def stop_process(process: asyncio.subprocess.Process) -> None:
    """Reap only this runner's child process group before dropping its database."""
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def check(config: dict, name: str, role: str) -> None:
    assert re.fullmatch(r"salegent_verify_integration_[a-f0-9]+", name), name
    assert re.fullmatch(r"salegent_verify_role_[a-f0-9]+", role), role
    connection = await asyncpg.connect(database=name, **config, command_timeout=15)
    try:
        head = await connection.fetchval(
            "SELECT version FROM ops.schema_migration "
            "ORDER BY substring(version from '[0-9]+')::integer DESC LIMIT 1"
        )
        async with connection.transaction():
            await connection.execute(f'SET LOCAL ROLE "{role}"')
            await connection.execute("SET LOCAL row_security = on")
            assert await connection.fetchval("SELECT current_database()") == name
            assert await connection.fetchval("SELECT current_user") == role
            assert not await connection.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
        print(f"S2_API_ISOLATION database={name} role={role} schema={head}", flush=True)
    finally:
        await connection.close()

    query = urlencode({"host": config["host"], "port": config["port"]})
    dsn = f"postgresql://{quote(config['user'], safe='')}@/{name}?{query}"
    env = {
        **os.environ,
        "SALES_TEST_DATABASE_URL": dsn,
        "SALES_TEST_ROLE": role,
        "SALES_TEST_WORKSPACE": "demo-sales-workspace",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(ROOT / "backend/src"),
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "-o", "faulthandler_timeout=45",
        *(str(ROOT / "backend/tests/integration" / target) for target in TARGETS),
        cwd=str(ROOT / "backend"), env=env, start_new_session=True,
    )
    try:
        try:
            code = await asyncio.wait_for(process.wait(), timeout=PYTEST_TIMEOUT_SECONDS)
        except TimeoutError as error:
            raise RuntimeError(f"S2 HTTP regression exceeded {PYTEST_TIMEOUT_SECONDS}s") from error
        if code:
            raise RuntimeError(f"S2 HTTP regression failed: pytest exit {code}")
        print("S2_API_OK selected HTTP/customer regressions passed", flush=True)
    finally:
        await stop_process(process)


async def verify() -> None:
    host = os.environ.get("PGHOST", "/tmp")
    if host not in {"localhost", "127.0.0.1", "::1"} and not host.startswith(("/tmp/", "/private/tmp/")) and host not in {"/tmp", "/private/tmp"}:
        raise RuntimeError("S2 HTTP verification accepts only local PostgreSQL host/socket")
    await main(serve=check)


if __name__ == "__main__":
    asyncio.run(verify())
