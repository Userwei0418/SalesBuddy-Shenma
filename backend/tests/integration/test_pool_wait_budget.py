"""Actual asyncpg pool cancellation/release after an exhausted one-slot pool."""
import asyncio
from dataclasses import replace
import time

import pytest

from sales_backend.db import Database, DatabaseBusy
from tests.integration.conftest import _dsn
from tests.test_tokens import settings


@pytest.mark.asyncio
async def test_real_pool_exhaustion_is_bounded_and_retains_capacity():
    database = Database(replace(settings(), database_url=_dsn(), database_min_pool_size=1,
                               database_max_pool_size=1, database_pool_wait_seconds=.05))
    await database.connect()
    try:
        async with database.connection() as holder:
            pid = await holder.fetchval('SELECT pg_backend_pid()')
            started = time.perf_counter()
            with pytest.raises(DatabaseBusy):
                async with database.connection():
                    pytest.fail('single pool slot was already held')
            assert .03 <= time.perf_counter() - started < 2

            async def blocked():
                async with database.connection():
                    pytest.fail('cancelled waiter acquired')

            task = asyncio.create_task(blocked())
            await asyncio.sleep(.005)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        async with database.connection() as recovered:
            assert await recovered.fetchval('SELECT pg_backend_pid()') == pid
            assert await recovered.fetchval('SELECT 1') == 1
    finally:
        await database.close()
