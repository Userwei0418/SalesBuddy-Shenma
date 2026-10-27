"""V242 proof on an explicit disposable socket DB; no saved credentials or live DB."""

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'database/tests'), str(ROOT / 'database/scripts')]
from migrate import apply, item
from verify_runtime_grants_postgres import migrate_through
from verify_review_targets_postgres import grants
from sales_backend.api.tasks import router
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.services.idempotency import IdempotencyConflict


async def release_evidence(args):
    """Capture each release stage without repeating the already-passed product tests."""
    import copy
    sys.path.insert(0, str(ROOT / 'tools/installation'))
    from runtime_roles import COLUMN_PRIVILEGES, SCHEMAS, SEQUENCE_PRIVILEGES, TABLE_PRIVILEGES
    from runtime_roles import fingerprint, verify_rls

    socket = Path(args.socket).resolve()
    assert str(socket).startswith(('/private/tmp/sb-review-pg.', '/tmp/sb-review-pg.'))
    config = dict(host=str(socket), port=args.port, user='postgres', password='', passfile='/dev/null', ssl=False)
    output = Path(args.output)
    assert not output.exists(), 'Release evidence is immutable; choose a fresh output path'
    policy_path = ROOT / 'tools/installation/runtime_permissions.json'
    policy_bytes = policy_path.read_bytes()
    policy = json.loads(policy_bytes)
    assert policy['schema'] == 'V240', 'This evidence run is bound to the reviewed V240 policy'
    suffix = uuid4().hex[:12]
    name = 'sb_review_release_' + suffix
    roles = {service: 'sb_review_' + service + '_' + suffix for service in sorted(policy['services'])}
    admin = await asyncpg.connect(database='postgres', **config)
    connection = None
    stages = {}
    expected_delta = {
        'V240': {'tables': ['crm.review_metric'], 'functions': [
            'security.save_review_metric(text,jsonb,integer)', 'security.review_target_money_json(jsonb)']},
        'V241': {'tables': ['crm.review_objective', 'crm.review_key_result', 'crm.review_key_result_progress'],
            'functions': ['security.review_objective_readable(uuid)', 'security.review_key_result_readable(uuid)',
                'security.save_review_objective(uuid,jsonb,integer)', 'security.save_review_key_result(uuid,jsonb,integer)',
                'security.confirm_review_key_result_progress(uuid,integer,numeric,text,boolean)',
                'security.mention_review_okr(text,uuid,integer,uuid[])']},
        'V242': {'tables': ['workflow.task_alignment', 'workflow.task_metric_alignment', 'workflow.task_key_result_alignment'],
            'functions': ['security.task_alignment_read(uuid)', 'security.save_task_alignment(uuid,jsonb,integer)',
                'security.record_task_contribution(uuid,jsonb,integer)']},
    }

    async def privileges(role):
        rows = await connection.fetch('''SELECT n.nspname||'.'||c.relname AS name,
          ARRAY(SELECT p FROM unnest($3::text[]) p WHERE has_table_privilege($1,c.oid,p)) AS privileges
          FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname=ANY($2::text[]) AND c.relkind IN ('r','p','v','m','f') ORDER BY 1''',
          role, [*SCHEMAS, 'public'], TABLE_PRIVILEGES)
        columns = await connection.fetch('''SELECT n.nspname||'.'||c.relname||'.'||a.attname AS name,
          ARRAY(SELECT p FROM unnest($3::text[]) p WHERE has_column_privilege($1,c.oid,a.attnum,p)) AS privileges
          FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid
          WHERE n.nspname=ANY($2::text[]) AND c.relkind IN ('r','p','v','m','f') AND a.attnum>0 AND NOT a.attisdropped
          ORDER BY 1''', role, [*SCHEMAS, 'public'], COLUMN_PRIVILEGES)
        sequences = await connection.fetch('''SELECT n.nspname||'.'||c.relname AS name,
          ARRAY(SELECT p FROM unnest($3::text[]) p WHERE has_sequence_privilege($1,c.oid,p)) AS privileges
          FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname=ANY($2::text[]) AND c.relkind='S' ORDER BY 1''', role, [*SCHEMAS, 'public'], SEQUENCE_PRIVILEGES)
        functions = await connection.fetch('''SELECT p.oid::regprocedure::text AS signature
          FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname=ANY($2::text[]) AND has_function_privilege($1,p.oid,'EXECUTE') ORDER BY 1''', role, SCHEMAS)
        return {'tables': {r['name']: sorted(r['privileges']) for r in rows if r['privileges']},
            'columns': {r['name']: sorted(r['privileges']) for r in columns if r['privileges']},
            'sequences': {r['name']: sorted(r['privileges']) for r in sequences if r['privileges']},
            'functions': [r['signature'] for r in functions]}

    async def snapshot(stage):
        ledger = dict(await connection.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum ORDER BY migration_key'))
        registered = [r['version'] for r in await connection.fetch('SELECT version FROM ops.schema_migration ORDER BY version')]
        tables = [dict(r) for r in await connection.fetch('''SELECT n.nspname||'.'||c.relname AS name,
          c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p') ORDER BY 1''', SCHEMAS)]
        raw = [dict(r) for r in await connection.fetch('''SELECT schemaname,tablename,policyname,
          permissive,roles::text[],cmd,qual,with_check FROM pg_policies
          WHERE schemaname=ANY($1::text[]) ORDER BY 1,2,3''', SCHEMAS)]
        normalized = copy.deepcopy(raw)
        direct = {service: {} for service in roles}
        for row in normalized:
            for service, role in roles.items():
                if role in row['roles']:
                    direct[service][row['schemaname'] + '.' + row['tablename']] = row['policyname']
            row['roles'] = sorted(role for role in row['roles'] if role not in roles.values())
        rls = {'tables': tables, 'policies': normalized}
        rls_fingerprint = fingerprint(rls)
        check_policy = copy.deepcopy(policy)
        check_policy['rls_fingerprint'] = rls_fingerprint
        await verify_rls(connection, roles, check_policy)
        assert direct == {service: rules['policies'] for service, rules in policy['services'].items()}
        actual = {service: await privileges(role) for service, role in roles.items()}
        result = {'stage': stage, 'ledger': ledger, 'ledger_count': len(ledger),
            'migration_fingerprint': fingerprint(ledger), 'registered': registered, 'registered_count': len(registered),
            'registered_fingerprint': hashlib.sha256(json.dumps(registered, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'rls': rls, 'raw_pg_policies': raw, 'direct_service_policies': direct, 'rls_fingerprint': rls_fingerprint,
            'original_verify_rls_passed': True, 'effective_privileges': actual}
        if stage == 'V240':
            assert rls_fingerprint == '55839e43cb6aab54d61fcb848f3a5780734d0658eb8d215bfb09f813fa91d57a'
            assert result['migration_fingerprint'] == 'a1e512a43c5eb8f201a3c1f3811e3db1781a793923ab5a19cf3dc1691a89b6e2'
            assert rls_fingerprint == policy['rls_fingerprint'] and result['migration_fingerprint'] == policy['migration_fingerprint']
        return result

    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        connection = await asyncpg.connect(database=name, **config)
        await migrate_through(connection, 239)
        filtered_out = {}
        for service, role in roles.items():
            await connection.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION')
            rules = policy['services'][service]
            filtered_out[service] = {'tables': [], 'functions': []}
            for parent in rules['memberships']:
                await connection.execute(f'GRANT "{parent}" TO "{role}" WITH INHERIT FALSE, SET FALSE')
            for schema in rules['schemas']:
                await connection.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
            for kind, sql_kind in (('tables', 'TABLE'), ('sequences', 'SEQUENCE')):
                for table, permissions in rules[kind].items():
                    if await connection.fetchval('SELECT to_regclass($1) IS NOT NULL', table):
                        await connection.execute(f'GRANT {",".join(permissions)} ON {sql_kind} {table} TO "{role}"')
                    else:
                        filtered_out[service].setdefault(kind, []).append(table)
            for table, permissions in rules['columns'].items():
                if not await connection.fetchval('SELECT to_regclass($1) IS NOT NULL', table):
                    filtered_out[service].setdefault('columns', []).append(table)
                    continue
                for privilege, columns in permissions.items():
                    await connection.execute(f'GRANT {privilege}({",".join(columns)}) ON {table} TO "{role}"')
            for signature in rules['functions']:
                if await connection.fetchval('SELECT to_regprocedure($1) IS NOT NULL', signature):
                    await connection.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO "{role}"')
                else:
                    filtered_out[service]['functions'].append(signature)
            for table, row_policy in rules['policies'].items():
                await connection.execute(f'ALTER POLICY {row_policy} ON {table} TO salegent_feishu_worker,"{role}"')
        await connection.execute('SELECT security.reconcile_runtime_grants()')
        stages['V239'] = previous = await snapshot('V239')
        for sql in sorted((ROOT / 'database/migrations').glob('V24[012]__*.sql')):
            stage = sql.name.split('__')[0]
            migration = item(stage, sql)
            await apply(connection, migration, ROOT / 'database')
            current = await snapshot(stage)
            assert all(current['ledger'][key] == value for key, value in previous['ledger'].items())
            assert set(current['ledger']) - set(previous['ledger']) == {stage}
            assert set(current['registered']) - set(previous['registered']) == {stage}
            assert current['ledger'][stage] == migration.checksum == hashlib.sha256(sql.read_bytes()).hexdigest()
            current['sql'] = {'path': str(sql.relative_to(ROOT)), 'sha256': migration.checksum}
            deltas = {}
            for service in roles:
                before = previous['effective_privileges'][service]
                after = current['effective_privileges'][service]
                changes = {}
                for kind in ('tables', 'columns', 'sequences'):
                    added = {key: value for key, value in after[kind].items() if key not in before[kind]}
                    removed = {key: value for key, value in before[kind].items() if key not in after[kind]}
                    changed = {key: {'before': before[kind][key], 'after': after[kind][key]}
                        for key in before[kind].keys() & after[kind].keys() if before[kind][key] != after[kind][key]}
                    changes[kind] = {'added': added, 'removed': removed, 'changed': changed}
                changes['functions'] = {'added': sorted(set(after['functions']) - set(before['functions'])),
                    'removed': sorted(set(before['functions']) - set(after['functions']))}
                if service != 'api':
                    assert before == after, (stage, service, changes)
                else:
                    expected = expected_delta[stage]
                    assert changes['tables']['added'] == {table: ['SELECT'] for table in expected['tables']}, (stage, changes)
                    assert changes['functions']['added'] == sorted(expected['functions']), (stage, changes)
                    assert not changes['functions']['removed']
                    for kind in ('tables', 'columns', 'sequences'):
                        assert not changes[kind]['removed'] and not changes[kind]['changed']
                    assert not changes['sequences']['added']
                    columns = await connection.fetch('''SELECT n.nspname||'.'||c.relname||'.'||a.attname AS name
                      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid
                      WHERE n.nspname||'.'||c.relname=ANY($1::text[]) AND a.attnum>0 AND NOT a.attisdropped''', expected['tables'])
                    assert changes['columns']['added'] == {r['name']: ['SELECT'] for r in columns}
                deltas[service] = changes
            current['actual_acl_delta'] = deltas
            current['api_exact_additive_delta_verified'] = True
            current['worker_feishu_platform_unchanged'] = True
            stages[stage] = previous = current
        assert set(stages) == {'V239', 'V240', 'V241', 'V242'}
        result = {'status': 'passed_isolated_release_metadata_evidence', 'stages': stages,
            'runtime_roles_source': {'path': str(ROOT / 'tools/installation/runtime_roles.py'),
                'sha256': hashlib.sha256((ROOT / 'tools/installation/runtime_roles.py').read_bytes()).hexdigest(),
                'schemas': list(SCHEMAS), 'normalization': 'original verify_rls fields; temporary service roles removed then sorted'},
            'input_policy_sha256': hashlib.sha256(policy_bytes).hexdigest(), 'input_policy_schema': policy['schema'],
            'baseline_policy_objects_absent_before_V240': filtered_out, 'roles': roles,
            'scope': 'own disposable private-socket database and four NOLOGIN/NOBYPASSRLS roles; no production or existing LOCAL access',
            'full_runtime_login_verifier_run': False,
            'cleanup': 'own database and four temporary roles dropped; shared cluster and legacy group retained'}
    finally:
        if connection:
            await connection.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        for role in roles.values():
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'output': str(output), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'stages': {key: {'rls': value['rls_fingerprint'], 'ledger': value['migration_fingerprint'],
                        'registered': value['registered_fingerprint']} for key, value in stages.items()}}))


async def verify(args):
    socket = Path(args.socket).resolve()
    assert str(socket).startswith(('/private/tmp/sb-review-pg.', '/tmp/sb-review-pg.'))
    config = dict(host=str(socket), port=args.port, user='postgres', password='', passfile='/dev/null', ssl=False)
    admin = await asyncpg.connect(database='postgres', **config)
    suffix = uuid4().hex[:12]
    name = 'sb_review_tasks_' + suffix
    role = 'sb_review_tasks_api_' + suffix
    worker = 'sb_review_tasks_worker_' + suffix
    c = None
    checks = []
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        c = await asyncpg.connect(database=name, **config)
        await migrate_through(c, 239)
        before = {r['migration_key']: r['checksum_sha256'] for r in await c.fetch('SELECT * FROM ops.migration_checksum')}
        await c.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await grants(c, role)
        await c.execute(f'CREATE ROLE "{worker}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await c.execute(f'GRANT USAGE ON SCHEMA security,workflow TO "{worker}"')
        sqls = sorted((ROOT / 'database/migrations').glob('V24[012]__*.sql'))
        assert len(sqls) == 3
        for sql in sqls:
            await apply(c, item(sql.name.split('__')[0], sql), ROOT / 'database')
        after = {r['migration_key']: r['checksum_sha256'] for r in await c.fetch('SELECT * FROM ops.migration_checksum')}
        assert all(after[k] == v for k, v in before.items()) and set(after) - set(before) == {'V240', 'V241', 'V242'}
        checks.append('V239 forward through V242, original checksums preserved')
        for typ in ('json', 'jsonb'):
            await c.set_type_codec(typ, schema='pg_catalog', encoder=json.dumps, decoder=json.loads, format='text')
        await c.execute('SET row_security=on')
        actors, workspaces, people, krs = {}, {}, {}, {}
        for label in ('own', 'foreign'):
            ws = uuid4()
            workspaces[label] = ws
            await c.execute('INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$2)', ws, 'alignment-' + str(ws))
            await c.execute('UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1', ws)
            # Explicitly narrow this fixture's sales grants; do not assume the
            # built-in role's target/task read scope is limited to self.
            await c.execute("""UPDATE config.permission_role_grant g SET scope_code='self',team_ids='{}'
              FROM config.permission_role r WHERE r.id=g.role_id AND r.workspace_id=$1
              AND r.builtin_role_code='sales' AND g.permission_code IN ('target.read','task.read')""", ws)
            team = uuid4()
            await c.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,'合成销售团队')", team, ws, 'alignment_' + label)
            for code, business_role in [('ops', 'operations'), ('sales', 'sales'), ('other', 'sales')]:
                uid = uuid4()
                account = code + uuid4().hex[:8]
                people[label + '_' + code] = (uid, account)
                await c.execute('INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)', uid, ws, account)
                await c.execute('INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,$3,$4)', ws, uid, business_role, 'workspace' if code == 'ops' else 'self')
                if code != 'ops':
                    await c.execute('UPDATE platform.role_binding SET team_id=$2 WHERE user_ref_id=$1', uid, team)
                    await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')", ws, uid, team)
                actors[label + '_' + code] = ActorContext(workspace_id=str(ws), user_id=str(uid), role=business_role,
                    data_scope='workspace' if code == 'ops' else 'self', team_ids=[str(team)] if code != 'ops' else [])
                if code != 'ops':
                    objective = await c.fetchval("""INSERT INTO crm.review_objective(workspace_id,title,scope_type,user_ref_id,department_code,
                      period_type,period_start,period_end,owner_user_id,created_by_user_id,updated_by_user_id)
                      VALUES($1,'合成季度目标','person',$2,'sales','quarter',date_trunc('quarter',current_date)::date,
                      (date_trunc('quarter',current_date)+interval '3 months - 1 day')::date,$2,$2,$2) RETURNING id""", ws, uid)
                    krs[label + '_' + code] = await c.fetchval("""INSERT INTO crm.review_key_result(workspace_id,objective_id,title,metric_code,
                      owner_user_id,created_by_user_id,updated_by_user_id) VALUES($1,$2,'合成跟进KR','visit_count',$3,$3,$3) RETURNING id""", ws, objective, uid)
                    if label == 'own' and code == 'sales':
                        krs['own_extra'] = await c.fetchval("""INSERT INTO crm.review_key_result(workspace_id,objective_id,title,metric_code,
                          owner_user_id,created_by_user_id,updated_by_user_id) VALUES($1,$2,'第二个合成KR','visit_count',$3,$3,$3) RETURNING id""", ws, objective, uid)
        active = actors['own_sales']
        class DB:
            @asynccontextmanager
            async def transaction(self, actor, readonly=False, **kwargs):
                async with c.transaction(readonly=readonly, **kwargs):
                    await c.execute(f'SET LOCAL ROLE "{role}"')
                    await set_request_context(c, actor)
                    assert not await c.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
                    yield c
        db = DB()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_database] = lambda: db
        app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=active)
        @app.exception_handler(IdempotencyConflict)
        async def idem_error(request, exc):
            return JSONResponse({'detail': str(exc)}, status_code=409)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic') as client:
            async def post(path, body, status=200, key=None):
                response = await client.post(path, json=body, headers={'Idempotency-Key': key} if key else {})
                assert response.status_code == status, (path, response.status_code, response.text)
                return response.json()
            async def patch(task, body, status=200):
                response = await client.patch('/api/v1/tasks/' + task + '/alignment', json=body)
                assert response.status_code == status, (response.status_code, response.text)
                return response.json()
            base = dict(description='合成任务核对跟进并汇报', assignee_account_code=people['own_sales'][1],
                        due_at=(datetime.now(UTC) + timedelta(days=1)).isoformat())
            old = await post('/api/v1/tasks', base, 201)
            assert old['alignment'] is None
            assert await c.fetchval('SELECT count(*) FROM workflow.task_alignment') == 0
            old = await post('/api/v1/tasks/' + old['id'] + '/events', {'event_type': 'accept'})
            old = await post('/api/v1/tasks/' + old['id'] + '/events', {'event_type': 'complete', 'note': '旧端完成说明'})
            assert old['status'] == 'completed' and old['alignment'] is None
            checks.append('legacy create, accept and complete without new fields remain operational and unclassified')
            expected_kr_ids = sorted([str(krs['own_sales']), str(krs['own_extra'])])
            alignment = {'metric_codes': ['daily_routine', 'daily_routine'],
                         'key_result_ids': [*expected_kr_ids, str(krs['own_sales'])],
                         'expected_contribution': {'metric_code': 'visit_count', 'value': '2', 'note': '合成计划'}}
            key = str(uuid4())
            created = await post('/api/v1/tasks', {**base, 'alignment': alignment}, 201, key)
            assert await post('/api/v1/tasks', {**base, 'alignment': alignment}, 201, key) == created
            task = created['id']
            got = created['alignment']
            assert got['metric_codes'] == ['daily_routine', 'visit_count'] and got['key_result_ids'] == expected_kr_ids
            assert got['direct_metric_codes'] == ['daily_routine']
            assert got['expected_contribution']['value'] == '2' and got['expected_contribution']['unit'] == '次'
            assert got['version_no'] == 1 and got['counts_toward_financial_actuals'] is False
            checks.append('real create atomic alignment, canonical dedup, inherited KR metric, server unit and replay')
            for url in ('/api/v1/tasks', '/api/v1/tasks?tab=all', '/api/v1/tasks/management'):
                response = await client.get(url)
                assert response.status_code == 200, response.text
                row = next(r for r in response.json()['items'] if r['id'] == task)
                assert row['alignment']['key_result_ids'] == expected_kr_ids
            overview = await client.get('/api/v1/tasks/overview')
            assert overview.json()['metrics']['all_pending'] == 1
            checks.append('list, tab, management read same links and global count remains one task')
            await patch(task, {'version_no': 99, 'alignment': alignment}, 409)
            updated = await patch(task, {'version_no': 1, 'alignment': alignment})
            assert updated['version_no'] == 2 and updated['alignment']['version_no'] == 2
            await patch(task, {'version_no': 1, 'alignment': alignment}, 409)
            checks.append('alignment uses locked task version and rejects concurrent stale replacement')
            active = actors['own_other']
            await patch(task, {'version_no': 2, 'alignment': alignment}, 404)
            assert (await client.get('/api/v1/tasks/' + task)).status_code == 404
            active = actors['foreign_sales']
            await patch(task, {'version_no': 2, 'alignment': alignment}, 404)
            active = actors['own_sales']
            for kr in (krs['foreign_sales'], krs['own_other']):
                await post('/api/v1/tasks', {**base, 'alignment': {**alignment, 'key_result_ids': [str(kr)]}}, 403)
            checks.append('cross-tenant and same-tenant unreadable task/KR associations denied')
            count = await c.fetchval('SELECT count(*) FROM workflow.task')
            for bad in ({**alignment, 'metric_codes': ['unknown']},
                        {**alignment, 'expected_contribution': {'metric_code': 'visit_count', 'value': '0.25'}},
                        {**alignment, 'expected_contribution': {'metric_code': 'collection', 'value': '2'}}):
                await post('/api/v1/tasks', {**base, 'alignment': bad}, 409)
            assert await c.fetchval('SELECT count(*) FROM workflow.task') == count
            await c.execute("UPDATE crm.review_metric SET active=false WHERE workspace_id=$1 AND code='daily_routine'", workspaces['own'])
            await post('/api/v1/tasks', {**base, 'alignment': {'metric_codes': ['daily_routine']}}, 409)
            await c.execute("UPDATE crm.review_metric SET active=true WHERE workspace_id=$1 AND code='daily_routine'", workspaces['own'])
            checks.append('unknown/disabled metric, wrong contribution metric and count precision reject whole transaction')
            financial_tables = ('crm.opportunity', 'crm.sales_target', 'crm.review_key_result_progress',
                                'crm.opportunity_actual_confirmation', 'crm.opportunity_actual_basis')
            financial_before = [await c.fetchval('SELECT count(*) FROM ' + table) for table in financial_tables]
            accepted = await post('/api/v1/tasks/' + task + '/events', {'event_type': 'accept', 'version_no': 2})
            done = await post('/api/v1/tasks/' + task + '/events', {'event_type': 'complete', 'note': '任务完成，跟进可查',
                'version_no': accepted['version_no'], 'actual_contribution': {'metric_code': 'visit_count', 'value': '1', 'note': '合成跟进记录'}})
            assert done['status'] == 'completed' and done['alignment']['key_result_ids'] == expected_kr_ids
            assert done['alignment']['actual_contribution']['status'] == 'reported'
            assert financial_before == [await c.fetchval('SELECT count(*) FROM ' + table) for table in financial_tables]
            await patch(task, {'version_no': done['version_no'], 'alignment': alignment}, 409)
            checks.append('actual claim remains reported, preserves links and does not create financial or KR progress')
            # Old completion/review on new relationships must preserve the same stored alignment.
            mixed = await post('/api/v1/tasks', {**base, 'assignee_account_code': people['own_other'][1], 'alignment': alignment}, 201)
            active = actors['own_other']
            detail = (await client.get('/api/v1/tasks/' + mixed['id'])).json()
            assert detail['alignment']['key_result_ids'] == [] and detail['alignment']['restricted_key_result_count'] == 2
            await post('/api/v1/tasks/' + mixed['id'] + '/events', {'event_type': 'accept'})
            pending = await post('/api/v1/tasks/' + mixed['id'] + '/events', {'event_type': 'complete', 'note': '旧端完成有KR任务'})
            assert pending['status'] == 'pending_review'
            active = actors['own_sales']
            reviewed = await post('/api/v1/tasks/' + mixed['id'] + '/events', {'event_type': 'approve_completion', 'version_no': pending['version_no']})
            assert reviewed['status'] == 'completed' and reviewed['alignment']['key_result_ids'] == expected_kr_ids
            checks.append('task recipient cannot see private KR; old complete and creator review retain links')
            money = await post('/api/v1/tasks', {**base, 'alignment': {'metric_codes': ['collection'],
                'expected_contribution': {'metric_code': 'collection', 'value': '9999.99'}}}, 201)
            accepted = await post('/api/v1/tasks/' + money['id'] + '/events', {'event_type': 'accept'})
            done = await post('/api/v1/tasks/' + money['id'] + '/events', {'event_type': 'complete', 'note': '合成贡献声明',
                'version_no': accepted['version_no'], 'actual_contribution': {'metric_code': 'collection', 'value': '9999.99', 'note': '待正式财务确认'}})
            assert done['alignment']['actual_contribution']['value'] == '9999.99'
            assert done['alignment']['actual_contribution']['counts_toward_financial_actuals'] is False
            assert financial_before == [await c.fetchval('SELECT count(*) FROM ' + table) for table in financial_tables]
            checks.append('explicit monetary task contribution and completion create no collection/recognition/target/KR entries')
            editable = await post('/api/v1/tasks', {**base, 'alignment': alignment}, 201)
            await c.execute("UPDATE crm.review_key_result SET status='archived' WHERE id=$1", krs['own_sales'])
            await post('/api/v1/tasks', {**base, 'alignment': alignment}, 403)
            await patch(editable['id'], {'version_no': editable['version_no'], 'alignment': {'metric_codes': ['daily_routine']}}, 403)
            readback = (await client.get('/api/v1/tasks/' + editable['id'])).json()
            assert readback['alignment']['restricted_key_result_count'] == 1
            assert await c.fetchval('SELECT count(*) FROM workflow.task_key_result_alignment WHERE task_id=$1::uuid', editable['id']) == 2
            checks.append('archived KR rejected for new links and existing restricted relationships cannot be silently dropped')
            raced = await post('/api/v1/tasks', {**base, 'alignment': {'metric_codes': ['daily_routine']}}, 201)
            async def contender(metric):
                connection = await asyncpg.connect(database=name, **config)
                try:
                    await connection.set_type_codec('jsonb', schema='pg_catalog', encoder=json.dumps, decoder=json.loads, format='text')
                    async with connection.transaction():
                        await connection.execute(f'SET LOCAL ROLE "{role}"')
                        await set_request_context(connection, active)
                        await connection.fetchval('SELECT security.save_task_alignment($1::uuid,$2::jsonb,1)',
                            raced['id'], {'metric_codes': [metric], 'key_result_ids': [], 'expected_contribution': None})
                    return 'saved'
                except asyncpg.RaiseError:
                    return 'stale'
                finally:
                    await connection.close()
            assert sorted(await asyncio.gather(contender('daily_routine'), contender('visit_count'))) == ['saved', 'stale']
            assert await c.fetchval('SELECT version_no FROM workflow.task WHERE id=$1::uuid', raced['id']) == 2
            checks.append('two independent concurrent transactions with one expected version yield one save and one stale conflict')
            guarded = await post('/api/v1/tasks', {**base, 'alignment': {'metric_codes': ['daily_routine']}}, 201)
            guarded_url = '/api/v1/tasks/' + guarded['id'] + '/alignment'
            guarded_body = {'version_no': 1, 'alignment': {'metric_codes': ['daily_routine']}}
            guarded_headers = {'Idempotency-Key': str(uuid4())}
            first = await client.patch(guarded_url, json=guarded_body, headers=guarded_headers)
            assert first.status_code == 200, first.text
            replay = await client.patch(guarded_url, json=guarded_body, headers=guarded_headers)
            assert replay.status_code == 200 and replay.json() == first.json()
            await post('/api/v1/tasks/' + guarded['id'] + '/events', {'event_type': 'accept'})
            await post('/api/v1/tasks/' + guarded['id'] + '/events', {'event_type': 'complete', 'note': '合成完成后重放'})
            replay = await client.patch(guarded_url, json=guarded_body, headers=guarded_headers)
            assert replay.status_code == 200 and replay.json() == first.json()
            await c.execute('INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)',
                workspaces['own'], people['own_sales'][0])
            for code in ('task.coordinate', 'task.cancel'):
                await c.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,$3,'deny')",
                    workspaces['own'], people['own_sales'][0], code)
            denied = await client.patch(guarded_url, json=guarded_body, headers=guarded_headers)
            assert denied.status_code == 403, denied.text
            await c.execute('DELETE FROM config.account_permission_override WHERE workspace_id=$1 AND user_ref_id=$2',
                workspaces['own'], people['own_sales'][0])
            await c.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,'task.read','deny')",
                workspaces['own'], people['own_sales'][0])
            denied = await client.patch(guarded_url, json=guarded_body, headers=guarded_headers)
            assert denied.status_code == 404, denied.text
            assert await c.fetchval('SELECT count(*) FROM ops.mutation_receipt WHERE request_key=$1::uuid', guarded_headers['Idempotency-Key']) == 1
            checks.append('PATCH receipt replay rechecks current task read/coordination scope; stale version and completed state do not reject an authorized committed replay')
        for function in ('security.task_alignment_read(uuid)', 'security.save_task_alignment(uuid,jsonb,integer)', 'security.record_task_contribution(uuid,jsonb,integer)'):
            assert await c.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', role, function)
            assert not await c.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', worker, function)
        assert not await c.fetchval('SELECT has_function_privilege($1,\'security.validate_task_contribution(uuid,jsonb,boolean)\',\'EXECUTE\')', role)
        for table in ('task_alignment', 'task_metric_alignment', 'task_key_result_alignment', 'task_alignment_history'):
            for privilege in ('INSERT', 'UPDATE', 'DELETE'):
                assert not await c.fetchval('SELECT has_table_privilege($1,$2,$3)', role, 'workflow.' + table, privilege)
            assert not await c.fetchval('SELECT has_table_privilege($1,$2,\'SELECT\')', worker, 'workflow.' + table)
        checks.append('API-only precise function grants; no direct mutations, internal helper or worker access')
        result = {'status': 'passed_isolated_postgres_and_asgi', 'checks': checks, 'check_count': len(checks),
                  'migrations': {sql.name: hashlib.sha256(sql.read_bytes()).hexdigest() for sql in sqls},
                  'ledger_before': before, 'ledger_after': after,
                  'scope': 'synthetic private socket database only, trusted actor override, real restricted-role RLS/functions',
                  'cleanup': 'own database and roles dropped in finally'}
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'status': result['status'], 'check_count': len(checks), 'output': args.output}))
    finally:
        if c:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.execute(f'DROP ROLE IF EXISTS "{worker}"')
        await admin.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--release-evidence-only', action='store_true')
    args = parser.parse_args()
    asyncio.run(release_evidence(args) if args.release_evidence_only else verify(args))
