"""V262 upgrade and dual protocol proof on an explicitly private disposable PG.

Synthetic ASGI identities exercise real restricted RLS; no JWT/UI/production claim.
V259/V260/V261 are independent and deliberately outside this focused ledger.
"""
# ruff: noqa: E402, E501, S608, S108
import argparse
import asyncio
import hashlib
import io
import json
import sys
from contextlib import asynccontextmanager, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import httpx
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend/scripts'))
from verify_review_targets_postgres import apply, grants, migrate_through
from verify_review_targets_postgres import item as migration_item

from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.idempotency import idempotency_conflict_handler
from sales_backend.api.task_execution import router as execution_router
from sales_backend.api.tasks import router as task_router
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.repositories.notifications import NotificationRepository
from sales_backend.services.idempotency import IdempotencyConflict
from sales_backend.services.tasks import TaskService


def checklist():
    return [{'item_id': str(uuid4()), 'definition_version': 1, 'title': '核对要求', 'acceptance_criteria': '人工确认结果'}]


async def preserved_rows(c, *, classified=False):
    """Compare all pre-existing fields, not just the execution JSON state."""
    task_row = "to_jsonb(x)-ARRAY['execution_mode','execution_origin_xid']" if classified else 'to_jsonb(x)'
    rows = {'task': await c.fetchval(f'SELECT jsonb_agg({task_row} ORDER BY id) FROM workflow.task x')}
    for table in ('task_execution', 'task_execution_event', 'task_execution_request', 'task_execution_submission', 'task_event'):
        rows[table] = await c.fetchval(f'SELECT jsonb_agg(to_jsonb(x) ORDER BY to_jsonb(x)::text) FROM workflow.{table} x')
    return rows


async def task_triggers(c):
    return [dict(r) for r in await c.fetch("""SELECT tgname,tgenabled::text,pg_get_triggerdef(oid) AS definition
        FROM pg_trigger WHERE tgrelid='workflow.task'::regclass AND NOT tgisinternal ORDER BY tgname""")]


async def verify_migration_boundary(c, migration_path, report):
    original_rows = await preserved_rows(c)
    original_triggers = await task_triggers(c)
    touch = next(t for t in original_triggers if t['tgname'] == 'trg_task_touch')
    assert touch['definition'] == 'CREATE TRIGGER trg_task_touch BEFORE UPDATE ON workflow.task FOR EACH ROW EXECUTE FUNCTION common.touch_updated_at()'
    report['samples']['migration_actor'] = dict(await c.fetchrow("""SELECT current_user,
        pg_has_role(current_user,c.relowner,'MEMBER') AS owns_task,
        r.rolsuper FROM pg_class c JOIN pg_roles r ON r.rolname=current_user WHERE c.oid='workflow.task'::regclass"""))
    assert report['samples']['migration_actor']['owns_task']
    report['samples']['touch_function'] = await c.fetchval("SELECT pg_get_functiondef('common.touch_updated_at()'::regprocedure)")
    report['samples']['task_triggers_before'] = original_triggers
    report['samples']['before_rows_sha256'] = hashlib.sha256(json.dumps(original_rows, sort_keys=True).encode()).hexdigest()
    report['samples']['preserved_table_counts'] = {table: len(rows or []) for table, rows in original_rows.items()}
    report['samples']['task_versions_before'] = [{key: row[key] for key in ('id', 'version_no', 'updated_at')} for row in original_rows['task']]
    outcomes = []
    for state, mode in (('O', 'ENABLE'), ('D', 'DISABLE'), ('R', 'ENABLE REPLICA'), ('A', 'ENABLE ALWAYS')):
        trial = c.transaction()
        await trial.start()
        try:
            await c.execute(f'ALTER TABLE workflow.task {mode} TRIGGER trg_task_touch')
            expected_triggers = await task_triggers(c)
            await apply(c, migration_item('V262', migration_path), ROOT/'database')
            assert await preserved_rows(c, classified=True) == original_rows, f'business/history mutation with trigger {state}'
            after_triggers = await task_triggers(c)
            assert {t['tgname']: t['tgenabled'] for t in after_triggers} == {t['tgname']: t['tgenabled'] for t in expected_triggers}, f'trigger state not restored: {state}'
            # V262 intentionally extends the mode guard to INSERT; no other
            # trigger definition is part of that schema change.
            assert [t for t in after_triggers if t['tgname'] != 'a_task_execution_write_guard'] == [t for t in expected_triggers if t['tgname'] != 'a_task_execution_write_guard']
            outcomes.append({'original_touch_state': state, 'all_business_rows_unchanged': True, 'all_trigger_states_restored': True})
        finally:
            await trial.rollback()
    assert await task_triggers(c) == original_triggers
    trial = c.transaction()
    await trial.start()
    try:
        await c.execute('ALTER TRIGGER trg_task_touch ON workflow.task RENAME TO unexpected_task_touch')
        try:
            await apply(c, migration_item('V262', migration_path), ROOT/'database')
        except asyncpg.RaiseError as exc:
            assert 'V262_UNKNOWN_TASK_TOUCH_TRIGGER' in str(exc)
        else:
            raise AssertionError('unknown touch trigger was silently bypassed')
    finally:
        await trial.rollback()
    assert await task_triggers(c) == original_triggers
    assert await preserved_rows(c) == original_rows
    report['samples']['migration_trigger_variants'] = outcomes
    report['checks'].append('classification preserves every existing workflow.task field (including version_no/updated_at), execution states/requests/submissions/events and old task events; O/D/R/A touch states restored; unknown touch fails atomically')


async def verify_waiting_revocation(c, config, name, role, actors, report):
    """A separate real HTTP transaction waits while an admin commits revocation."""
    request_c = await asyncpg.connect(database=name, **config)
    for typ in ('json', 'jsonb'):
        await request_c.set_type_codec(typ, schema='pg_catalog', encoder=json.dumps, decoder=json.loads, format='text')
    await request_c.execute('SET row_security=on')
    actor = actors['executor']
    ws, uid = actor.workspace_id, actor.user_id
    await c.execute('INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2) ON CONFLICT DO NOTHING', ws, uid)

    class DB:
        @asynccontextmanager
        async def transaction(self, request_actor, readonly=False, isolation=None):
            async with request_c.transaction(readonly=readonly, isolation=isolation):
                await request_c.execute(f'SET LOCAL ROLE "{role}"')
                await set_request_context(request_c, request_actor)
                assert not await request_c.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
                yield request_c

    app = FastAPI()
    app.include_router(task_router)
    app.add_exception_handler(IdempotencyConflict, idempotency_conflict_handler)
    app.dependency_overrides[get_database] = DB
    app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=actor)
    cases = []
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated') as client:
            for has_receipt in (True, False):
                async with DB().transaction(actors['creator']) as tx:
                    created = await TaskService().create(tx, actor=actors['creator'], description='并发撤权兼容性任务', due_at=datetime.now(UTC)+timedelta(days=2), priority_code='normal', assignee_account_code='executor')
                tid, key = created['id'], str(uuid4())
                body = {'event_type': 'accept', 'version_no': created['version_no'], 'note': '接受任务'}
                endpoint = f'/api/v1/tasks/{tid}/events'
                headers = {'Idempotency-Key': key}
                if has_receipt:
                    first = await client.post(endpoint, json=body, headers=headers)
                    assert first.status_code == 200, first.text
                    replay = await client.post(endpoint, json=body, headers=headers)
                    assert replay.status_code == 200 and replay.json() == first.json(), replay.text
                before = await preserved_rows(c, classified=True)
                receipts_before = await c.fetchval('SELECT count(*) FROM ops.mutation_receipt WHERE operation=$1 AND request_key=$2', f'tasks.events:{tid}', key)
                assert receipts_before == int(has_receipt)
                task = None
                try:
                    async with c.transaction():
                        lock = f'mutation:{ws}:{uid}:tasks.events:{tid}:{key}'
                        await c.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', lock)
                        task = asyncio.create_task(client.post(endpoint, json=body, headers=headers))
                        for _ in range(500):
                            if await c.fetchval("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1 AND locktype='advisory' AND NOT granted)", request_c.get_server_pid()):
                                break
                            if task.done():
                                response = await task
                                raise AssertionError(f'HTTP did not wait on lock: {response.status_code} {response.text}')
                            await asyncio.sleep(0.01)
                        else:
                            raise AssertionError('HTTP never reached advisory lock wait')
                        # This commit is after its initial permission check, before
                        # the waiter can obtain the receipt lock and return/write.
                        await c.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2,'task.accept','deny',NULL)", ws, uid)
                    response = await asyncio.wait_for(task, timeout=10)
                    assert response.status_code == 403, response.text
                    assert await preserved_rows(c, classified=True) == before
                    assert await c.fetchval('SELECT count(*) FROM ops.mutation_receipt WHERE operation=$1 AND request_key=$2', f'tasks.events:{tid}', key) == receipts_before
                    cases.append({'existing_receipt': has_receipt, 'advisory_wait_observed': True, 'revocation_committed_before_unlock': True, 'status': response.status_code, 'receipt_count': receipts_before, 'business_and_history_unchanged': True})
                finally:
                    if task is not None and not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    await c.execute("DELETE FROM config.account_permission_override WHERE workspace_id=$1 AND user_ref_id=$2 AND permission_code='task.accept'", ws, uid)
        report['samples']['concurrent_revocation'] = cases
        report['checks'].append('restricted real ASGI receipt replay and first mutation both reach advisory lock wait, then deny after committed concurrent revocation; neither changes task/history/receipt; authorized success and identical replay remain 200')
    finally:
        await request_c.close()


async def verify(args):
    socket = str(Path(args.socket).resolve())
    assert socket.startswith(('/tmp/sb122-task-pg.', '/private/tmp/sb122-task-pg.'))
    config = dict(host=socket, port=args.port, user='postgres', password='', passfile='/dev/null', ssl=False)
    admin = await asyncpg.connect(database='postgres', **config)
    name, role = 'sb262_' + uuid4().hex[:10], 'sb262_api_' + uuid4().hex[:10]
    report = {'status': 'started', 'production_accessed': False,
              'focus': args.focus,
              'schema': 'complete V258 + V262 focused ledger; not final release ledger',
              'http_auth': 'synthetic injected identity; actual NOSUPERUSER NOBYPASSRLS role', 'checks': [], 'samples': {}}
    c = None
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0 ENCODING \'UTF8\'')
        c = await asyncpg.connect(database=name, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(c, 256)
        for typ in ('json', 'jsonb'):
            await c.set_type_codec(typ, schema='pg_catalog', encoder=json.dumps, decoder=json.loads, format='text')
        ws, team = uuid4(), uuid4()
        await c.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Compat fixture')", ws, str(ws))
        await c.execute('UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1', ws)
        await c.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,'Compat team')", team, ws, str(team))
        actors = {}
        for label in ('creator', 'executor', 'replacement', 'outsider'):
            uid = uuid4()
            await c.execute('INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)', uid, ws, label)
            await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'operations','workspace',$3)", ws, uid, team)
            actors[label] = ActorContext(workspace_id=str(ws), user_id=str(uid), role='operations', data_scope='workspace', team_ids=[str(team)])

        async def seed(title, status='pending_confirm'):
            tid = await c.fetchval("INSERT INTO workflow.task(workspace_id,title,description,creator_user_ref_id,creator_team_id,status,due_at,association_kind,completion_note) VALUES($1,$2,$2,$3,$4,$5,clock_timestamp()+interval '10 days','daily','historic result') RETURNING id", ws, title, actors['creator'].user_id, team, status)
            await c.execute("INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility) VALUES($1,$2,$3,$4,'operations','owner')", tid, ws, actors['executor'].user_id, team)
            return tid

        legacy = await seed('pre-V257 active')
        review = await seed('pre-V257 pending-review', 'pending_review')
        cancelled = await seed('pre-V257 cancelled', 'cancelled')
        historical = await seed('pre-V257 old events')
        await c.execute("INSERT INTO workflow.task_event(workspace_id,task_id,event_type,actor_user_ref_id,note) VALUES($1,$2,'submit_completion',$3,'legacy event, not V1 evidence')", ws, historical, actors['executor'].user_id)
        await apply(c, migration_item('V257', ROOT/'database/migrations/V257__task_execution_lifecycle.sql'), ROOT/'database')
        await c.execute("INSERT INTO workflow.task_execution_event(task_id,workspace_id,actor_user_ref_id,kind,body) VALUES($1,$2,$3,'cancel_flows','{}')", cancelled, ws, actors['creator'].user_id)
        for permission in ('task.feedback', 'task.reviewer_handover'):
            await c.execute("INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code) SELECT workspace_id,id,$1,'workspace' FROM config.permission_role WHERE workspace_id=$2 AND builtin_role_code='operations'", permission, ws)
        # A real V257 initialized task (no command yet) must retain all definitions.
        async with c.transaction():
            await set_request_context(c, actors['creator'])
            preserved = await seed('V257 explicit human checklist')
            await c.fetchval('SELECT workflow.task_execution_initialize($1,$2::jsonb)', preserved, checklist())
        # A backfilled task that actually used V1 feedback also cannot downgrade.
        promoted = historical
        async with c.transaction():
            await set_request_context(c, actors['creator'])
            await c.fetchval('SELECT workflow.task_execution_command($1,\'append_feedback\',$2::jsonb,$3)', promoted,
                dict(version_no=1, requirements_version=1, responsibility_version=1, text='real V1 feedback'), uuid4())
        only_old_event = await seed('only old event after V257')
        await c.execute("INSERT INTO workflow.task_event(workspace_id,task_id,event_type,actor_user_ref_id,note) VALUES($1,$2,'complete',$3,'legacy complete event')", ws, only_old_event, actors['executor'].user_id)
        if args.focus == 'boundary':
            # Include nonempty immutable submission snapshots and command receipts
            # in the migration invariant, as well as states and event rows.
            async with c.transaction():
                await set_request_context(c, actors['executor'])
                async def command(action, **fields):
                    summary = await c.fetchval('SELECT workflow.task_execution_summary($1)', preserved)
                    body = {k: summary[k] for k in ('version_no', 'requirements_version', 'responsibility_version')}
                    return await c.fetchval('SELECT workflow.task_execution_command($1,$2,$3::jsonb,$4)', preserved, action, body | fields, uuid4())
                await command('accept_task', reason='保留历史验收')
                summary = await c.fetchval('SELECT workflow.task_execution_summary($1)', preserved)
                definition = summary['checklist'][0]
                await command('set_item_progress', item_id=definition['item_id'], definition_version=1, state='completed', note='迁移前完成')
                await command('submit_completion', note='迁移前提交成果')
            assert await c.fetchval('SELECT count(*) FROM workflow.task_execution_submission') > 0
            assert await c.fetchval('SELECT count(*) FROM workflow.task_execution_request') > 0
        before = await c.fetchval("SELECT jsonb_agg(to_jsonb(x) ORDER BY task_id) FROM workflow.task_execution x")
        history_before = await c.fetchval("SELECT jsonb_agg(to_jsonb(x) ORDER BY id) FROM workflow.task_execution_event x")
        await apply(c, migration_item('V258', ROOT/'database/migrations/V258__knowledge_materials.sql'), ROOT/'database')
        migration_path = ROOT/'database/migrations/V262__task_execution_legacy_compat.sql'
        if args.focus == 'boundary':
            await verify_migration_boundary(c, migration_path, report)
        task_rows_before = await preserved_rows(c)
        await apply(c, migration_item('V262', migration_path), ROOT/'database')
        task_rows_after = await preserved_rows(c, classified=True)
        assert task_rows_after == task_rows_before
        if args.focus == 'boundary':
            report['samples']['after_rows_sha256'] = hashlib.sha256(json.dumps(task_rows_after, sort_keys=True).encode()).hexdigest()
            report['samples']['task_versions_after'] = [{key: row[key] for key in ('id', 'version_no', 'updated_at')} for row in task_rows_after['task']]
        assert before == await c.fetchval("SELECT jsonb_agg(to_jsonb(x) ORDER BY task_id) FROM workflow.task_execution x")
        assert history_before == await c.fetchval("SELECT jsonb_agg(to_jsonb(x) ORDER BY id) FROM workflow.task_execution_event x")
        modes = {str(r['id']): r['execution_mode'] for r in await c.fetch('SELECT id,execution_mode FROM workflow.task')}
        assert all(modes[str(t)] == 'legacy' for t in (legacy, review, cancelled, only_old_event))
        assert all(modes[str(t)] == 'execution_v1' for t in (preserved, promoted))
        assert await c.fetchval("SELECT enabled FROM control.tenant_module WHERE workspace_id=$1 AND module_code='task_execution_v1'", ws) is False
        report['checks'].append('upgrade distinguishes empty V257 backfill/old task_event/cancel_flows from actual V1 initialization or feedback, preserving states and events byte-for-byte')
        if args.release_schema:
            assert args.focus == 'full', 'Release schema is only used for the HTTP regression, not the V262 migration boundary'
            with redirect_stdout(io.StringIO()):
                await migrate_through(c, 268)
            report['schema']='complete V268 release ledger; task HTTP regression only'
        await c.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE')
        await grants(c, role)
        await c.execute("SET row_security=on")
        if args.focus == 'boundary':
            await verify_waiting_revocation(c, config, name, role, actors, report)
            report['migration_sha256'] = hashlib.sha256(migration_path.read_bytes()).hexdigest()
            report['postgres_version'] = await c.fetchval('SHOW server_version')
            report['status'] = 'passed'
            print(json.dumps({k: v for k, v in report.items() if k != 'samples'}, ensure_ascii=False))
            return
        active = actors['creator']
        class DB:
            @asynccontextmanager
            async def transaction(self, actor, readonly=False, isolation=None):
                async with c.transaction(readonly=readonly, isolation=isolation):
                    await c.execute(f'SET LOCAL ROLE "{role}"')
                    await set_request_context(c, actor)
                    assert not await c.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
                    yield c
        db = DB()
        app = FastAPI()
        app.include_router(task_router)
        app.include_router(execution_router)
        app.add_exception_handler(IdempotencyConflict, idempotency_conflict_handler)
        app.dependency_overrides[get_database] = lambda: db
        app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=active)
        base = dict(description='核实兼容任务并反馈结果', assignee_account_code='executor', due_at=(datetime.now(UTC)+timedelta(days=10)).isoformat())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated') as client:
            async def request(method, url, body=None, expected=200, key=None):
                r = await client.request(method, url, json=body, headers={'Idempotency-Key': str(key or uuid4())})
                assert r.status_code == expected, (method, url, r.status_code, r.text)
                return r.json()
            async def old(tid, event, *, actor='executor', version=None, expected=200, key=None, **fields):
                nonlocal active
                active = actors[actor]
                if version is None:
                    version = await c.fetchval('SELECT version_no FROM workflow.task WHERE id=$1', tid)
                return await request('POST', f'/api/v1/tasks/{tid}/events', dict(event_type=event, note='人工确认结果', version_no=version, **fields), expected, key)
            async def new(tid, action, *, actor='executor', expected=200, **fields):
                nonlocal active
                active = actors[actor]
                state = await request('GET', f'/api/v1/tasks/{tid}/execution')
                return await request('POST', f'/api/v1/tasks/{tid}/execution/{action}',
                    {k: state[k] for k in ('version_no','requirements_version','responsibility_version')} | fields, expected)
            active = actors['creator']
            fresh = await request('POST', '/api/v1/tasks', base, 201)
            assert fresh['execution_mode']=='legacy' and fresh['execution'] is None and fresh['execution_protocol_version'] is None
            assert fresh['action_permissions']['task.coordinate'] is True
            task = fresh['id']
            create_key = uuid4()
            first = await request('POST','/api/v1/tasks',base,201,create_key)
            replay = await request('POST','/api/v1/tasks',base,201,create_key)
            assert first == replay
            await request('POST','/api/v1/tasks',base | {'description':'与前一次不同的创建请求'},409,create_key)
            batch = await request('POST','/api/v1/tasks/batch',{'tasks':[base,base|{'assignee_account_code':'replacement'}]},201)
            assert all(i['execution_mode']=='legacy' for i in batch['items'])
            await request('POST','/api/v1/tasks',base|{'execution_protocol_version':1,'checklist':checklist()},409)
            await request('POST','/api/v1/tasks',base|{'execution_protocol_version':1},422)
            report['checks'].append('old single and batch HTTP create succeed with rollout off; identical create receipt replays, conflicting receipt 409; explicit V1 off 409, incomplete V1 422')
            accept_key=uuid4()
            accepted=await old(legacy,'accept',key=accept_key,version=1)
            accepted_replay=await old(legacy,'accept',key=accept_key,version=1)
            assert accepted == accepted_replay
            await old(legacy,'complete',version=1,expected=409)
            await old(legacy,'complete',actor='outsider',expected=403)
            completed=await old(legacy,'complete')
            assert completed['status']=='pending_review' and completed['execution'] is None
            await old(legacy,'approve_completion',actor='executor',expected=403)
            rejected=await old(legacy,'reject_completion',actor='creator')
            assert rejected['status']=='in_progress'
            await old(legacy,'complete')
            approved=await old(legacy,'approve_completion',actor='creator')
            assert approved['status']=='completed'
            await old(review,'approve_completion',actor='creator')
            declined=await old(task,'reject')
            assert declined['status']=='cancelled'
            transfer=await old(first['id'],'reassign',actor='creator',assignee_account_code='replacement')
            assert transfer['status']=='pending_confirm' and transfer['execution_mode']=='legacy'
            await old(first['id'],'accept',actor='replacement')
            report['checks'].append('restricted legacy accept/reject/complete/approve/reject-completion/reassign preserve review, owner, old-version, receipt and original RBAC semantics; historical pending review remains reviewable')
            # New calls can neither upgrade a committed legacy task nor operate its V1 state.
            active=actors['executor']
            err=await request('POST',f'/api/v1/tasks/{first["id"]}/execution/accept_task',dict(version_no=1,requirements_version=1,responsibility_version=1,reason='accept'),409)
            assert err['detail']['code']=='TASK_EXECUTION_LEGACY_MODE'
            for sql, params in [
                ("UPDATE workflow.task SET execution_mode='execution_v1' WHERE id=$1",[first['id']]),
                ("UPDATE workflow.task SET execution_mode='legacy' WHERE id=$1",[preserved]),
                ("UPDATE workflow.task SET title='unsafe' WHERE id=$1",[preserved]),
            ]:
                try:
                    async with db.transaction(actors['creator']) as tx:
                        await tx.execute(sql,*params)
                except asyncpg.PostgresError as exc:
                    assert exc.sqlstate in ('40001','42501')
                else:
                    raise AssertionError('mode/protocol DML bypass')
            await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1 AND module_code='task_execution_v1'",ws)
            # Even a creator who rewrites the old timestamp cannot convert a
            # previously committed legacy row by making xmin look freshly inserted.
            try:
                async with db.transaction(actors['creator']) as tx:
                    await tx.execute("UPDATE workflow.task SET created_at=clock_timestamp(),status='pending_confirm' WHERE id=$1",first['id'])
                    await tx.fetchval("SELECT workflow.task_execution_initialize($1,$2::jsonb)",first['id'],checklist())
            except asyncpg.PostgresError as exc:
                assert exc.sqlstate=='42501' and 'TASK_INITIALIZE_FORBIDDEN' in str(exc)
            else:
                raise AssertionError('committed legacy row converted after timestamp rewrite')
            active=actors['creator']
            v1=await request('POST','/api/v1/tasks',base|{'execution_protocol_version':1,'checklist':checklist()},201)
            assert v1['execution_mode']=='execution_v1' and v1['execution_protocol_version']==1
            report['samples']['v1_created']=v1
            for event in ('accept','reject','complete','approve_completion','reject_completion','reassign'):
                await old(v1['id'],event,actor='creator',expected=409,**({'assignee_account_code':'replacement'} if event=='reassign' else {}))
            await c.execute("UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code='task_execution_v1'",ws)
            await new(v1['id'],'accept_task',reason='接受新协议任务')
            active=actors['executor']
            current=await request('GET',f'/api/v1/tasks/{v1["id"]}/execution')
            item=current['checklist'][0]
            await new(v1['id'],'set_item_progress',item_id=item['item_id'],definition_version=1,state='completed',note='成果已确认')
            await new(v1['id'],'submit_completion',note='交付结果')
            active=actors['creator']
            submitted=await request('GET',f'/api/v1/tasks/{v1["id"]}/execution')
            await new(v1['id'],'review_completion',actor='creator',submission_id=submitted['current_submission'],decision='approve',note='已验收',rework_item_ids=[])
            assert await c.fetchval('SELECT status FROM workflow.task WHERE id=$1',v1['id'])=='completed'
            report['checks'].append('V1 requires explicit enabled rollout; old six actions fence per-task only; disabling rollout does not prevent V1 acceptance/progress/submission/review; ordinary DML cannot change modes')
            # Real HTTP cancellation after acceptance used to clear accepted_at,
            # tripping the immutable V1 execution-field guard (SQLSTATE 40001).
            await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1 AND module_code='task_execution_v1'",ws)
            for paused in (False, True):
                active=actors['creator']
                definitions=checklist()+checklist()
                cancellable=await request('POST','/api/v1/tasks',base|{'execution_protocol_version':1,'checklist':definitions},201)
                tid=cancellable['id']
                accepted=await new(tid,'accept_task',reason='接收后核验取消')
                definition=accepted['execution']['checklist'][0]
                await new(tid,'set_item_progress',item_id=definition['item_id'],definition_version=definition['definition_version'],state='completed',note='第一项完成')
                if paused:
                    await new(tid,'pause',reason='待补充材料',recheck_at=(datetime.now(UTC)+timedelta(hours=1)).isoformat())
                active=actors['creator']
                before_cancel=await request('GET',f'/api/v1/tasks/{tid}/execution')
                accepted_at=await c.fetchval('SELECT accepted_at FROM workflow.task WHERE id=$1',tid)
                assert accepted_at is not None and before_cancel['progress']['percent']==50
                started=perf_counter()
                managed=await request('GET','/api/v1/tasks/management')
                report['samples'].setdefault('management_read_ms',[]).append(round((perf_counter()-started)*1000,2))
                projected=next(i for i in managed['items'] if i['id']==tid)
                assert projected['execution']['progress']==before_cancel['progress'] and projected['can_cancel'] is True
                assert all(before_cancel.get(key)==value for key,value in projected['execution'].items())
                await old(tid,'cancel',actor='creator',version=before_cancel['version_no']-1,expected=409)
                cancel_key=uuid4()
                closed=await old(tid,'cancel',actor='creator',version=before_cancel['version_no'],key=cancel_key)
                repeated=await old(tid,'cancel',actor='creator',version=before_cancel['version_no'],key=cancel_key)
                assert repeated==closed and closed['status']=='cancelled'
                assert datetime.fromisoformat(closed['accepted_at'])==accepted_at
                assert closed['execution']['progress']['percent']==50 and closed['execution']['pause'] is None
                assert closed['version_no']==before_cancel['version_no']+1 and closed['can_cancel'] is False
                assert await c.fetchval("SELECT count(*) FROM workflow.task_event WHERE task_id=$1 AND event_type='cancel'",tid)==1
                assert await c.fetchval("SELECT count(*) FROM workflow.task_execution_event WHERE task_id=$1 AND kind='cancel_flows'",tid)==1
                assert await c.fetchval("SELECT count(*) FROM workflow.task_assignee WHERE task_id=$1 AND responsibility='owner' AND assignee_user_ref_id=$2::uuid",tid,actors['executor'].user_id)==1
                await old(tid,'cancel',actor='creator',expected=409)
            report['checks'].append('restricted HTTP V1 cancellation after 1/2 completion, both running and paused: preserves acceptance/owner/progress, closes pause once, increments version once, identical receipt replay and stale-version/terminal guards remain intact')
            # Both modes survive all shared projection paths and notification reads.
            active=actors['creator']
            listed=await request('GET','/api/v1/tasks')
            items=listed if isinstance(listed,list) else listed['items']
            assert {'legacy','execution_v1'} <= {i['execution_mode'] for i in items}
            management=await request('GET','/api/v1/tasks/management')
            assert {'legacy','execution_v1'} <= {i['execution_mode'] for i in management['items']}
            async with db.transaction(actors['creator'],readonly=True) as tx:
                notifications=await NotificationRepository().list(tx,unread_only=False,limit=100)
                assert notifications
            report['checks'].append('task list, management, per-task action and notification projections read both modes under restricted role')
            # Source adapters share central create. They do not manufacture checklist definitions.
            for source in ('business_advice','meeting_import'):
                async with db.transaction(actors['creator']) as tx:
                    sourced=await TaskService().create(tx,actor=actors['creator'],description='人工来源兼容性任务',due_at=datetime.now(UTC)+timedelta(days=2),priority_code='normal',assignee_account_code='executor',source_code=source)
                    assert sourced['execution_mode']=='legacy'
            report['checks'].append('business_advice and meeting_import TaskService creation remain legacy without protocol; full external parser/AI/provider not exercised')
            active=actors['creator']
            finance_before=await c.fetchval('SELECT count(*) FROM crm.customer_actual')
            aligned=await request('POST','/api/v1/tasks',base|{'assignee_account_code':'creator','alignment':{'metric_codes':['daily_routine','visit_count'],'expected_contribution':{'metric_code':'visit_count','value':'2','note':'计划拜访'}}},201)
            await old(aligned['id'],'accept',actor='creator')
            aligned_done=await old(aligned['id'],'complete',actor='creator',actual_contribution={'metric_code':'visit_count','value':'1','note':'已验证跟进成果'})
            assert aligned_done['status']=='completed'
            assert aligned_done['alignment']['metric_codes']==['daily_routine','visit_count']
            assert aligned_done['alignment']['actual_contribution']['value']=='1'
            assert aligned_done['alignment']['actual_contribution']['counts_toward_financial_actuals'] is False
            other=await request('POST','/api/v1/tasks',base|{'alignment_contract_version':2,'alignment':{'other_reason':'客户要求的例行核对'}},201)
            assert other['alignment']['other_reason']=='客户要求的例行核对' and other['execution_mode']=='legacy'
            assert finance_before==await c.fetchval('SELECT count(*) FROM crm.customer_actual')
            report['checks'].append('legacy self-created completion, multiple metric links, expected/actual contribution and V2 other_reason remain intact without creating financial actuals')
            foreign_ws, foreign_uid = uuid4(), uuid4()
            await c.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Other tenant')",foreign_ws,str(foreign_ws))
            await c.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,'foreign','Foreign','foreign')",foreign_uid,foreign_ws)
            await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,'operations','workspace')",foreign_ws,foreign_uid)
            foreign=ActorContext(workspace_id=str(foreign_ws),user_id=str(foreign_uid),role='operations',data_scope='workspace',team_ids=[])
            active=foreign
            await request('GET',f'/api/v1/tasks/{v1["id"]}',expected=404)
            await request('POST',f'/api/v1/tasks/{legacy}/events',dict(event_type='accept',version_no=1),expected=404)
            report['checks'].append('cross-tenant detail and legacy mutations remain 404')
        report['migration_sha256']=hashlib.sha256(migration_path.read_bytes()).hexdigest()
        report['postgres_version']=await c.fetchval('SHOW server_version')
        report['status']='passed'
    except Exception as exc:
        report['status']='failed'
        report['failure']=str(exc)
        if isinstance(exc,asyncpg.PostgresError):
            report['postgres_context']=exc.context
        raise
    finally:
        if c:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()
        report['disposable_database_and_role_removed']=True
        Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='samples'},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--socket',required=True)
    p.add_argument('--port',type=int,default=55438)
    p.add_argument('--output',required=True)
    p.add_argument('--focus', choices=('full', 'boundary'), default='full')
    p.add_argument('--release-schema',action='store_true',help='Apply the remaining release migrations through V268 before the HTTP regression')
    asyncio.run(verify(p.parse_args()))
