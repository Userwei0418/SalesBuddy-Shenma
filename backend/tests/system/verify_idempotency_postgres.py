"""Concurrent real task creation/replay/rollback in a disposable PostgreSQL database."""
import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'backend/src'), str(ROOT/'database/scripts')]
from migrate import migrate
from verify_jobs_postgres import settings

from sales_backend.db import Database
from sales_backend.domain.agent import ActorContext
from sales_backend.services.idempotency import IdempotencyConflict, execute_mutation
from sales_backend.services.tasks import TaskService


async def main():
    config = {'host': os.environ.get('PGHOST','/tmp'), 'user': os.environ.get('PGUSER','postgres')}
    admin = await asyncpg.connect(database='postgres', **config)
    name = 'salegent_verify_idempotency_'+uuid.uuid4().hex[:12]
    conn = db = None
    checks=[]
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        conn = await asyncpg.connect(database=name, **config)
        await migrate(conn)
        workspace = uuid.uuid4()
        await conn.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'事务测试')",workspace,str(workspace))
        users=[]
        for code,role in [('MANAGER','manager'),('SALES','sales')]:
            user=uuid.uuid4();users.append(user)
            await conn.execute('''INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name)
                VALUES($1,$2,$3,$3,$3)''',user,workspace,code)
            await conn.execute('''INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code)
                VALUES($1,$2,$3,$4)''',workspace,user,role,'workspace' if role=='manager' else 'self')
        actor=ActorContext(workspace_id=str(workspace),user_id=str(users[0]),role='manager',data_scope='workspace')
        recipient=ActorContext(workspace_id=str(workspace),user_id=str(users[1]),role='sales',data_scope='self')
        db=Database(settings(name));await db.connect()
        data=dict(description='真实幂等测试任务',assignee_account_code='SALES',due_at=datetime.now(UTC)+timedelta(days=2),priority_code='normal')
        async def create(key,payload=None,fail=False):
            payload=payload or data
            async with db.transaction(actor) as c:
                async def write():
                    result=await TaskService().create(c,actor=actor,**payload)
                    if fail: raise RuntimeError('intentional failure after business SQL')
                    return result
                return await execute_mutation(c,actor,key,'tasks.create',payload,write)
        key=uuid.uuid4()
        a,b=await asyncio.gather(create(key),create(key))
        assert a==b
        assert await conn.fetchval('SELECT count(*) FROM workflow.task')==1
        assert await conn.fetchval('SELECT count(*) FROM workflow.notification')==1
        checks.append('concurrent_create_one_task_one_reminder_same_response')
        await db.close();db=Database(settings(name));await db.connect()
        assert await create(key)==a
        checks.append('response_lost_replay_after_connection_pool_restart')
        try:
            await create(key,{**data,'description':'changed'})
            raise AssertionError('different payload accepted')
        except IdempotencyConflict: pass
        assert await conn.fetchval('SELECT count(*) FROM workflow.task')==1
        checks.append('same_key_changed_payload_conflict')
        second=await create(uuid.uuid4());assert second['id']!=a['id']
        checks.append('intentional_identical_new_task_not_suppressed')
        failed_key=uuid.uuid4()
        try: await create(failed_key,fail=True)
        except RuntimeError: pass
        assert await conn.fetchval('SELECT count(*) FROM workflow.task')==2
        assert await conn.fetchval('SELECT count(*) FROM ops.mutation_receipt WHERE request_key=$1',failed_key)==0
        await create(failed_key)
        assert await conn.fetchval('SELECT count(*) FROM workflow.task')==3
        checks.append('business_and_receipt_rollback_then_retry')
        event_key=uuid.uuid4()
        async def accept():
            async with db.transaction(recipient) as c:
                return await execute_mutation(c,recipient,event_key,'tasks.events:'+a['id'],{'event_type':'accept'},
                    lambda:TaskService().apply_event(c,actor=recipient,task_id=a['id'],event_type='accept',note=None))
        accepted,replayed=await asyncio.gather(accept(),accept())
        assert accepted==replayed and accepted['status']=='pending_execution'
        assert await conn.fetchval("SELECT count(*) FROM workflow.task_event WHERE task_id=$1::uuid AND event_type='accepted'",a['id']) in (0,1)
        assert await conn.fetchval("SELECT count(*) FROM workflow.task_event WHERE task_id=$1::uuid",a['id'])==2
        checks.append('task_accept_retry_replays_without_duplicate_event')
        print(json.dumps({'passed':len(checks),'checks':checks}))
    finally:
        if db: await db.close()
        if conn: await conn.close()
        assert name.startswith('salegent_verify_')
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()

if __name__=='__main__': asyncio.run(main())
