"""Two-connection login/edit and cross-identifier races in a disposable database."""
import asyncio
from uuid import uuid4
import asyncpg
from run_integration_postgres import main
from sales_backend.db import set_request_context
from sales_backend.repositories.identity import IdentityRepository

async def verify(config, name, role):
    first = await asyncpg.connect(database=name, **config)
    second = await asyncpg.connect(database=name, **config)
    try:
        for conn in (first, second):
            await conn.execute(f'SET ROLE "{role}"')
        async def context(conn, account):
            row = await conn.fetchrow("SELECT * FROM security.resolve_account_actor('demo-sales-workspace',$1,NULL)", account)
            identity = IdentityRepository._actor(row).context
            await set_request_context(conn, identity)
            return identity
        async with first.transaction():
            admin = await context(first,'ADMIN001')
            await first.execute("UPDATE platform.user_ref SET phone_number='13800000001' WHERE account_code='XS001'")
        entered = asyncio.Event()
        async def remove_phone():
            async with second.transaction():
                await context(second,'ADMIN001')
                await second.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))",'account-management:'+admin.workspace_id)
                entered.set()
                await second.execute("SELECT id FROM platform.user_ref WHERE account_code='XS001' FOR UPDATE")
                await second.execute("UPDATE platform.user_ref SET phone_number=NULL WHERE account_code='XS001'")
                await second.execute('SELECT security.revoke_managed_account_sessions($1::uuid)',user.user_id)
        sid=uuid4()
        async with first.transaction():
            user=await context(first,'XS001')
            assert await first.fetchval("SELECT security.lock_password_login_identifier('13800000001')")
            pending=asyncio.create_task(remove_phone())
            await entered.wait()
            try:
                await asyncio.wait_for(asyncio.shield(pending),0.15)
                raise AssertionError('admin edit did not wait for login row lock')
            except asyncio.TimeoutError:
                pass
            await first.execute("INSERT INTO platform.auth_session(id,workspace_id,user_ref_id,refresh_token_hash,expires_at) VALUES($1,$2::uuid,$3::uuid,$4,clock_timestamp()+interval '1 hour')",sid,user.workspace_id,user.user_id,'0'*64)
        await asyncio.wait_for(pending,5)
        async with first.transaction():
            await context(first,'XS001')
            assert await first.fetchval('SELECT status FROM platform.auth_session WHERE id=$1',sid)=='revoked'
            assert not await first.fetchval("SELECT security.lock_password_login_identifier('13800000001')")
        entered.clear()
        async def conflicting_account():
            try:
                async with second.transaction():
                    await context(second,'ADMIN001')
                    entered.set()
                    await second.execute("UPDATE platform.user_ref SET account_code='13900000002' WHERE account_code='XS002'")
            except asyncpg.UniqueViolationError:
                return 'rejected'
            return 'allowed'
        async with first.transaction():
            await context(first,'ADMIN001')
            await first.execute("UPDATE platform.user_ref SET phone_number='13900000002' WHERE account_code='XS001'")
            pending=asyncio.create_task(conflicting_account())
            await entered.wait()
            try:
                await asyncio.wait_for(asyncio.shield(pending),0.15)
                raise AssertionError('identifier guard did not serialize writers')
            except asyncio.TimeoutError:
                pass
        assert await asyncio.wait_for(pending,5)=='rejected'
        print('PASS: login-before-removal revokes issued session; concurrent cross-column collision rejected')
    finally:
        await first.close()
        await second.close()

if __name__=='__main__':
    asyncio.run(main(serve=verify))
