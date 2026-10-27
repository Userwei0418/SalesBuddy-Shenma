"""Independent application connections share one atomic tenant request bucket."""
import asyncio
import os
import re

import asyncpg

from sales_backend.db import _initialize_connection, set_request_context
from verify_account_quota_postgres import seed


async def verify(dsn):
    owner = await asyncpg.connect(dsn)
    connections = []
    try:
        assert (await owner.fetchval('SELECT current_database()')).startswith('salegent_verify_integration_')
        role = os.environ['SALES_TEST_ROLE']
        assert re.fullmatch(r'salegent_verify_role_[a-f0-9]+', role)
        actor, other = await seed(owner, None), await seed(owner, None)
        await owner.execute("UPDATE control.tenant_http_limit SET rate_per_minute=1,burst=3,tokens=3 WHERE workspace_id=$1::uuid AND bucket='read'", actor.workspace_id)
        for _ in range(10):
            conn = await asyncpg.connect(dsn)
            await _initialize_connection(conn)
            await conn.execute(f'SET ROLE "{role}"')
            await conn.execute("SET lock_timeout='5s'; SET statement_timeout='10s'")
            connections.append(conn)
        async def consume(conn, identity=actor):
            async with conn.transaction():
                await set_request_context(conn, identity)
                return await conn.fetchval("SELECT security.consume_http_request('read')")
        async with asyncio.timeout(15):
            results = await asyncio.gather(*(consume(c) for c in connections))
        assert sum(r['allowed'] for r in results) == 3, results
        assert all(r['allowed'] or 1 <= r['retry_after'] <= 60 for r in results)
        row = await owner.fetchrow("SELECT accepted,rejected FROM control.tenant_http_limit WHERE workspace_id=$1::uuid AND bucket='read'", actor.workspace_id)
        assert tuple(row) == (3, 7), row
        print('PASS 10 independent application connections admit exactly 3 requests and persist 7 rejections')
        # Holding one tenant's token row never prevents another tenant's admission.
        tx = owner.transaction(); await tx.start()
        try:
            await owner.execute("SELECT 1 FROM control.tenant_http_limit WHERE workspace_id=$1::uuid AND bucket='read' FOR UPDATE", actor.workspace_id)
            pending = asyncio.create_task(consume(connections[0]))
            async with asyncio.timeout(3):
                while not await owner.fetchval("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1", connections[0].get_server_pid()):
                    if pending.done(): raise AssertionError('Expected request to wait on its own bucket')
                    await asyncio.sleep(.02)
            async with asyncio.timeout(2):
                assert (await consume(connections[1], other))['allowed']
        finally:
            await tx.rollback()
        assert not (await pending)['allowed']
        print('PASS another tenant proceeds while the first tenant bucket is locked')
    finally:
        for conn in connections: await conn.close()
        await owner.close()


if __name__ == '__main__':
    asyncio.run(verify(os.environ['SALES_TEST_DATABASE_URL']))
