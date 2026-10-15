"""Competing byte reservations on a disposable DB, under actual restricted roles."""
import asyncio
import os
import re
from uuid import uuid4

import asyncpg

from sales_backend.db import set_request_context
from verify_account_quota_postgres import seed


async def storage_actor(owner, limit):
    actor = await seed(owner, None)
    await owner.execute('INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1::uuid,$2::uuid)', actor.workspace_id, actor.user_id)
    await owner.execute("""INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code)
        VALUES($1::uuid,$2::uuid,'visit.upload','allow','self')""", actor.workspace_id, actor.user_id)
    await owner.execute('UPDATE control.tenant_storage_quota SET limit_bytes=$2 WHERE workspace_id=$1::uuid', actor.workspace_id, limit)
    return actor


async def reserve(connection, actor, identifier=None, receipt=None):
    identifier, receipt = identifier or uuid4(), receipt or uuid4()
    await set_request_context(connection, actor)
    result = await connection.fetchval('SELECT security.reserve_visit_storage($1,$2,6,\'postgres\',\'postgres\',$3,$4,$5,$6)',
        identifier, receipt, 'synthetic/'+str(identifier), 'a'*64, '/synthetic/'+str(identifier)+'.txt', 'synthetic.txt')
    assert result == 'reserved'
    return identifier, receipt


async def verify(dsn):
    owner = await asyncpg.connect(dsn); connections=[]
    try:
        assert (await owner.fetchval('SELECT current_database()')).startswith('salegent_verify_integration_')
        role=os.environ['SALES_TEST_ROLE']; assert re.fullmatch(r'salegent_verify_role_[a-f0-9]+',role)
        actor=await storage_actor(owner,18); other=await storage_actor(owner,6)
        for _ in range(10):
            connection=await asyncpg.connect(dsn)
            await connection.execute(f'SET ROLE "{role}"')
            await connection.execute("SET lock_timeout='5s'; SET statement_timeout='10s'")
            connections.append(connection)
        async def attempt(connection, who=actor):
            async with connection.transaction():
                return await reserve(connection,who)
        async with asyncio.timeout(20):
            outcomes=await asyncio.gather(*(attempt(c) for c in connections),return_exceptions=True)
        accepted=[x for x in outcomes if not isinstance(x,BaseException)]
        assert len(accepted)==3, outcomes
        assert all(isinstance(x,asyncpg.PostgresError) and x.sqlstate=='P7430' for x in outcomes if isinstance(x,BaseException)),outcomes
        assert await owner.fetchval('SELECT reserved_bytes FROM control.tenant_storage_quota WHERE workspace_id=$1::uuid',actor.workspace_id)==18
        assert await owner.fetchval('SELECT count(*) FROM control.storage_allocation WHERE workspace_id=$1::uuid',actor.workspace_id)==3
        print('PASS 10 concurrent uploads reserve exactly 18 bytes; 7 rejected allocations leave no rows')
        first,second,third=connections[:3]
        async with first.transaction():
            assert await reserve(first,actor,*accepted[0])==accepted[0]
            assert await first.fetchval('SELECT security.fence_unpublished_storage($1,$2)',*accepted[0])
            assert await first.fetchval('SELECT security.release_unpublished_storage($1,$2)',*accepted[0])
        tx=first.transaction(); await tx.start(); held=await reserve(first,actor)
        pending=asyncio.create_task(attempt(second))
        try:
            async with asyncio.timeout(4):
                while not await owner.fetchval("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1",second.get_server_pid()):
                    if pending.done(): raise AssertionError('reservation did not wait for tenant quota')
                    await asyncio.sleep(.02)
            async with asyncio.timeout(2): await attempt(third,other)
        finally: await tx.rollback()
        async with asyncio.timeout(5): await pending
        assert not await owner.fetchval('SELECT EXISTS(SELECT 1 FROM control.storage_allocation WHERE import_id=$1)',held[0])
        assert await owner.fetchval('SELECT reserved_bytes FROM control.tenant_storage_quota WHERE workspace_id=$1::uuid',actor.workspace_id)==18
        assert await owner.fetchval('SELECT reserved_bytes FROM control.tenant_storage_quota WHERE workspace_id=$1::uuid',other.workspace_id)==6
        print('PASS replay is free; rollback restores bytes; another tenant is not blocked')
    finally:
        for connection in connections: await connection.close()
        await owner.close()


if __name__=='__main__': asyncio.run(verify(os.environ['SALES_TEST_DATABASE_URL']))
