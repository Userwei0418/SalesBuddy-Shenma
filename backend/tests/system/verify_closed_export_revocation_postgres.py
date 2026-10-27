"""Committed export/revocation concurrency in a separately owned throwaway DB.

The repeatable-read snapshot, logout commit and fresh rejection all use real
PostgreSQL connections. Immutable receipts survive until the runner drops this
entire database; no rows leak into the shared rollback-only integration suite.
"""
# ruff: noqa: E402 -- Standalone acceptance loads repository test helpers explicitly.
import asyncio
import json
import os
from pathlib import Path
import re
import sys

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/src")]
from run_integration_postgres import main as isolated_database
from tests.integration.test_closed_company_export_sql import assert_restricted, call, issue, seed_company


async def verify(config, name, role, report):
    assert re.fullmatch(r"salegent_verify_integration_[a-f0-9]{12}", name)
    assert re.fullmatch(r"salegent_verify_role_[a-f0-9]{12}", role)
    report.update(database=name, role=role)
    reader = writer = None
    try:
        reader = await asyncpg.connect(database=name, **config)
        writer = await asyncpg.connect(database=name, **config)
        for client in (reader, writer):
            assert await client.fetchval("SELECT current_database()") == name
            assert int(await client.fetchval("SHOW port")) == 5432
            assert await client.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=session_user")
            for type_name in ("json", "jsonb"):
                await client.set_type_codec(
                    type_name, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text"
                )
            await client.execute('SET ROLE "' + role + '"')
            await assert_restricted(client)
        async with writer.transaction():
            company = await seed_company(writer)
            token, _ = await issue(writer, company)
        snapshot_ready = asyncio.Event()
        logout_committed = asyncio.Event()

        async def export_reader():
            async with reader.transaction(isolation="repeatable_read", readonly=True):
                assert (await call(reader, "rows", token, "crm.customer", 0, 100))["items"]
                snapshot_ready.set()
                await logout_committed.wait()
                # An old snapshot is not sufficient authority to deliver a ZIP.
                assert (await call(reader, "status", token))["workspace_id"] == str(company["workspace"])
            for operation in ("status", "chunk"):
                try:
                    async with reader.transaction(readonly=True):
                        if operation == "status":
                            await call(reader, "status", token)
                        else:
                            await reader.fetch(
                                "SELECT * FROM security.closed_export_content_chunk($1,$2,0)", token, company["import"]
                            )
                except asyncpg.PostgresError as exc:
                    assert exc.sqlstate == "P7401" and str(exc) == "CLOSED_EXPORT_AUTH_REQUIRED"
                else:
                    raise AssertionError("Fresh authorization accepted a logged-out export session")

        async def logout_writer():
            await snapshot_ready.wait()
            async with writer.transaction():
                await call(writer, "logout", token)
            logout_committed.set()

        async with asyncio.timeout(10), asyncio.TaskGroup() as tasks:
            tasks.create_task(export_reader())
            tasks.create_task(logout_writer())
        report.update(repeatable_read_snapshot_retained=True, logout_committed=True,
                      fresh_status_and_original_chunk_rejected=True)
    finally:
        for client in (reader, writer):
            if client is not None:
                await client.close()


async def main():
    # This script owns its database and must never use an inherited application
    # DSN or the persistent local acceptance cluster on 15432.
    if os.environ.get("PGHOST", "127.0.0.1") != "127.0.0.1" or os.environ.get("PGPORT", "5432") != "5432":
        raise ValueError("Use the disposable loopback PostgreSQL on port 5432")
    os.environ.setdefault("PGHOST", "127.0.0.1")
    os.environ.setdefault("PGPORT", "5432")
    report = {"status": "running", "external_calls": 0, "production_changed": False}

    async def serve(config, name, role):
        await verify(config, name, role, report)

    await isolated_database(serve=serve)
    observer = await asyncpg.connect(host="127.0.0.1", port=5432, user=os.environ.get("PGUSER", "postgres"),
                                     database="postgres")
    try:
        assert not await observer.fetchval("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)", report["database"])
        assert not await observer.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", report["role"])
    finally:
        await observer.close()
    report.update(status="passed", database_and_role_removed=True)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
