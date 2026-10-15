"""Concurrent OTP consumption in an isolated database, never a shared deployment."""
import asyncio
import json
import os
import secrets
import time
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
from sales_backend.security.platform_mfa import PlatformMFACipher
from sales_backend.services.platform_control import PUBLIC_FUNCTIONS
sys.path.insert(0,str(Path(__file__).resolve().parents[2] / "scripts"))
from recover_platform_operator import recover


async def verify(dsn):
    owner=await asyncpg.connect(dsn)
    if not (await owner.fetchval('SELECT current_database()')).startswith('salegent_verify_integration_'):
        await owner.close();raise RuntimeError('Disposable integration database required')
    role='mfa_replay_'+uuid4().hex;operator=uuid4()
    one=two=None
    try:
        await owner.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await owner.execute(f'GRANT USAGE ON SCHEMA control TO "{role}"')
        for signature in PUBLIC_FUNCTIONS:
            await owner.execute(f'GRANT EXECUTE ON FUNCTION control.{signature} TO "{role}"')
        cipher=PlatformMFACipher('fixture',{'fixture':secrets.token_bytes(32)})
        await owner.execute("INSERT INTO control.operator(id,account_code,display_name,password_hash,mfa_secret) VALUES($1,$2,'并发隔离验收','unusable-fixture',$3::jsonb)",
                            operator,'MFA'+uuid4().hex.upper(),json.dumps(cipher.seal(str(operator),secrets.token_bytes(20))))
        one,two=await asyncpg.connect(dsn),await asyncpg.connect(dsn)
        for conn in (one,two): await conn.execute(f'SET ROLE "{role}"')
        step=int(time.time())//30
        results=await asyncio.gather(*[conn.fetchval('SELECT control.issue_session($1,$2,$3,$4,$5,$6)',
                 operator,'unusable-fixture',uuid4().hex+uuid4().hex,'synthetic-throttle',0,step) for conn in (one,two)],return_exceptions=True)
        assert sum(isinstance(r,str) for r in results)==1, 'Exactly one session must accept a concurrently verified OTP'
        errors=[r for r in results if isinstance(r,asyncpg.PostgresError)]
        assert len(errors)==1 and errors[0].sqlstate=='P7403'
        assert await owner.fetchval('SELECT count(*) FROM control.session WHERE operator_id=$1',operator)==1
        print('PASS concurrent reuse of one platform OTP creates exactly one authenticated session')
        account=await owner.fetchval('SELECT account_code FROM control.operator WHERE id=$1',operator)
        await recover(dsn,account,'Isolated-Recovery-2026','隔离恢复验收')
        row=await owner.fetchrow('SELECT mfa_secret,mfa_revision,mfa_last_step FROM control.operator WHERE id=$1',operator)
        assert row['mfa_secret'] is None and row['mfa_revision']==1 and row['mfa_last_step']==-1
        assert not await owner.fetchval('SELECT EXISTS(SELECT 1 FROM control.session WHERE operator_id=$1 AND revoked_at IS NULL)',operator)
        assert await owner.fetchval("SELECT count(*) FROM control.audit WHERE operator_id=$1 AND action='platform.owner_recovery'",operator)==1
        print('PASS owner recovery rotates credentials, revokes all sessions and requires new MFA enrollment')
    finally:
        for conn in (one,two):
            if conn: await conn.close()
        await owner.execute(f'DROP OWNED BY "{role}"')
        await owner.execute(f'DROP ROLE "{role}"')
        await owner.close()


if __name__=='__main__':
    asyncio.run(verify(os.environ['SALES_TEST_DATABASE_URL']))
