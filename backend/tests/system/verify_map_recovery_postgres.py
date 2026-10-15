"""Concurrent map recovery in a disposable local database; no provider calls."""

import asyncio
import json

import asyncpg
from run_integration_postgres import main

from sales_backend.db import _initialize_connection
from sales_backend.repositories.customer_assets import today
from sales_backend.services.customer_map import refresh_missing_map
from tests.integration.test_customer_map_recovery import fixture
from tests.integration.test_operations_claims_sql import actor


async def verify(config, name, role):
    assert name.startswith("salegent_verify_integration_")

    async def connect():
        conn = await asyncpg.connect(database=name, **config)
        await _initialize_connection(conn)
        await conn.execute(f'SET ROLE "{role}"')
        return conn

    conn = await connect()
    try:
        async with conn.transaction():
            sales, customer = await fixture(conn)
    finally:
        await conn.close()

    async def refresh():
        conn = await connect()
        try:
            async with conn.transaction():
                who = await actor(conn, "XS001")
                return await refresh_missing_map(conn, who, as_of=today())
        finally:
            await conn.close()

    results = await asyncio.gather(*(refresh() for _ in range(6)))
    assert sum(item["enqueued_count"] for item in results) == 1, results
    assert {item["status"] for item in results} == {"updating"}
    conn = await connect()
    try:
        async with conn.transaction():
            await actor(conn, "XS001")
            count = await conn.fetchval(
                "SELECT count(*) FROM ops.job WHERE aggregate_id=$1::uuid AND payload->>'user_id'=$2",
                customer["id"], sales.user_id,
            )
            assert count == 1
    finally:
        await conn.close()
    print(json.dumps({"concurrent_requests": 6, "queued_jobs": count, "provider_calls": 0}), flush=True)


if __name__ == "__main__":
    asyncio.run(main(serve=verify))
