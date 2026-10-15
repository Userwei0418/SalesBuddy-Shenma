"""Commit-order tenant pause probe on a runner-created disposable database only."""
import asyncio
import os
import secrets
import json
from sales_backend.security.platform_mfa import PlatformMFACipher
from uuid import uuid4

import asyncpg

from sales_backend.db import set_request_context, TenantInactive
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.services.platform_control import PUBLIC_FUNCTIONS


async def verify(dsn, runtime_role):
    owner = await asyncpg.connect(dsn)
    if not (await owner.fetchval('SELECT current_database()')).startswith('salegent_verify_integration_'):
        await owner.close()
        raise RuntimeError('Disposable integration database required')
    platform_role = 'tenant_pause_probe_' + uuid4().hex
    actor_id, ws, uid = uuid4(), uuid4(), uuid4()
    token = uuid4().hex + uuid4().hex
    writer = control = None
    transaction = None
    change = None
    try:
        await owner.execute(f'CREATE ROLE "{platform_role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await owner.execute(f'GRANT USAGE ON SCHEMA control TO "{platform_role}"')
        for signature in PUBLIC_FUNCTIONS:
            await owner.execute(f'GRANT EXECUTE ON FUNCTION control.{signature} TO "{platform_role}"')
        cipher=PlatformMFACipher('fixture',{'fixture':secrets.token_bytes(32)})
        await owner.execute("INSERT INTO control.operator(id,account_code,display_name,password_hash,mfa_secret) VALUES($1,$2,'事务验收','unusable-fixture',$3::jsonb)",actor_id,'PAUSE'+uuid4().hex.upper(),json.dumps(cipher.seal(str(actor_id),secrets.token_bytes(20))))
        await owner.execute("INSERT INTO control.session(token_hash,operator_id,expires_at,mfa_verified_at) VALUES($1,$2,clock_timestamp()+interval '1 hour',clock_timestamp())",token,actor_id)
        await owner.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'并发隔离验收')",ws,'pause-'+uuid4().hex)
        await owner.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) VALUES($1,$2,$3,'测试管理员')",uid,ws,str(uid))
        await owner.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,'administrator','workspace')",ws,uid)
        writer,control=await asyncpg.connect(dsn),await asyncpg.connect(dsn)
        await writer.execute(f'SET ROLE "{runtime_role}"')
        await control.execute(f'SET ROLE "{platform_role}"')
        actor=ActorContext(workspace_id=str(ws),user_id=str(uid),role=RoleCode.ADMINISTRATOR,data_scope=DataScope.WORKSPACE)
        transaction=writer.transaction(); await transaction.start()
        await set_request_context(writer,actor)
        change=asyncio.create_task(control.fetchval("SELECT control.transition($1,$2,1,'suspended','并发验证')",token,ws))
        blocked=False
        for _ in range(100):
            if await owner.fetchval('SELECT $1=ANY(pg_blocking_pids($2))',writer.get_server_pid(),control.get_server_pid()):
                blocked=True;break
            await asyncio.sleep(.02)
        assert blocked,'Pause must serialize behind an already-authorized write transaction'
        await transaction.commit();transaction=None
        await asyncio.wait_for(change,5)
        assert await owner.fetchval('SELECT status FROM platform.workspace WHERE id=$1',ws)=='suspended'
        try:
            async with writer.transaction():
                await set_request_context(writer,actor)
        except TenantInactive:
            pass
        else:
            raise AssertionError('Captured pre-pause identity performed another transaction')
        print('PASS tenant pause waits for prior writes; later captured identities are rejected')
    finally:
        if transaction: await transaction.rollback()
        if change and not change.done():
            change.cancel();await asyncio.gather(change,return_exceptions=True)
        for conn in (writer,control):
            if conn: await conn.close()
        await owner.execute(f'DROP OWNED BY "{platform_role}"')
        await owner.execute(f'DROP ROLE "{platform_role}"')
        await owner.close()


if __name__=='__main__':
    asyncio.run(verify(os.environ['SALES_TEST_DATABASE_URL'],os.environ['SALES_TEST_ROLE']))
