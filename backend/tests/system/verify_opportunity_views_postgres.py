# ruff: noqa: S108 -- Explicit local PostgreSQL Unix socket allowlist, not temporary credentials.
"""S3 real concurrent transactions on a local disposable non-bypass database.

Uses the same local runner as S2; no production connection or external service.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_integration_postgres import main as disposable_database

from sales_backend.contracts.opportunity_views import OpportunityViewCreate, OpportunityViewUpdate
from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.opportunity_views import OpportunityViewRepository


async def run(config, name, role):
    assert re.fullmatch(r"salegent_verify_integration_[a-f0-9]+", name)
    assert re.fullmatch(r"salegent_verify_role_[a-f0-9]+", role)
    repo = OpportunityViewRepository()

    @asynccontextmanager
    async def session(code="XS001"):
        connection = await asyncpg.connect(database=name, **config, command_timeout=15)
        await _initialize_connection(connection)
        try:
            async with connection.transaction():
                await connection.execute(f'SET LOCAL ROLE "{role}"')
                assert not await connection.fetchval(
                    "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
                )
                record = await connection.fetchrow(
                    "SELECT * FROM security.resolve_account_actor('demo-sales-workspace',$1,NULL)", code
                )
                actor = IdentityRepository._actor(record).context
                await set_request_context(connection, actor)
                yield connection, actor
        finally:
            await connection.close()

    async def create():
        async with session() as (connection, actor):
            return await repo.create(connection, actor, OpportunityViewCreate(name="并发个人偏好"))

    # Two independent connections; advisory limit lock must not lose creations.
    initial = await asyncio.gather(create(), create())
    assert len({str(item["id"]) for item in initial}) == 2
    print("S3_CHECK_OK independent concurrent creations commit", flush=True)

    async def update(label):
        async with session() as (connection, actor):
            return await repo.update(
                connection, actor, initial[0]["id"], OpportunityViewUpdate(name=label, version_no=1)
            )

    outcomes = await asyncio.gather(update("并发A"), update("并发B"), return_exceptions=True)
    assert sum(isinstance(value, dict) for value in outcomes) == 1, outcomes
    assert sum(isinstance(value, VersionConflict) for value in outcomes) == 1, outcomes
    async with session() as (connection, actor):
        saved = await repo.get(connection, initial[0]["id"])
        assert saved["version_no"] == 2 and saved["name"] in {"并发A", "并发B"}
        # Complete the fixture to 99 under the same runtime RLS and audit trigger.
        await connection.execute(
            "INSERT INTO crm.opportunity_saved_view(workspace_id,owner_user_ref_id,name,config) "
            "SELECT common.current_workspace_id(),common.current_user_ref_id(),'并发上限-'||n,$1::jsonb "
            "FROM generate_series(1,97) n",
            saved["config"],
        )
    print("S3_CHECK_OK stale concurrent update is rejected without overwrite", flush=True)

    outcomes = await asyncio.gather(create(), create(), return_exceptions=True)
    assert sum(isinstance(value, dict) for value in outcomes) == 1, outcomes
    assert sum(isinstance(value, ValueError) for value in outcomes) == 1, outcomes
    async with session() as (connection, actor):
        assert (await repo.list(connection))["total"] == 100
    print("S3_CHECK_OK concurrent creates cannot exceed owner limit 100", flush=True)

    async with session("XS002") as (connection, actor):
        assert (await repo.list(connection))["total"] == 0
        assert await connection.fetchval("SELECT count(*) FROM crm.opportunity_saved_view") == 0
    print("S3_CHECK_OK private preferences stay hidden from other sales", flush=True)


async def verify():
    # This check must precede the runner's first connection and CREATE DATABASE.
    host = os.environ.get("PGHOST", "/tmp")
    local_tcp = host in {"localhost", "127.0.0.1", "::1"}
    local_socket = host in {"/tmp", "/private/tmp"} or host.startswith(("/tmp/", "/private/tmp/"))
    if not (local_tcp or local_socket):
        raise RuntimeError("S3 concurrency verification accepts only local PostgreSQL host/socket")
    await disposable_database(run)


if __name__ == "__main__":
    asyncio.run(verify())
