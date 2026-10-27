"""Eight focused V288 scenarios in an isolated Unix-only PostgreSQL 16 cluster.

PG_BIN=/path/to/pg16/bin python database/tests/verify_task_execution_v288_postgres.py
No application DSN, private settings, production records or external services.
"""
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'database/scripts'), str(ROOT / 'tools/installation')]
import migrate
from runtime_roles import load_policy, fingerprint, SCHEMAS
from upgrade_permissions import reconcile_service_roles

PORT = 56488
API_FUNCTIONS = [
    'workflow.task_execution_initialize_v3(uuid,jsonb)',
    'workflow.task_execution_command_v3(uuid,text,jsonb,uuid)',
    'workflow.task_execution_adopt_v3(uuid,jsonb,uuid)',
    'workflow.task_execution_adoption_v3(uuid)',
]


async def verify(socket):
    c = await asyncpg.connect(host=str(socket), port=PORT, user='task_test_owner', database='postgres', command_timeout=120)
    await c.set_type_codec('jsonb', encoder=json.dumps, decoder=json.loads, schema='pg_catalog')
    for role in ['salegent_feishu_worker', 'salegent_api', 'salegent_worker', 'salegent_platform']:
        await c.execute(f'CREATE ROLE {role} NOLOGIN')
    await migrate.migrate(c, ROOT / 'database', adopt_existing=True)
    await c.execute('SET row_security=on')
    async with c.transaction():
        p = load_policy()
        p['schema'] = 'V288'
        p['migration_fingerprint'] = fingerprint(dict(await c.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum')))
        p['services']['api']['functions'] = sorted(set(p['services']['api']['functions'] + API_FUNCTIONS))
        roles = {svc: 'task_test_' + svc for svc in p['services']}
        for svc, role in roles.items():
            await c.execute(f'CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS')
            for membership in p['services'][svc]['memberships']:
                await c.execute(f'GRANT {membership} TO {role} WITH INHERIT FALSE, SET FALSE')
        tables = [dict(x) for x in await c.fetch("SELECT n.nspname||'.'||c.relname AS name,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p') ORDER BY 1", SCHEMAS)]
        policies = [dict(x) for x in await c.fetch('SELECT schemaname,tablename,policyname,permissive,roles::text[],cmd,qual,with_check FROM pg_policies WHERE schemaname=ANY($1::text[]) ORDER BY 1,2,3', SCHEMAS)]
        for row in policies:
            row['roles'] = sorted(v for v in row['roles'] if v not in roles.values())
        p['rls_fingerprint'] = fingerprint({'tables': tables, 'policies': policies})
        await reconcile_service_roles(c, roles, p)
    print('Exact four-service permissions verified', flush=True)
    ws, foreign_ws, team = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    users = [uuid.uuid4() for _ in range(3)]
    await c.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'V288 synthetic'),($3,$4,'Other synthetic')", ws, 'task-v288-' + ws.hex, foreign_ws, 'task-v288-' + foreign_ws.hex)
    await c.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'synthetic','Synthetic team')", team, ws)
    for i, user in enumerate(users):
        await c.execute('INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$3,$3)', user, ws, 'USER' + str(i))
        await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'manager','workspace',$3)", ws, user, team)
        await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'manager')", ws, user, team)

    async def ctx(user, workspace=ws):
        await c.execute('SET LOCAL ROLE task_test_api')
        await c.execute("SELECT set_config('app.workspace_id',$1,true),set_config('app.user_ref_id',$2,true),set_config('app.role_code','manager',true),set_config('app.authorization_context','',true)", str(workspace), str(user))

    async def read(task, user=users[0]):
        async with c.transaction():
            await ctx(user)
            return await c.fetchval('SELECT workflow.task_execution_read($1)', task)

    async def create(owner=None, protocol=3):
        owner = owner or users[0]
        task = uuid.uuid4()
        async with c.transaction():
            await ctx(users[0])
            due = await c.fetchval("SELECT clock_timestamp()+interval '7 days'")
            status = 'in_progress' if protocol == 3 else 'pending_confirm'
            await c.execute("INSERT INTO workflow.task(id,workspace_id,creator_user_ref_id,creator_team_id,title,description,due_at,association_kind,source_code,status) VALUES($1,$2,$3,$4,'Synthetic task','Synthetic description',$5,'daily','mini_program',$6)", task, ws, users[0], team, due, status)
            await c.execute("INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility) VALUES($1,$2,$3,$4,'manager','owner')", task, ws, owner, team)
            if protocol == 3:
                await c.fetchval('SELECT workflow.task_execution_initialize_v3($1,$2)', task, {})
            elif protocol == 2:
                await c.fetchval('SELECT workflow.task_execution_initialize_v2($1,$2,$3)', task,
                    {'requirements': {'title': 'Synthetic task', 'description': 'Synthetic description', 'deliverable': 'real result', 'acceptance_criteria': 'review result', 'due_at': due.isoformat(), 'priority_code': 'normal'}, 'checklist': []}, owner == users[0])
        return task, due

    async def command(task, user, action, payload, key=None, body=None):
        async with c.transaction():
            await ctx(user)
            data = None if body else await c.fetchval('SELECT workflow.task_execution_read($1)', task)
            body = body or {**{k: data[k] for k in ['version_no', 'requirements_version', 'responsibility_version']}, **payload}
            result = await c.fetchval('SELECT workflow.task_execution_command_v3($1,$2,$3,$4)', task, action, body, key or uuid.uuid4())
            return result, body

    async def reject(awaitable, code=None):
        try:
            async with c.transaction():
                await awaitable
        except (asyncpg.SerializationError, asyncpg.InsufficientPrivilegeError, asyncpg.InvalidParameterValueError, asyncpg.NoDataFoundError) as exc:
            if code:
                assert code in str(exc), (code, str(exc))
        else:
            raise AssertionError('Unexpectedly allowed forbidden/stale command')

    # 1. Self creation and completion are real events, never accept/start/review.
    task, due = await create()
    data = await read(task)
    assert data['status'] == 'in_progress' and data['first_started_at'] is None and data['progress'] is None
    assert await c.fetchval('SELECT accepted_at FROM workflow.task WHERE id=$1', task) is None
    rr, _ = await command(task, users[0], 'append_feedback', {'text': 'Talked to customer', 'next_step': 'Send a proposal'})
    stale = {k: data[k] for k in ['version_no', 'requirements_version', 'responsibility_version']}
    await reject(command(task, users[0], 'pause', {}, body={**stale, 'reason': 'Stale form'}), 'TASK_EXECUTION_VERSION_CONFLICT')
    rr, _ = await command(task, users[0], 'submit_completion', {'note': 'Proposal delivered'})
    assert rr['execution']['status'] == 'completed' and rr['execution']['completion_action'] == 'submit_completion'
    assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM workflow.task_event WHERE task_id=$1 AND event_type IN ('accept','review_completion'))", task)
    assert not await c.fetchval('SELECT EXISTS(SELECT 1 FROM workflow.notification WHERE object_id=$1)', task)
    print('1 self flow, no invented acceptance/start/review or self notification', flush=True)

    # 2. Nonself review rounds remain immutable and stale rounds cannot be approved.
    task, due = await create(users[1])
    rr, _ = await command(task, users[1], 'submit_completion', {'note': 'Round one'})
    first = rr['execution']['current_submission']
    assert rr['execution']['processing_reason'] == 'awaiting_review' and rr['execution']['status'] == 'needs_adjustment'
    await command(task, users[1], 'append_feedback', {'text': 'Additional context'})
    assert await c.fetchval('SELECT snapshot->>\'note\' FROM workflow.task_execution_submission WHERE id=$1', uuid.UUID(first)) == 'Round one'
    await reject(command(task, users[1], 'pause', {'reason': 'hide review'}), 'TASK_EXECUTION_ACTION_UNAVAILABLE')
    await reject(command(task, users[1], 'close', {'reason': 'bypass creator'}), 'TASK_EXECUTION_FORBIDDEN')
    await command(task, users[0], 'review_completion', {'submission_id': first, 'decision': 'reject', 'note': 'Need full result'})
    rr, _ = await command(task, users[1], 'submit_completion', {'note': 'Round two'})
    second = rr['execution']['current_submission']
    await reject(command(task, users[0], 'review_completion', {'submission_id': first, 'decision': 'approve'}), 'TASK_SUBMISSION_STALE')
    rr, _ = await command(task, users[0], 'review_completion', {'submission_id': second, 'decision': 'approve'})
    assert rr['execution']['status'] == 'completed'
    assert await c.fetchval('SELECT count(*) FROM workflow.task_execution_submission WHERE task_id=$1', task) == 2
    print('2 nonself rejection/resubmission, immutable snapshots and stale review', flush=True)

    # 3. Delay and pause are explicit; feedback preserves reasons/due date.
    task, due = await create(users[1])
    delayed = due + timedelta(days=2)
    rr, _ = await command(task, users[1], 'defer', {'reason': 'Customer rescheduled', 'due_at': delayed.isoformat()})
    delay = rr['execution']['defer']
    rr, _ = await command(task, users[1], 'append_feedback', {'text': 'Still waiting'})
    assert rr['execution']['defer'] == delay and rr['execution']['status'] == 'deferred'
    await command(task, users[0], 'pause', {'reason': 'Await dependency'})
    await reject(command(task, users[1], 'submit_completion', {'note': 'Pretend done'}), 'TASK_EXECUTION_ACTION_UNAVAILABLE')
    await command(task, users[1], 'resume', {'reason': 'Dependency ready'})
    assert await c.fetchval('SELECT due_at FROM workflow.task WHERE id=$1', task) == delayed
    await command(task, users[1], 'request_adjustment', {'reason': 'Need new scope'})
    await reject(command(task, users[1], 'resume', {'reason': 'Skip creator'}), 'TASK_EXECUTION_ACTION_UNAVAILABLE')
    await command(task, users[0], 'continue_original', {})
    print('3 explicit delay/pause/resume and adjustment decision boundary', flush=True)

    # 4. Same-value saves do not reset review, actual revisions do.
    rr, _ = await command(task, users[1], 'submit_completion', {'note': 'Before revision'})
    sid = rr['execution']['current_submission']
    rr, _ = await command(task, users[0], 'revise', {'description': 'Synthetic description'})
    assert not rr['changed'] and rr['execution']['current_submission'] == sid
    rr, _ = await command(task, users[0], 'revise', {'description': 'Changed requirement', 'executor_user_id': str(users[2])})
    assert rr['execution']['status'] == 'in_progress' and rr['execution']['current_submission'] is None
    await reject(command(task, users[1], 'submit_completion', {'note': 'Former owner'}), 'TASK_EXECUTION_FORBIDDEN')
    rr, _ = await command(task, users[0], 'revise', {'executor_user_id': str(users[0])})
    assert rr['execution']['self_managed'] and rr['execution']['status'] == 'in_progress'
    print('4 no-op vs actual revisions, direct reassignment and self-mode recompute', flush=True)

    # 5. Identical key/body replays without duplicate events or terminal counts.
    key = uuid.uuid4()
    rr, body = await command(task, users[0], 'submit_completion', {'note': 'Final result'}, key)
    replay, _ = await command(task, users[0], 'submit_completion', {}, key, body)
    assert replay == rr
    await reject(command(task, users[0], 'submit_completion', {}, key, {**body, 'note': 'Different'}), 'TASK_EXECUTION_IDEMPOTENCY_CONFLICT')
    assert await c.fetchval("SELECT count(*) FROM workflow.task_event WHERE task_id=$1 AND to_status='completed'", task) == 1
    print('5 idempotent terminal replay and receipt mismatch rejection', flush=True)

    # 6. SQL guards, old command routes, tenant boundary, and live deny/revocation.
    task, _ = await create(users[1])
    async with c.transaction():
        await ctx(users[0])
        async with c.transaction():
            await reject(c.execute("UPDATE workflow.task SET status='completed' WHERE id=$1", task), 'TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED')
    async with c.transaction():
        await ctx(users[0])
        async with c.transaction():
            await reject(c.fetchval('SELECT workflow.task_execution_command($1,$2,$3,$4)', task, 'cancel_task', {}, uuid.uuid4()))
    async with c.transaction():
        await ctx(users[0], foreign_ws)
        async with c.transaction():
            await reject(c.fetchval('SELECT workflow.task_execution_read($1)', task))
    data = await read(task)
    await c.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1", users[1])
    await reject(command(task, users[1], 'append_feedback', {'text': 'Inactive', 'next_step': 'None'}), 'TASK_EXECUTION_FORBIDDEN')
    await c.execute("UPDATE platform.user_ref SET status='active' WHERE id=$1", users[1])
    async with c.transaction():
        await ctx(users[0])
        grants = await c.fetchval('SELECT jsonb_agg(to_jsonb(g)) FROM security.authorization_current_grants() g')
        grants = [g for g in grants if g['permission_code'] != 'task.cancel'] + [{'permission_code':'task.cancel','effect':'deny','scope_code':None,'team_ids':[],'source_code':'synthetic','source_id':'test'}]
        await c.execute("SELECT set_config('app.authorization_context',$1,true)", json.dumps({'workspace_id':str(ws),'user_id':str(users[0]),'grants':grants}))
        assert not (await c.fetchval('SELECT workflow.task_execution_read($1)', task))['capabilities']['close']
    print('6 legacy SQL/API guard, tenant isolation and current account/deny checks', flush=True)

    # 7. Explicit adoption preserves legacy result/reviewer and rejects self laundering.
    legacy, _ = await create(users[1], protocol=0)
    await c.execute("UPDATE workflow.task SET status='pending_review',completion_submitted_at=clock_timestamp(),completion_note='Historic submission' WHERE id=$1", legacy)
    async with c.transaction():
        await ctx(users[0])
        eligibility = await c.fetchval('SELECT workflow.task_execution_adoption_v3($1)', legacy)
        assert eligibility['available']
        rr = await c.fetchval('SELECT workflow.task_execution_adopt_v3($1,$2,$3)', legacy, {k: eligibility[k] for k in ['version_no','requirements_version','responsibility_version']}, uuid.uuid4())
    assert rr['execution']['processing_reason'] == 'awaiting_review' and rr['execution']['status'] == 'needs_adjustment'
    await command(legacy, users[0], 'review_completion', {'submission_id': rr['execution']['current_submission'], 'decision': 'approve'})
    old_self, _ = await create(protocol=0)
    await c.execute("UPDATE workflow.task SET status='pending_review',completion_submitted_at=clock_timestamp(),completion_note='Historic self' WHERE id=$1", old_self)
    async with c.transaction():
        await ctx(users[0])
        eligibility = await c.fetchval('SELECT workflow.task_execution_adoption_v3($1)', old_self)
        assert not eligibility['available'] and eligibility['reason'] == 'TASK_LEGACY_REVIEW_REQUIRED'
    old_v2, _ = await create(protocol=2)
    old = await read(old_v2)
    assert old['protocol_version'] == 2 and old['status'] == 'pending_execution'
    async with c.transaction():
        await ctx(users[0])
        old_reply = await c.fetchval('SELECT workflow.task_execution_command($1,$2,$3,$4)', old_v2, 'append_feedback', {**{k: old[k] for k in ['version_no','requirements_version','responsibility_version','checklist_revision']}, 'text': 'Original V2 still works'}, uuid.uuid4())
        assert old_reply['execution']['protocol_version'] == 2
    print('7 explicit historical review preservation, no self laundering, V2 unchanged', flush=True)

    # 8. Close is terminal but has no invented approval or performance result.
    rr, _ = await command(task, users[0], 'close', {'reason': 'Customer cancelled project'})
    assert rr['execution']['completion_action'] == 'close' and rr['execution']['completion_result'] == 'Customer cancelled project'
    row = await c.fetchrow("SELECT status,verification_status FROM workflow.task WHERE id=$1", task)
    assert dict(row) == {'status': 'completed', 'verification_status': 'not_required'}
    assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM workflow.task_event WHERE task_id=$1 AND event_type='review_completion')", task)
    assert not await c.fetchval('SELECT EXISTS(SELECT 1 FROM workflow.notification n JOIN workflow.task_execution_event e ON e.id::text=n.payload->>\'event_id\' WHERE n.recipient_user_ref_id=e.actor_user_ref_id)')
    async with c.transaction():
        await c.execute('SET LOCAL ROLE task_test_worker')
        await c.fetchval('SELECT workflow.task_execution_enqueue_pause_rechecks(100)')
        await c.fetchval('SELECT workflow.task_execution_enqueue_coordination_checks(100)')
    print('8 close evidence, no self notifications and existing worker compatibility', flush=True)
    print(json.dumps({'migration_fingerprint':p['migration_fingerprint'], 'rls_fingerprint':p['rls_fingerprint'], 'schema':p['schema']}, ensure_ascii=False), flush=True)
    await c.close()


def run():
    binary = Path(os.environ['PG_BIN'])
    socket = Path(tempfile.mkdtemp(prefix='sb-task-v288-'))
    subprocess.run([str(binary/'initdb'), '-D', str(socket/'pg'), '-U', 'task_test_owner', '--auth=trust', '--encoding=UTF8', '--no-locale'], check=True, stdout=subprocess.DEVNULL)
    subprocess.run([str(binary/'pg_ctl'), '-D', str(socket/'pg'), '-l', str(socket/'postgres.log'), '-o', f"-k {socket} -p {PORT} -c listen_addresses=''", '-w', 'start'], check=True, stdout=subprocess.DEVNULL)
    keep = os.environ.get('KEEP_TASK_PG') == '1'
    try:
        asyncio.run(verify(socket))
        if keep:
            print(f'Synthetic PG retained: socket={socket} port={PORT} database=postgres operator=task_test_owner api=task_test_api', flush=True)
    finally:
        if not keep:
            subprocess.run([str(binary/'pg_ctl'), '-D', str(socket/'pg'), '-m', 'fast', '-w', 'stop'], check=True, stdout=subprocess.DEVNULL)
            shutil.rmtree(socket)


if __name__ == '__main__':
    run()
