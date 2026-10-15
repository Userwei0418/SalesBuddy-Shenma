"""Call quotas under competing real restricted database connections."""
import asyncio
import os
import re
from uuid import uuid4

import asyncpg

from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.repositories.model_calls import ModelCallRepository
from verify_account_quota_postgres import seed


async def reserve(connection, actor, event=None):
    await set_request_context(connection, actor)
    invocation = await ModelCallRepository().start(connection, actor, operation='concurrency_fixture',
        operation_id=str(event or uuid4()), run_id=None, endpoint='/v1/chat/completions', model='synthetic',
        metadata={}, attempt=1, request_id=None, provider='synthetic')
    await connection.fetchval('SELECT security.reserve_model_call($1::uuid,$2::uuid)', invocation, uuid4())
    return invocation


async def verify(dsn):
    owner = await asyncpg.connect(dsn)
    connections = []
    try:
        assert (await owner.fetchval('SELECT current_database()')).startswith('salegent_verify_integration_')
        runtime = os.environ['SALES_TEST_ROLE']
        assert re.fullmatch(r'salegent_verify_role_[a-f0-9]+', runtime)
        actor, other = await seed(owner, None), await seed(owner, None)
        await owner.execute("UPDATE control.tenant_call_quota SET call_limit=3 WHERE workspace_id=$1::uuid AND resource='ai_calls'", actor.workspace_id)
        for _ in range(10):
            connection = await asyncpg.connect(dsn)
            await _initialize_connection(connection)
            await connection.execute(f'SET ROLE "{runtime}"')
            await connection.execute("SET lock_timeout='5s'; SET statement_timeout='10s'")
            connections.append(connection)
        async def call(conn, identity=actor, event=None):
            async with conn.transaction():
                return await reserve(conn, identity, event)
        async with asyncio.timeout(20):
            results = await asyncio.gather(*(call(c) for c in connections), return_exceptions=True)
        assert len([r for r in results if isinstance(r, str)]) == 3, results
        assert all(isinstance(r, asyncpg.PostgresError) and r.sqlstate=='P7431' for r in results if not isinstance(r, str)), results
        assert await owner.fetchval('SELECT count(*) FROM agent.model_invocation WHERE workspace_id=$1::uuid', actor.workspace_id) == 3
        print('PASS 10 restricted connections accept exactly 3 calls; rejected audit rows roll back')
        # Ten simultaneous retries share one event and only one quota unit.
        event = uuid4()
        async with asyncio.timeout(20):
            await asyncio.gather(*(call(c, other, event) for c in connections))
        assert await owner.fetchval('SELECT count(*) FROM control.call_event WHERE workspace_id=$1::uuid', other.workspace_id) == 1
        assert await owner.fetchval('SELECT count(*) FROM control.call_attempt WHERE workspace_id=$1::uuid', other.workspace_id) == 10
        print('PASS concurrent retries keep one logical quota event and ten independent audit receipts')
        await owner.execute("UPDATE control.tenant_call_quota SET call_limit=4 WHERE workspace_id=$1::uuid AND resource='ai_calls'", actor.workspace_id)
        tx = connections[0].transaction(); await tx.start()
        held = await reserve(connections[0], actor)
        pending = asyncio.create_task(call(connections[1]))
        try:
            async with asyncio.timeout(4):
                while not await owner.fetchval("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1", connections[1].get_server_pid()):
                    if pending.done(): raise AssertionError('did not wait for tenant quota')
                    await asyncio.sleep(.02)
            async with asyncio.timeout(2): await call(connections[2], other)
        finally:
            await tx.rollback()
        async with asyncio.timeout(5): await pending
        assert not await owner.fetchval('SELECT EXISTS(SELECT 1 FROM agent.model_invocation WHERE id=$1::uuid)', held)
        print('PASS rollback releases reservation and a second tenant progresses independently')
    finally:
        for connection in connections: await connection.close()
        await owner.close()


if __name__ == '__main__':
    asyncio.run(verify(os.environ['SALES_TEST_DATABASE_URL']))
