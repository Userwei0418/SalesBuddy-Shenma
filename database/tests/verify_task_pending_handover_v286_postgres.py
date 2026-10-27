"""One focused pending-handover eligibility case in a disposable Unix-only PG16.

PG_BIN=/path/to/pg16/bin python database/tests/verify_task_pending_handover_v286_postgres.py
Reuses fixture/role setup only; does not run the V285 business scenario matrix.
"""
from verify_task_execution_v285_postgres import run


async def verify(c, workspace, users, create, read, command):
    task, _ = await create(users[0])
    response, _ = await command(task, users[0], 'initiate_executor_handover', {
        'target_user_id': str(users[1]), 'reason': 'synthetic handover',
    })
    execution = response['execution']
    assert execution['status'] == 'pending_confirm'
    assert execution['execution_handover']['status'] == 'pending'
    assert not execution['coordination_required'].get('executor_unavailable')

    async def business_snapshot():
        return await c.fetchval("""
            SELECT jsonb_build_object(
                'task', to_jsonb(t),
                'execution', (SELECT to_jsonb(e) FROM workflow.task_execution e WHERE e.task_id=t.id),
                'assignees', (SELECT jsonb_agg(to_jsonb(a) ORDER BY to_jsonb(a)::text)
                    FROM workflow.task_assignee a WHERE a.task_id=t.id),
                'events', (SELECT jsonb_agg(to_jsonb(e) ORDER BY to_jsonb(e)::text)
                    FROM workflow.task_execution_event e WHERE e.task_id=t.id)
            ) FROM workflow.task t WHERE t.id=$1
        """, task)

    before = await business_snapshot()
    await c.execute("""INSERT INTO config.account_authorization(workspace_id,user_ref_id)
        VALUES($1,$2) ON CONFLICT DO NOTHING""", workspace, users[1])
    # Both variants retain task.accept: the V285 pre-acceptance owner check alone
    # could not notice these losses. Changes affect only synthetic fixture grants.
    for permission in ('task.complete', 'task.read'):
        await c.execute("""DELETE FROM config.account_permission_override
            WHERE workspace_id=$1 AND user_ref_id=$2""", workspace, users[1])
        await c.execute("""INSERT INTO config.account_permission_override
            (workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,$3,'deny')
        """, workspace, users[1], permission)
        async with c.transaction():
            await c.execute("SELECT set_config('app.workspace_id',$1,true)", str(workspace))
            assert await c.fetchval("SELECT workflow.task_execution_user_allows($1,$2,'task.accept',$2,true)", task, users[1])
            assert not await c.fetchval("SELECT workflow.task_execution_user_allows($1,$2,$3,$2,true)", task, users[1], permission)
        execution = await read(task, users[0])
        assert execution['coordination_required']['executor_unavailable'] is True
        assert execution['status'] == 'pending_confirm'
        assert execution['execution_handover']['status'] == 'pending'
        async with c.transaction():
            await c.execute('SET LOCAL ROLE task_test_worker')
            for _ in range(2):
                await c.fetchval('SELECT workflow.task_execution_enqueue_coordination_checks(100)')
        notices = await c.fetch("""SELECT channel_code,payload FROM workflow.notification
            WHERE workspace_id=$1 AND object_id=$2 AND template_code='task_coordination_required'
              AND status<>'cancelled'""", workspace, task)
        assert notices and all(n['channel_code'] == 'in_app' for n in notices)
        assert all(n['payload']['issues']['executor_unavailable'] is True for n in notices)
        assert await business_snapshot() == before
        print(f'Pending target {permission} deny: visible coordination + in-app notice; task/state/history unchanged', flush=True)

    await c.execute("""DELETE FROM config.account_permission_override
        WHERE workspace_id=$1 AND user_ref_id=$2""", workspace, users[1])
    assert not (await read(task, users[0]))['coordination_required'].get('executor_unavailable')
    assert await business_snapshot() == before


if __name__ == '__main__':
    run(verify)
