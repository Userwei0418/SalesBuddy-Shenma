"""Concurrent real worker claims cannot exceed a company's lease allocation."""
import asyncio
import os
import re
from uuid import uuid4

import asyncpg


async def verify(dsn):
    owner=await asyncpg.connect(dsn);connections=[]
    try:
        if not (await owner.fetchval('SELECT current_database()')).startswith('salegent_verify_integration_'):
            raise RuntimeError('Disposable integration database required')
        role=os.environ['SALES_TEST_ROLE'];assert re.fullmatch(r'salegent_verify_role_[a-f0-9]+',role)
        kind='concurrent-fairness.'+uuid4().hex;companies=[uuid4(),uuid4()]
        for ws in companies:
            await owner.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'并发轮转验收')",ws,str(ws))
            await owner.execute('UPDATE control.tenant_job_schedule SET concurrency_limit=1 WHERE workspace_id=$1',ws)
            await owner.execute("INSERT INTO ops.job(workspace_id,job_type,payload) SELECT $1,$2,'{}'::jsonb FROM generate_series(1,20)",ws,kind)
        for _ in range(8):
            c=await asyncpg.connect(dsn);await c.execute(f'SET ROLE "{role}"');connections.append(c)
        async def claim(c):
            return await c.fetchrow("SELECT * FROM ops.claim_job('concurrent-fairness',30,$1::text[],false)",[kind])
        await asyncio.gather(*(claim(c) for c in connections))
        # A skipped lock is allowed to return no work; refill after commit.
        for _ in range(2):await claim(connections[0])
        counts=await owner.fetch("SELECT workspace_id,count(*) AS n FROM ops.job WHERE job_type=$1 AND status='running' GROUP BY workspace_id",kind)
        assert len(counts)==2 and all(r['n']==1 for r in counts)
        assert not any(await asyncio.gather(*(claim(c) for c in connections)))
        print('PASS eight concurrent claimers keep one active lease per company and serve both companies')
        # A configuration/lifecycle writer must not stall unrelated tenant claims.
        await owner.execute("UPDATE ops.job SET locked_until=clock_timestamp()-interval '1 second' WHERE job_type=$1",kind)
        async with owner.transaction():
            await owner.execute('SELECT 1 FROM platform.workspace WHERE id=$1 FOR UPDATE',companies[0])
            row=await asyncio.wait_for(claim(connections[0]),3)
            assert row and row['workspace_id']==companies[1]
        print('PASS a locked tenant is skipped without blocking another tenant')
    finally:
        for c in connections:await c.close()
        await owner.close()


if __name__=='__main__':asyncio.run(verify(os.environ['SALES_TEST_DATABASE_URL']))
