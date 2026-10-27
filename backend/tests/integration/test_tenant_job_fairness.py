"""Company rotation, aggregate lease limits and platform-only scheduling settings."""
from uuid import uuid4

import asyncpg
import pytest

from tests.integration.feishu_fixtures import seed_execute, seed_fetchval, fixture_owner
from tests.integration.test_platform_control import platform, provision  # noqa: F401

pytestmark = pytest.mark.asyncio


async def queue(c, priority=100, count=4, kind=None):
    ws, kind = uuid4(), kind or 'fairness.'+uuid4().hex
    await seed_execute(c,"INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'合成轮转租户')",ws,'fair-'+uuid4().hex)
    for index in range(count):
        await seed_execute(c,"INSERT INTO ops.job(workspace_id,job_type,payload,priority) VALUES($1,$2,'{}'::jsonb,$3)",ws,kind,priority-index)
    return ws, kind


async def claim(c, kind):
    return await c.fetchrow("SELECT * FROM ops.claim_job('fairness-test',30,$1::text[],false)",[kind])


async def test_rotation_precedes_global_priority_and_preserves_in_company_priority(connection):
    c=connection
    a,kind=await queue(c,priority=100)
    b,_=await queue(c,priority=10,kind=kind)
    first=await claim(c,kind);second=await claim(c,kind)
    assert {first['workspace_id'],second['workspace_id']}=={a,b}
    assert sorted([first['priority'],second['priority']])==[10,100]
    third=await claim(c,kind);fourth=await claim(c,kind)
    assert third['workspace_id']==first['workspace_id'] and fourth['workspace_id']==second['workspace_id']
    assert sorted([third['priority'],fourth['priority']])==[9,99]
    assert await claim(c,kind) is None
    # Lease expiry permits a new attempt but invalidates the old attempt token.
    await seed_execute(c,"UPDATE ops.job SET locked_until=clock_timestamp()-interval '1 second' WHERE id=$1",first['id'])
    reclaimed=await claim(c,kind)
    assert reclaimed['id']==first['id'] and reclaimed['lease_token']!=first['lease_token']
    assert reclaimed['attempts']==2


async def test_capacity_is_shared_across_types_and_lowering_does_not_cancel_active_jobs(connection):
    c=connection;ws,kind=await queue(c)
    other=kind+'.other'
    await seed_execute(c,"INSERT INTO ops.job(workspace_id,job_type,payload,priority) VALUES($1,$2,'{}',100)",ws,other)
    one=await claim(c,kind);two=await claim(c,other)
    assert one and two and await claim(c,kind) is None
    await seed_execute(c,'UPDATE control.tenant_job_schedule SET concurrency_limit=1 WHERE workspace_id=$1',ws)
    assert await claim(c,kind) is None
    assert await seed_fetchval(c,"SELECT count(*) FROM ops.job WHERE workspace_id=$1 AND status='running'",ws)==2
    await seed_execute(c,"UPDATE ops.job SET status='succeeded',lease_token=NULL,locked_until=NULL WHERE workspace_id=$1 AND status='running'",ws)
    assert await claim(c,kind) and await claim(c,kind) is None


async def test_suspended_tenant_and_disabled_modules_do_not_consume_turns(connection):
    c=connection;a,kind=await queue(c);b,_=await queue(c,kind=kind)
    await seed_execute(c,"UPDATE platform.workspace SET status='suspended' WHERE id=$1",a)
    assert (await claim(c,kind))['workspace_id']==b
    assert (await claim(c,kind))['workspace_id']==b
    assert await claim(c,kind) is None
    # Built-in AI tasks retain the module gate as well as tenant rotation.
    await seed_execute(c,"INSERT INTO ops.job(workspace_id,job_type,payload) VALUES($1,'business.advice','{}')",b)
    assert await claim(c,'business.advice') is None


async def test_expired_attempt_cleanup_and_effect_reconciliation_still_run(connection):
    c=connection;ws,kind=await queue(c,count=1)
    row=await claim(c,kind)
    await seed_execute(c,"UPDATE ops.job SET attempts=max_attempts,locked_until=clock_timestamp()-interval '1 second' WHERE id=$1",row['id'])
    assert await claim(c,kind) is None
    assert await seed_fetchval(c,'SELECT status FROM ops.job WHERE id=$1',row['id'])=='dead_letter'
    ws,kind=await queue(c,count=1)
    row=await claim(c,kind)
    await seed_execute(c,'INSERT INTO ops.job_effect(job_id,workspace_id,lease_token) VALUES($1,$2,$3)',row['id'],ws,row['lease_token'])
    await seed_execute(c,"UPDATE ops.job SET locked_until=clock_timestamp()-interval '1 second' WHERE id=$1",row['id'])
    assert await claim(c,kind) is None
    assert await seed_fetchval(c,'SELECT status FROM ops.job WHERE id=$1',row['id'])=='succeeded'


async def test_platform_settings_cas_audit_and_recent_mfa(platform,connection):  # noqa: F811
    client,database=platform
    _,_,created=await provision(client)
    path='/api/v1/platform/tenants/'+created['id']+'/jobs'
    before=(await client.get(path)).json()
    assert before['concurrency_limit']==2 and before['running']==0
    body={'version_no':before['version_no'],'concurrency_limit':1,'reason':'隔离验收降低并发'}
    response=await client.post(path,json=body)
    assert response.status_code==200,response.text
    assert response.json()['concurrency_limit']==1
    assert (await client.post(path,json=body)).status_code==409
    assert (await client.post(path,json={**body,'concurrency_limit':0})).status_code==422
    audit=(await client.get('/api/v1/platform/audit')).json()['items']
    assert any(x['action']=='tenant.job_settings' for x in audit)
    async with fixture_owner(connection):
        await connection.execute("UPDATE control.session SET mfa_verified_at=clock_timestamp()-interval '6 minutes' WHERE operator_id=$1",database.operator_id)
    response=await client.post(path,json={**body,'version_no':before['version_no']+1})
    assert response.status_code==403 and response.json()['detail']=='PLATFORM_REAUTH_REQUIRED'


async def test_company_runtime_cannot_read_or_change_schedule(connection):
    for sql in ('SELECT * FROM control.tenant_job_schedule',"SELECT control.job_settings('forged',gen_random_uuid())",
                "SELECT control.save_job_settings('forged',gen_random_uuid(),1,16,'forged')"):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await connection.execute(sql)
