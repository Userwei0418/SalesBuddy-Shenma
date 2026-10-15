"""Real competing account transactions against a disposable database only."""
import asyncio
import os
import re
from uuid import uuid4

import asyncpg

from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode


async def seed(owner, limit):
    workspace, admin = uuid4(), uuid4()
    await owner.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'合成并发额度租户')",
                        workspace, 'quota-'+uuid4().hex)
    await owner.execute("""INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name)
        VALUES($1,$2,$3,'合成管理员')""", admin, workspace, str(admin))
    await owner.execute("""INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code)
        VALUES($1,$2,'administrator','workspace')""", workspace, admin)
    await owner.execute("UPDATE control.tenant_account_quota SET account_limit=$2 WHERE workspace_id=$1", workspace, limit)
    return ActorContext(workspace_id=str(workspace), user_id=str(admin), role=RoleCode.ADMINISTRATOR,
                        data_scope=DataScope.WORKSPACE)


async def insert(connection, actor):
    uid = uuid4()
    await set_request_context(connection, actor)
    await connection.execute("""INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name)
        VALUES($1,$2::uuid,$3,'合成并发用户')""", uid, actor.workspace_id, str(uid))
    return uid


async def verify(dsn):
    owner = await asyncpg.connect(dsn)
    connections = []
    try:
        assert (await owner.fetchval('SELECT current_database()')).startswith('salegent_verify_integration_')
        runtime = os.environ['SALES_TEST_ROLE']
        assert re.fullmatch(r'salegent_verify_role_[a-f0-9]+', runtime)
        actor = await seed(owner, 4)
        other = await seed(owner, 2)
        for _ in range(12):
            connection = await asyncpg.connect(dsn)
            await connection.execute(f'SET ROLE "{runtime}"')
            await connection.execute("SET lock_timeout='5s'; SET statement_timeout='10s'")
            connections.append(connection)

        async def create(connection, identity=actor):
            async with connection.transaction():
                return await insert(connection, identity)

        async with asyncio.timeout(20):
            outcomes = await asyncio.gather(*(create(c) for c in connections), return_exceptions=True)
        accepted = [x for x in outcomes if not isinstance(x, BaseException)]
        assert len(accepted) == 3, outcomes
        assert all(isinstance(x, asyncpg.PostgresError) and x.sqlstate == 'P7429'
                   for x in outcomes if isinstance(x, BaseException)), outcomes
        row = await owner.fetchrow("""SELECT q.active_accounts,(SELECT count(*) FROM platform.user_ref u
            WHERE u.workspace_id=q.workspace_id AND u.status='active' AND u.deleted_at IS NULL) AS actual
            FROM control.tenant_account_quota q WHERE workspace_id=$1::uuid""", actor.workspace_id)
        assert row['active_accounts'] == row['actual'] == 4
        print('PASS 12 competing restricted transactions accept exactly 3 free seats; rejected rows roll back')

        # Leave one seat. A rolled-back transaction cannot permanently consume it.
        await owner.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1", accepted[0])
        first, second, third = connections[:3]
        transaction = first.transaction()
        await transaction.start()
        held = await insert(first, actor)
        pending = asyncio.create_task(create(second))
        try:
            async with asyncio.timeout(4):
                while not await owner.fetchval("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1", second.get_server_pid()):
                    if pending.done():
                        raise AssertionError('Competing account did not wait for the reserved seat')
                    await asyncio.sleep(0.02)
            # A blocked company never takes the other company's slot or lock.
            async with asyncio.timeout(2):
                await create(third, other)
        finally:
            await transaction.rollback()
        async with asyncio.timeout(5):
            await pending
        assert not await owner.fetchval('SELECT EXISTS(SELECT 1 FROM platform.user_ref WHERE id=$1)', held)
        assert await owner.fetchval('SELECT active_accounts FROM control.tenant_account_quota WHERE workspace_id=$1::uuid', actor.workspace_id) == 4
        assert await owner.fetchval('SELECT active_accounts FROM control.tenant_account_quota WHERE workspace_id=$1::uuid', other.workspace_id) == 2
        print('PASS rollback releases the pending slot; a second tenant keeps making progress independently')
    finally:
        for connection in connections:
            await connection.close()
        await owner.close()


if __name__ == '__main__':
    asyncio.run(verify(os.environ['SALES_TEST_DATABASE_URL']))
