"""Run after integration fixtures, only inside the runner's disposable database."""

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import asyncpg

from sales_backend.db import set_request_context
from sales_backend.domain.tasks import TaskConflict, TaskForbidden
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.repositories.tasks import TaskRepository
from sales_backend.services.idempotency import execute_mutation
from sales_backend.services.tasks import TaskService


@asynccontextmanager
async def session(code):
    dsn = os.environ["SALES_TEST_DATABASE_URL"]
    role = os.environ["SALES_TEST_ROLE"]
    if not re.fullmatch(r"salegent_verify_role_[a-f0-9]+", role):
        raise RuntimeError("Disposable role required")
    connection = await asyncpg.connect(dsn)
    try:
        if not (await connection.fetchval("SELECT current_database()")).startswith("salegent_verify_"):
            raise RuntimeError("Disposable database required")
        for kind in ("json", "jsonb"):
            await connection.set_type_codec(
                kind, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text"
            )
        async with connection.transaction():
            await connection.execute(f'SET LOCAL ROLE "{role}"')
            assert not await connection.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
            row = await connection.fetchrow(
                "SELECT * FROM security.resolve_account_actor('demo-sales-workspace',$1,NULL)", code
            )
            actor = IdentityRepository._actor(row).context
            await set_request_context(connection, actor)
            yield connection, actor
    finally:
        await connection.close()


async def main():
    from uuid import uuid4

    async with session("ADMIN001") as (connection, admin):
        for code in ["OPS_C01", "OPS_C02"]:
            await OperationsAccountRepository().create(
                connection,
                admin,
                {"account_code": code, "display_name": code, "roles": ["operations"], "team_id": admin.team_ids[0]},
            )
    async with session("XS001") as (connection, creator):
        created = await TaskService().create(
            connection,
            actor=creator,
            description="并发领取隔离验证任务",
            target_position="operations",
            due_at=datetime.now(UTC) + timedelta(days=1),
            priority_code="normal",
        )
    ready = 0
    go = asyncio.Event()
    keys = {code: uuid4() for code in ["OPS_C01", "OPS_C02"]}

    async def claim(code):
        nonlocal ready
        async with session(code) as (connection, person):
            ready += 1
            if ready == 2:
                go.set()
            await go.wait()
            result = await execute_mutation(
                connection,
                person,
                keys[code],
                "tasks.events:" + created["id"],
                {"event_type": "accept"},
                lambda: TaskService().apply_event(
                    connection, actor=person, task_id=created["id"], event_type="accept", note=None
                ),
            )
            return code, result

    outcomes = await asyncio.gather(claim("OPS_C01"), claim("OPS_C02"), return_exceptions=True)
    assert sum(isinstance(r, TaskConflict) for r in outcomes) == 1, outcomes
    winner, result = next(r for r in outcomes if isinstance(r, tuple))
    loser = next(code for code in keys if code != winner)
    assert result["status"] == "pending_execution"
    async with session(winner) as (connection, person):
        replay = await execute_mutation(
            connection,
            person,
            keys[winner],
            "tasks.events:" + created["id"],
            {"event_type": "accept"},
            lambda: TaskService().apply_event(
                connection, actor=person, task_id=created["id"], event_type="accept", note=None
            ),
        )
        assert replay["id"] == result["id"] and replay["version_no"] == result["version_no"]
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM workflow.task_assignee WHERE task_id=$1::uuid AND responsibility='owner'",
                created["id"],
            )
            == 1
        )
    async with session(loser) as (connection, person):
        detail = await TaskRepository().detail(connection, task_id=created["id"])
        assert detail["owner_name"] == winner and detail["requires_action"] is False
        try:
            await TaskService().complete(connection, actor=person, task_id=created["id"], note="越权测试")
        except TaskForbidden:
            pass
        else:
            raise AssertionError("Candidate completed another owner task")
    print(
        "POSITION_CONCURRENCY_OK: one winner, one conflict, durable retry, observer read, owner-only completion",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
