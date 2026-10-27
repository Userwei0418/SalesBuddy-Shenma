"""Revocation is serialized with support reads on independent restricted connections."""
import asyncio
import hashlib
import json
import os
import secrets
from uuid import uuid4

import asyncpg
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.services.platform_control import PUBLIC_FUNCTIONS


async def verify(dsn):
    owner=await asyncpg.connect(dsn)
    assert (await owner.fetchval('SELECT current_database()')).startswith('salegent_verify_integration_')
    role='support_verify_'+uuid4().hex
    connections=[];created=False
    try:
        ws,admin,operator,grant=[uuid4() for _ in range(4)]
        await owner.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'隔离支持并发公司')",ws,'support-'+uuid4().hex)
        await owner.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,'SUPPORTADMIN','隔离管理员')",admin,ws,str(admin))
        await owner.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,'administrator','workspace')",ws,admin)
        await owner.execute("INSERT INTO control.operator(id,account_code,display_name,password_hash,mfa_secret) VALUES($1,$2,'隔离支持人员','unusable','{}')",operator,'SUPPORT'+uuid4().hex[:12].upper())
        token=hashlib.sha256(secrets.token_bytes(32)).hexdigest()
        await owner.execute("INSERT INTO control.session(token_hash,operator_id,expires_at,mfa_verified_at) VALUES($1,$2,clock_timestamp()+interval '30 minutes',clock_timestamp())",token,operator)
        await owner.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS');created=True
        await owner.execute(f'GRANT USAGE ON SCHEMA control TO "{role}"')
        for signature in PUBLIC_FUNCTIONS:await owner.execute(f'GRANT EXECUTE ON FUNCTION control.{signature} TO "{role}"')
        runtime=os.environ['SALES_TEST_ROLE'];assert runtime.startswith('salegent_verify_role_') and all(x in '0123456789abcdef' for x in runtime.removeprefix('salegent_verify_role_'))
        reader=await asyncpg.connect(dsn);connections.append(reader);await reader.execute(f'SET ROLE "{role}"')
        revoker=await asyncpg.connect(dsn);connections.append(revoker);await revoker.execute(f'SET ROLE "{runtime}"')
        actor=ActorContext(workspace_id=str(ws),user_id=str(admin),role=RoleCode.ADMINISTRATOR,data_scope=DataScope.WORKSPACE)
        async with revoker.transaction():
            await set_request_context(revoker,actor)
            await revoker.execute("SELECT security.approve_support($1,$2,ARRAY['customers'],clock_timestamp()+interval '1 hour','并发隔离测试')",grant,operator)
        transaction=reader.transaction();await transaction.start()
        result=json.loads(await reader.fetchval("SELECT control.support_read($1,$2,'customers',NULL)",token,grant));assert result['items']==[]
        async def revoke():
            async with revoker.transaction():
                await set_request_context(revoker,actor)
                return await revoker.fetchval("SELECT security.revoke_support($1,'结束排障')",grant)
        pending=asyncio.create_task(revoke())
        # Observe the real lock wait, instead of asserting a timing assumption.
        for _ in range(100):
            if await owner.fetchval("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1",revoker.get_server_pid()):break
            await asyncio.sleep(.02)
        else:raise AssertionError('Revocation did not wait for active read transaction')
        assert not pending.done()
        await transaction.commit();await asyncio.wait_for(pending,5)
        denied=json.loads(await reader.fetchval("SELECT control.support_read($1,$2,'customers',NULL)",token,grant))
        assert denied=={'error':'SUPPORT_ACCESS_DENIED'}
        actions=await owner.fetch("SELECT action FROM control.support_event WHERE grant_id=$1 ORDER BY id",grant)
        assert [r['action'] for r in actions]==['approved','read','revoked','denied']
        # The denial audit survives the function transaction and contains no data.
        assert await owner.fetchval("SELECT count(*) FROM control.support_event WHERE grant_id=$1 AND action='denied' AND row_count=0",grant)==1
        print('PASS independent restricted connections: read completes before revoke, all later reads denied, audit committed')
    finally:
        for conn in connections:await conn.close()
        if created:
            await owner.execute(f'DROP OWNED BY "{role}"');await owner.execute(f'DROP ROLE "{role}"')
        await owner.close()


if __name__=='__main__':asyncio.run(verify(os.environ['SALES_TEST_DATABASE_URL']))
