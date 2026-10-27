# ruff: noqa: S108 -- Explicit local PostgreSQL Unix socket allowlist, not temporary credentials.
"""Run VISIT_RANGE HTTP and existing opportunity/authorization/VISIT_RANGE regressions in disposable local PostgreSQL.

Set PGHOST/PGPORT/PGUSER for a local maintenance connection; the shared runner
creates and removes a fresh database and NOSUPERUSER/NOBYPASSRLS runtime role.
Only synthetic fixtures are used. No application server or external service is
started: HTTP tests exercise the real ASGI routes and PostgreSQL transactions.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "backend/tests/system"), str(ROOT / "backend/src")]
from run_integration_postgres import main  # noqa: E402 -- Local runner path set above.

TARGETS = (
    "test_visit_range_api.py",
    "test_visit_history_cursor.py",
    "test_history_read_projection.py",
    "test_authorization_visits.py",
    "test_synthetic_trial_boundaries.py",
    "test_opportunity_views_api.py",
    "test_customer_creation_requests_api.py",
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
            "SELECT version FROM ops.schema_migration ORDER BY substring(version from '[0-9]+')::integer DESC LIMIT 1"
        )
        async with connection.transaction():
            await connection.execute(f'SET LOCAL ROLE "{role}"')
            await connection.execute("SET LOCAL row_security = on")
            assert await connection.fetchval("SELECT current_database()") == name
            assert await connection.fetchval("SELECT current_user") == role
            assert not await connection.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
        print(f"VISIT_RANGE_API_ISOLATION database={name} role={role} schema={head}", flush=True)
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
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-o",
        "faulthandler_timeout=45",
        *(str(ROOT / "backend/tests/integration" / target) for target in TARGETS),
        cwd=str(ROOT / "backend"),
        env=env,
        start_new_session=True,
    )
    try:
        try:
            code = await asyncio.wait_for(process.wait(), timeout=PYTEST_TIMEOUT_SECONDS)
        except TimeoutError as error:
            raise RuntimeError(f"VISIT_RANGE HTTP regression exceeded {PYTEST_TIMEOUT_SECONDS}s") from error
        if code:
            raise RuntimeError(f"VISIT_RANGE HTTP regression failed: pytest exit {code}")
        print("VISIT_RANGE_API_OK selected HTTP/opportunity/authorization/S2 regressions passed", flush=True)
    finally:
        await stop_process(process)


async def verify() -> None:
    host = os.environ.get("PGHOST", "/tmp")
    if (
        host not in {"localhost", "127.0.0.1", "::1"}
        and not host.startswith(("/tmp/", "/private/tmp/"))
        and host not in {"/tmp", "/private/tmp"}
    ):
        raise RuntimeError("VISIT_RANGE HTTP verification accepts only local PostgreSQL host/socket")
    await main(serve=check)


if __name__ == "__main__":
    asyncio.run(verify())
