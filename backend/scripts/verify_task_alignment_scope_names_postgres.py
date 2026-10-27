# ruff: noqa: E402, E501, S108, S608 -- UUID private fixtures and fixed SQL identifiers only.
"""D03 display-only names: isolated limited-role HTTP proof, without new grants."""

import argparse
import asyncio
import copy
import hashlib
import io
import json
from contextlib import asynccontextmanager, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import httpx
from fastapi import FastAPI
from verify_task_alignment_v2_postgres import ROOT, acl, apply, exact, fingerprint, item, migrate_through, rls

from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.review_okr import router as okr_router
from sales_backend.api.tasks import router
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext

PRODUCT_FILES = [
    'backend/src/sales_backend/repositories/' + name + '.py'
    for name in ('task_alignment', 'tasks', 'task_browse', 'review_okr')
]


def without_names(value):
    result = copy.deepcopy(value)
    for key in ('targets', 'key_results'):
        for entry in result.get(key, []):
            entry['scope'].pop('user_name', None)
            entry['scope'].pop('team_name', None)
    return result


async def verify(args):
    socket = str(Path(args.socket).resolve())
    assert socket.startswith(('/tmp/sb-review-pg.', '/private/tmp/sb-review-pg.'))
    config = dict(host=socket, port=args.port, user='postgres', password='', passfile='/dev/null', ssl=False)
    output = Path(args.output)
    assert not output.exists()
    suffix = uuid4().hex[:12]
    name = 'sb_d03_names_' + suffix
    policy = json.loads((ROOT / 'backend/tests/fixtures/review_release_contract.frozen.json').read_text())['policies']['V243']
    assert policy['schema'] == 'V243'
    roles = {service: 'sb_d03_names_' + service + '_' + suffix for service in sorted(policy['services'])}
    checks, samples = [], {}
    c = None
    admin = await asyncpg.connect(database='postgres', **config)
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        c = await asyncpg.connect(database=name, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(c, 243)
        await c.execute(f'REVOKE ALL ON DATABASE "{name}" FROM PUBLIC')
        await c.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        for service, identity in roles.items():
            await c.execute(f'CREATE ROLE "{identity}" NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION')
            await c.execute(f'GRANT CONNECT ON DATABASE "{name}" TO "{identity}"')
            for parent in policy['services'][service]['memberships']:
                await c.execute(f'GRANT "{parent}" TO "{identity}" WITH INHERIT FALSE, SET FALSE')
        await exact(c, roles, policy, reconcile=True)
        original_acl = await acl(c, roles)
        for typ in ('json', 'jsonb'):
            await c.set_type_codec(typ, schema='pg_catalog', encoder=json.dumps, decoder=json.loads, format='text')
        sql = ROOT / 'database/migrations/V244__task_alignment_object_basis.sql'
        await apply(c, item('V244', sql), ROOT / 'database')
        ledger = dict(await c.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
        stage = copy.deepcopy(policy)
        stage.update(schema='V244', migration_fingerprint=fingerprint(ledger), rls_fingerprint=fingerprint(await rls(c, roles)))
        exact_result = await exact(c, roles, stage)
        assert await acl(c, roles) == original_acl
        await c.execute('SET row_security=on')
        fixtures = {}
        for label in ('own', 'foreign'):
            ws, team, user, owner = uuid4(), uuid4(), uuid4(), uuid4()
            await c.execute('INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$2)', ws, str(ws))
            await c.execute('UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1', ws)
            await c.execute("""UPDATE config.permission_role_grant g SET scope_code='self',team_ids='{}'
              FROM config.permission_role r WHERE r.id=g.role_id AND r.workspace_id=$1
              AND r.builtin_role_code='sales' AND g.permission_code IN ('target.read','task.read')""", ws)
            await c.execute('INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,$4)', team, ws, label, label + '_TEAM')
            for uid, display in ((user, label + '_SUBJECT'), (owner, label + '_OTHER_OWNER')):
                await c.execute('INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)', uid, ws, display + suffix)
                await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'sales','self',$3)", ws, uid, team)
                await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')", ws, uid, team)
            await c.execute('INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)', ws, user)
            await c.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2,'target.read','allow','workspace')", ws, user)
            objects = {}
            for scope in ('person', 'team'):
                person_id, team_id = (user, None) if scope == 'person' else (None, team)
                target = await c.fetchval("""INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,user_ref_id,team_id,
                  kind,amount,updated_by_user_ref_id,period_type,period_start,period_end)
                  VALUES($1,2026,$2,$3,$4,'collection',100,$5,'quarter','2026-10-01','2026-12-31') RETURNING id""", ws, scope, person_id, team_id, user)
                objective = await c.fetchval("""INSERT INTO crm.review_objective(workspace_id,title,scope_type,user_ref_id,team_id,department_code,
                  period_type,period_start,period_end,owner_user_id,created_by_user_id,updated_by_user_id)
                  VALUES($1,$2,$3,$4,$5,'sales','quarter','2026-10-01','2026-12-31',$6,$7,$7) RETURNING id""", ws, label + '_' + scope, scope, person_id, team_id, owner, user)
                kr = await c.fetchval("""INSERT INTO crm.review_key_result(workspace_id,objective_id,title,metric_code,owner_user_id,created_by_user_id,updated_by_user_id)
                  VALUES($1,$2,'合成KR','daily_routine',$3,$4,$4) RETURNING id""", ws, objective, owner, user)
                objects[scope] = dict(target=str(target), objective=str(objective), kr=str(kr))
            fixtures[label] = dict(ws=ws, team=team, user=user, owner=owner, objects=objects,
                user_name=label + '_SUBJECT' + suffix, team_name=label + '_TEAM')
        own, foreign = fixtures['own'], fixtures['foreign']
        actor = ActorContext(workspace_id=str(own['ws']), user_id=str(own['user']), role='sales', data_scope='self', team_ids=[str(own['team'])])

        class DB:
            @asynccontextmanager
            async def transaction(self, context, readonly=False, **kwargs):
                async with c.transaction(readonly=readonly, **kwargs):
                    await c.execute(f'SET LOCAL ROLE "{roles["api"]}"')
                    await set_request_context(c, context)
                    assert not await c.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
                    yield c

        db = DB()
        app = FastAPI()
        app.include_router(router)
        app.include_router(okr_router)
        app.dependency_overrides[get_database] = lambda: db
        app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=actor)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic') as client:
            http_count = 0

            async def call(method, path, body=None, status=200):
                nonlocal http_count
                http_count += 1
                response = await client.request(method, path, json=body)
                assert response.status_code == status, (method, path, response.status_code, response.text)
                return response.json()

            base = dict(description='合成名称回显验证', assignee_account_code=own['user_name'], due_at=(datetime.now(UTC) + timedelta(days=1)).isoformat())
            ids = own['objects']
            task = await call('POST', '/api/v1/tasks', {**base, 'alignment_contract_version': 2,
                'alignment': {'target_ids': [ids[s]['target'] for s in ('person', 'team')], 'key_result_ids': [ids[s]['kr'] for s in ('person', 'team')]}}, 201)
            legacy = await call('POST', '/api/v1/tasks', {**base, 'alignment': {'metric_codes': ['daily_routine'], 'key_result_ids': [ids['person']['kr']]}}, 201)
            plain = await call('POST', '/api/v1/tasks', base, 201)

            async def raw(task_id):
                async with db.transaction(actor, readonly=True) as scoped:
                    return await scoped.fetchval('SELECT security.task_alignment_read($1::uuid)', task_id)

            def named(alignment):
                for key in ('targets', 'key_results'):
                    assert len(alignment[key]) == 2
                    for obj in alignment[key]:
                        scope = obj['scope']
                        assert (scope['user_name'], scope['team_name']) == (
                            (own['user_name'], None) if scope['scope_type'] == 'person' else (None, own['team_name'])
                        )
                assert without_names(alignment) == original

            original = await raw(task['id'])
            legacy_original = await raw(legacy['id'])
            assert legacy['alignment'] == legacy_original
            named(task['alignment'])
            samples['v2_alignment'] = task['alignment']
            reads = ['/api/v1/tasks/' + task['id'], '/api/v1/tasks', '/api/v1/tasks?tab=all', '/api/v1/tasks/management']

            async def four_paths(expected=None):
                for path in reads:
                    page = await call('GET', path)
                    selected = page if path == reads[0] else next(row for row in page['items'] if row['id'] == task['id'])
                    if expected is None:
                        named(selected['alignment'])
                    else:
                        assert selected['alignment'] == expected
                    if path != reads[0]:
                        assert next(row for row in page['items'] if row['id'] == legacy['id'])['alignment'] == await raw(legacy['id'])
                        assert next(row for row in page['items'] if row['id'] == plain['id'])['alignment'] is None

            await four_paths()
            assert (await call('GET', '/api/v1/tasks/' + legacy['id']))['alignment'] == legacy_original
            checks.append('Four limited-role HTTP paths and create readback return real person/team scope names; V1 JSON exact and unclassified null preserved')

            async def business_digest():
                tables = ('workflow.task', 'workflow.task_alignment', 'workflow.task_target_alignment', 'workflow.task_key_result_alignment', 'workflow.task_metric_alignment', 'workflow.task_alignment_history', 'crm.sales_target', 'crm.review_objective', 'crm.review_key_result')
                return {table: await c.fetchval("SELECT md5(COALESCE(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text)::text,'[]')) FROM " + table + ' r') for table in tables}

            before_reads = await business_digest()
            await four_paths()
            kr_page = await call('GET', '/api/v1/review/key-results')
            assert kr_page['total'] == 2
            for row in kr_page['items']:
                assert row['owner_user_id'] == str(own['owner'])
                scope = row['scope']
                assert (scope['user_name'], scope['team_name']) == ((own['user_name'], None) if scope['scope_type'] == 'person' else (None, own['team_name']))
            assert await business_digest() == before_reads
            checks.append('Names derive from actual target/objective scope, not distinct KR/task owner; reads leave all task versions, links, history and target/OKR rows identical')

            # Archive an existing linked KR: the task function hides it, while the
            # KR list still exposes the authorized archived record as before.
            await c.execute("UPDATE crm.review_key_result SET status='archived' WHERE id=$1::uuid", ids['team']['kr'])
            archived_raw = await raw(task['id'])
            archived_task = await call('GET', '/api/v1/tasks/' + task['id'])
            assert without_names(archived_task['alignment']) == archived_raw
            assert archived_raw['restricted_key_result_count'] == 1
            archived_page = await call('GET', '/api/v1/review/key-results')
            archived = next(row for row in archived_page['items'] if row['id'] == ids['team']['kr'])
            assert archived_page['total'] == 2 and archived['status'] == 'archived' and archived['linkable'] is False
            assert archived['scope']['team_name'] == own['team_name']
            await c.execute("UPDATE crm.review_key_result SET status='active' WHERE id=$1::uuid", ids['team']['kr'])
            checks.append('Archived KR remains in list with name; task restricted objects/count exactly follow original function')

            # Cross-tenant IDs cannot enter saved links; raw list/detail cannot
            # leak the other workspace, even though its display names exist.
            await call('POST', '/api/v1/tasks', {**base, 'alignment_contract_version': 2,
                'alignment': {'target_ids': [foreign['objects']['person']['target']]}}, 403)
            await call('POST', '/api/v1/tasks', {**base, 'alignment_contract_version': 2,
                'alignment': {'key_result_ids': [foreign['objects']['team']['kr']]}}, 403)
            await call('GET', '/api/v1/review/key-results/' + foreign['objects']['person']['kr'], status=404)
            assert 'foreign_' not in json.dumps(await call('GET', '/api/v1/review/key-results'))
            checks.append('Cross-tenant target/KR rejected; foreign KR detail 404 and no foreign names in authorized list')

            # A directory RLS restriction can hide the display row without hiding
            # its target. LEFT JOIN must preserve the authorized object with null.
            await c.execute(f'''CREATE POLICY d03_names_fixture_hide ON platform.user_ref AS RESTRICTIVE FOR SELECT TO "{roles['api']}" USING (id <> '{own['user']}'::uuid)''')
            async with db.transaction(actor, readonly=True) as scoped:
                from sales_backend.repositories.task_alignment import TASK_ALIGNMENT_READ_SQL
                projected = await scoped.fetchval('SELECT ' + TASK_ALIGNMENT_READ_SQL + ' FROM workflow.task t WHERE t.id=$1::uuid', task['id'])
                from sales_backend.repositories.review_okr import ReviewOkrRepository
                missing_page = await ReviewOkrRepository().key_results(scoped)
            assert without_names(projected) == original
            for key in ('targets', 'key_results'):
                personal = next(x for x in projected[key] if x['scope']['scope_type'] == 'person')
                assert personal['scope']['user_name'] is None
            assert next(x for x in missing_page['items'] if x['scope']['scope_type'] == 'person')['scope']['user_name'] is None
            await c.execute('DROP POLICY d03_names_fixture_hide ON platform.user_ref')
            checks.append('Synthetic stricter directory RLS yields null name without removing authorized target/KR; no grant change')

            await c.execute("UPDATE config.account_permission_override SET effect='deny',scope_code=NULL WHERE workspace_id=$1 AND user_ref_id=$2 AND permission_code='target.read'", own['ws'], own['user'])
            hidden_raw = await raw(task['id'])
            assert hidden_raw['targets'] == hidden_raw['key_results'] == []
            assert hidden_raw['restricted_target_count'] == hidden_raw['restricted_key_result_count'] == 2
            await four_paths(hidden_raw)
            revoked_page = await call('GET', '/api/v1/review/key-results')
            assert revoked_page['items'] == [] and revoked_page['total'] == 0
            checks.append('Same ActorContext after target.read revocation: four task paths preserve original hidden arrays/counts, no object names; KR list empty')
            assert await business_digest() == before_reads
            assert await acl(c, roles) == original_acl
            final_exact = await exact(c, roles, stage)
            result = dict(status='passed_isolated_D03_scope_names', checks=checks, check_count=len(checks), http_count=http_count,
                samples=samples, source_sha256={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in PRODUCT_FILES},
                script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), migration_sha256=hashlib.sha256(sql.read_bytes()).hexdigest(),
                exact_four_role_verifier_before=exact_result, exact_four_role_verifier_after=final_exact,
                acl_unchanged=True, task_versions_links_and_business_rows_unchanged=True, database=name, roles=roles,
                business_auth='trusted synthetic ActorContext with actual limited API role, existing grants and RLS',
                production_or_existing_LOCAL_accessed=False, model_calls=False)
    finally:
        if c:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        for identity in roles.values():
            await admin.execute(f'DROP ROLE IF EXISTS "{identity}"')
        assert await admin.fetchval('SELECT count(*) FROM pg_database WHERE datname=$1', name) == 0
        assert await admin.fetchval('SELECT count(*) FROM pg_roles WHERE rolname=ANY($1::text[])', list(roles.values())) == 0
        await admin.close()
    result['cleanup'] = 'own UUID database and four roles dropped; catalog counts zero; shared private cluster retained'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    print(json.dumps(dict(status=result['status'], check_count=len(checks), http_count=http_count, output=str(output), sha256=hashlib.sha256(output.read_bytes()).hexdigest())))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', required=True)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--output', required=True)
    asyncio.run(verify(parser.parse_args()))
