"""Operational metadata is MFA-only, bounded, tenant-scoped and read-only."""
from uuid import UUID, uuid4

import asyncpg
import pytest

from tests.integration.feishu_fixtures import fixture_owner
from tests.integration.test_platform_control import BASE, PASSWORD, platform as _platform_fixture
from tests.integration.test_operations_api import client_for, sign_in

pytestmark = pytest.mark.asyncio
platform = _platform_fixture


async def seed_company(connection, number, status='active'):
    ws = UUID(int=number)
    await connection.execute("INSERT INTO platform.workspace(id,external_workspace_id,name,status) VALUES($1,$2,'同名合成公司',$3)", ws, 'runtime-'+str(number), status)
    await connection.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", ws)
    return ws


async def snapshot(client, **params):
    response = await client.get(BASE+'/runtime', params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_runtime_snapshot_is_paged_by_identity_and_missing_quota_is_not_hidden(platform, connection):
    client, _ = platform
    async with fixture_owner(connection):
        for number in range(1, 23):
            await seed_company(connection, number)
        await connection.execute('DELETE FROM control.tenant_storage_quota WHERE workspace_id=$1', UUID(int=2))
        await connection.execute('UPDATE platform.workspace SET deleted_at=clock_timestamp() WHERE id=$1', UUID(int=22))
    first = await snapshot(client)
    assert len(first['items']) == 20 and first['next_cursor'] == str(UUID(int=20))
    assert first['items'][1]['originals'] == dict(available=False, limit_bytes=None, stored_bytes=None, reserved_bytes=None, over_limit=False, pending=None, overdue=None)
    second = await snapshot(client, after_id=first['next_cursor'])
    ids = [row['workspace_id'] for row in first['items']+second['items']]
    assert len(ids) == len(set(ids)) and str(UUID(int=21)) in ids and str(UUID(int=22)) not in ids
    assert second['next_cursor'] is None
    assert (await snapshot(client, after_id=str(UUID(int=2**128-1))))['items'] == []


async def test_runtime_counts_are_tenant_scoped_and_do_not_disclose_or_mutate_payloads(platform, connection):
    client, _ = platform
    marker = 'PRIVATE-CONTENT-MUST-NOT-LEAVE'
    async with fixture_owner(connection):
        a, b = await seed_company(connection, 1), await seed_company(connection, 2)
        actor = await connection.fetchval("INSERT INTO platform.user_ref(workspace_id,external_user_id,display_name) VALUES($1,'runtime-test','合成用户') RETURNING id", a)
        for state, available, lease in [('queued', '-1 hour', None), ('failed', '1 hour', None), ('running', '-1 hour', '1 hour'), ('running', '-1 hour', '-1 hour'), ('dead_letter', '-1 hour', None), ('succeeded', '-1 hour', None)]:
            await connection.execute("""INSERT INTO ops.job(workspace_id,job_type,payload,status,available_at,locked_until)
                VALUES($1,'runtime.test',jsonb_build_object('private',$2::text),$3,clock_timestamp()+$4::text::interval,clock_timestamp()+$5::text::interval)""", a, marker, state, available, lease)
        await connection.execute("INSERT INTO ops.job(workspace_id,job_type,payload,status) VALUES($1,'runtime.test','{}','dead_letter')", b)
        cid = uuid4()
        await connection.execute("""INSERT INTO config.feishu_connection(id,workspace_id,revision,enabled,validated_revision,settings,updated_by)
            VALUES($1,$2,1,true,1,jsonb_build_object('connection_id',$1::uuid::text,'workspace_id',$2::uuid::text,'private',$3::text),$4)""", cid, a, marker, actor)
        for state in ('pending','failed','running','dead_letter','succeeded'):
            eid = await connection.fetchval("""INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,status,available_at,locked_until)
                VALUES($1,$2,'customer',$3,$4,clock_timestamp()-interval '1 hour',clock_timestamp()-interval '1 hour') RETURNING id""", cid, a, uuid4(), state)
        await connection.execute("""INSERT INTO ops.feishu_delivery(dedupe_key,event_id,connection_id,workspace_id,config_revision,chat_id,payload,status)
            VALUES($1,$2,$3,$4,1,$1,jsonb_build_object('text',$1::text),'unknown')""", marker, eid, cid, a)
        await connection.execute('UPDATE control.tenant_storage_quota SET limit_bytes=50,stored_bytes=60,reserved_bytes=20 WHERE workspace_id=$1', a)
        for state in ('external_pending','stored','released'):
            await connection.execute("""INSERT INTO control.storage_allocation(import_id,workspace_id,actor_id,receipt,size_bytes,state,driver,profile,storage_key,filename,created_at)
                VALUES($1,$2,$3,$4,20,$5,'postgres','test',$6,$6,clock_timestamp()-interval '1 hour')""", uuid4(), a, actor, uuid4(), state, marker)
        event = uuid4()
        await connection.execute("INSERT INTO control.call_event(workspace_id,resource,operation_code,event_id,actor_id,state) VALUES($1,'ai_calls','runtime.test',$2,$3,'reserved')", a, event, actor)
        for state in ('reserved','dispatched','unknown','settled'):
            await connection.execute("""INSERT INTO control.call_attempt(invocation_id,workspace_id,resource,operation_code,event_id,receipt,state,created_at)
                VALUES($1,$2,'ai_calls','runtime.test',$3,$4,$5,clock_timestamp()-interval '1 hour')""", uuid4(), a, event, uuid4(), state)
        for kind, status, hours in [('provider_attempt','failed',1),('legacy_logical','failed',1),('provider_attempt','succeeded',1),('provider_attempt','failed',25)]:
            await connection.execute("""INSERT INTO agent.model_invocation(workspace_id,provider_code,model_id,endpoint_code,request_hash,status,record_kind,started_at,request_snapshot)
                VALUES($1,'synthetic','synthetic','synthetic',$2,$3,$4,clock_timestamp()-make_interval(hours=>$5),jsonb_build_object('text',$6::text))""", a, '0'*64, status, kind, hours, marker)
    page = await snapshot(client)
    assert marker not in str(page)
    row, other = page['items'][:2]
    assert row['jobs'] == dict(running=1, expired_leases=1, waiting=2, overdue=1, dead_letter=1)
    assert other['jobs'] == dict(running=0, expired_leases=0, waiting=0, overdue=0, dead_letter=1)
    assert row['sync'] == dict(enabled=True, waiting=2, overdue=2, expired_leases=1, dead_letter=1, unknown_deliveries=1)
    assert row['originals'] == dict(available=True, limit_bytes=50, stored_bytes=60, reserved_bytes=20, over_limit=True, pending=1, overdue=1)
    assert row['calls'] == dict(pending=2, overdue=2, unknown=1, failed_last_24h=1)
    assert other['calls'] == dict(pending=0, overdue=0, unknown=0, failed_last_24h=0)
    assert (await snapshot(client))['items'] == page['items']  # reads never repair or settle
    async with fixture_owner(connection):
        assert await connection.fetchval("SELECT count(*) FROM ops.job WHERE workspace_id=$1", a) == 6
        assert await connection.fetchval("SELECT count(*) FROM control.call_attempt WHERE workspace_id=$1 AND state='unknown'", a) == 1


async def test_runtime_overdue_excludes_future_work_disabled_modules_and_paused_companies(platform, connection):
    client, _ = platform
    async with fixture_owner(connection):
        for number, state in [(1,'active'),(2,'suspended'),(3,'closed'),(4,'provisioning')]:
            ws = await seed_company(connection, number, state)
            await connection.execute("INSERT INTO ops.job(workspace_id,job_type,payload,available_at) VALUES($1,'visit.import','{}',clock_timestamp()-interval '1 hour')", ws)
        await connection.execute("UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code='visit'", UUID(int=1))
    for row in (await snapshot(client))['items'][:4]:
        assert row['jobs']['waiting'] == 1 and row['jobs']['overdue'] == 0
    async with fixture_owner(connection):
        await connection.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1 AND module_code='visit'", UUID(int=1))
    assert (await snapshot(client))['items'][0]['jobs']['overdue'] == 1
    assert (await snapshot(client, wait_minutes=90))['items'][0]['jobs']['overdue'] == 0


async def test_runtime_requires_mfa_and_live_platform_identity_not_a_company_token(platform, connection):
    client, database = platform
    for params in ({'wait_minutes':0},{'wait_minutes':1441},{'wait_minutes':1.2},{'after_id':'bad'}):
        assert (await client.get(BASE+'/runtime', params=params)).status_code == 422
    async with database.connection() as conn:
        for table in ('ops.job','control.call_attempt','activity.visit_import','agent.model_invocation'):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.fetch('SELECT * FROM '+table)
        with pytest.raises(asyncpg.PostgresError):
            async with conn.transaction():
                await conn.fetchval("SELECT control.runtime_overview('forged',NULL,15)")
    async with await client_for(connection) as company:
        await sign_in(company,'ADMIN001')
        assert (await company.get(BASE+'/runtime')).status_code == 401
    async with fixture_owner(connection):
        await connection.execute('UPDATE control.session SET mfa_verified_at=NULL WHERE operator_id=$1', database.operator_id)
    assert (await client.get(BASE+'/runtime')).status_code == 403
    assert (await client.post(BASE+'/auth/logout')).status_code == 200
    assert (await client.get(BASE+'/runtime')).status_code == 401
    login = await client.post(BASE+'/auth/login',json={'account_code':database.account,'password':PASSWORD})
    assert login.status_code == 403, login.text
    assert (await client.get(BASE+'/runtime')).status_code == 401


async def test_runtime_rejects_revoked_platform_operator(platform, connection):
    client, database = platform
    assert (await client.get(BASE+'/runtime')).status_code == 200
    async with fixture_owner(connection):
        await connection.execute('UPDATE control.operator SET enabled=false WHERE id=$1', database.operator_id)
    assert (await client.get(BASE+'/runtime')).status_code == 401
