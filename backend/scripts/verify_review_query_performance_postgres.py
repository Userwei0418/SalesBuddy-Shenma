# ruff: noqa: E501, S108, S608 -- Isolated synthetic DB, fixed SQL identifiers and explicit private socket.
"""Compare the frozen Review query to the live repository under actual RLS."""

import argparse
import asyncio
import hashlib
import io
import json
import traceback
from contextlib import asynccontextmanager, redirect_stdout
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from verify_review_targets_postgres import ROOT, grants, migrate_through

from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.review_overview import router
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.work_report import SHANGHAI, WorkReportError
from sales_backend.permission_context import current_feature
from sales_backend.repositories import review_overview as repository


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + '\n'


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


async def verify(args):
    socket = str(Path(args.socket).resolve())
    assert socket.startswith(('/tmp/sb-review-pg.', '/private/tmp/sb-review-pg.'))
    config = dict(host=socket, port=args.port, user='postgres', password='', passfile='/dev/null', ssl=False)
    suffix = uuid4().hex[:12]
    output = Path(args.output_dir) / ('run_' + suffix)
    output.mkdir(parents=True, exist_ok=False)
    old_path = Path(args.original_sql)
    old_sql = old_path.read_text().strip().rstrip(';')
    assert old_sql.startswith('/* review_current_opportunities_v1 */')
    source = ROOT / 'backend/src/sales_backend/repositories/review_overview.py'
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    name, role = 'sb_review_perf_' + suffix, 'sb_review_perf_reader_' + suffix
    result = dict(status='started', database=name, role=role, checks=[], timings={}, plans={}, http_pairs=[],
        original_sql_path=str(old_path), original_sql_sha256=hashlib.sha256(old_path.read_bytes()).hexdigest(),
        source_sha256=source_hash, script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        current_feature='work_report.read', production_or_existing_LOCAL_accessed=False, model_calls=False)
    c = None
    admin = await asyncpg.connect(database='postgres', **config)
    feature_token = current_feature.set('work_report.read')
    failure = None
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        c = await asyncpg.connect(database=name, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(c, 243)
        await c.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION')
        await grants(c, role)
        await c.execute('SET row_security=on')
        for typ in ('json', 'jsonb'):
            await c.set_type_codec(typ, schema='pg_catalog', encoder=json.dumps, decoder=json.loads, format='text')
        anchor = await c.fetchval("SELECT timezone('Asia/Shanghai',clock_timestamp())::date")
        quarter = (anchor.month - 1) // 3 + 1
        start = date(anchor.year, quarter * 3 - 2, 1)
        moment = datetime.combine(anchor, time(10), SHANGHAI)
        specs = {}
        for label in ('own', 'hidden', 'foreign'):
            ws = specs['own']['ws'] if label == 'hidden' else uuid4()
            team, user, customer = uuid4(), uuid4(), uuid4()
            if label != 'hidden':
                await c.execute('INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$2)', ws, 'perf_' + str(ws))
                await c.execute('UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1', ws)
                await c.execute("""UPDATE config.permission_role_grant g SET scope_code='self',team_ids='{}'
                  FROM config.permission_role r WHERE r.id=g.role_id AND r.workspace_id=$1
                  AND r.builtin_role_code='sales' AND g.permission_code IN ('visit.read','work_report.read','opportunity.read','customer.read')""", ws)
            await c.execute('INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,$3)', team, ws, label + '_team')
            await c.execute('INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)', user, ws, label + '_sales')
            await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'sales','self',$3)", ws, user, team)
            await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')", ws, user, team)
            await c.execute('INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,owner_user_ref_id,owner_team_id) VALUES($1,$2,$3,$3,$4,$4,$5)', customer, ws, label + '_customer', user, team)
            specs[label] = dict(ws=ws, team=team, user=user, customer=customer, opportunities=[])
        own = specs['own']
        actor = ActorContext(workspace_id=str(own['ws']), user_id=str(own['user']), role='sales', data_scope='self', team_ids=[str(own['team'])])
        for label, count in (('own', 33), ('hidden', 3), ('foreign', 3)):
            spec = specs[label]
            await c.execute("SELECT set_config('app.workspace_id',$1,false),set_config('app.user_ref_id',$2,false),set_config('app.role_code','sales',false),set_config('app.team_ids',$3,false)", str(spec['ws']), str(spec['user']), str(spec['team']))
            for i in range(count):
                opportunity = uuid4()
                spec['opportunities'].append(opportunity)
                status = 'won' if i % 9 == 0 else 'open'
                if label == 'own' and i == 30:
                    status = 'lost'
                if label == 'own' and i == 31:
                    status = 'cancelled'
                await c.execute("SELECT set_config('app.native_opportunity_create',$1,false)", str(opportunity))
                await c.execute("""INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,
                  amount,currency,probability,stage_code,status,sales_channel,created_by_user_ref_id)
                  VALUES($1,$2,$3,$4,$5,$6,1000,'CNY',$7,'qualified',$8,'direct',$5)""", opportunity, spec['ws'], spec['customer'], label + '_' + str(i).zfill(2), spec['user'], spec['team'], 70 if i % 2 == 0 else 30, status)
                if label == 'own' and i == 32:
                    await c.execute('UPDATE crm.opportunity SET deleted_at=clock_timestamp() WHERE id=$1', opportunity)
                if i != 7:
                    await c.execute("""INSERT INTO crm.opportunity_forecast(workspace_id,opportunity_id,year,quarter,
                      collection_amount,recognized_amount,collection_confidence) VALUES($1,$2,$3,$4,$5,$6,$7)""", spec['ws'], opportunity, anchor.year, quarter, 100 + i, 200 + i, 'low' if i == 9 else 'high')
        await c.execute("SELECT set_config('app.native_opportunity_create','',false)")
        form = await c.fetchval("SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1")
        visits, links, categories = [], [], {}
        expected = {str(ident): None for ident in own['opportunities'][:30]}

        def add_visit(label, *, direct=None, linked=(), status='confirmed', days=5, deleted=False, category='unrelated', visible=False, customer_id=None):
            spec, ident = specs[label], uuid4()
            when = moment - timedelta(days=days)
            visits.append((ident, spec['ws'], customer_id or spec['customer'], direct, spec['user'], spec['team'], form, status, when,
                when if status == 'archived' else None, when if deleted else None))
            links.extend((spec['ws'], ident, opportunity) for opportunity in linked)
            categories.setdefault(category, []).append(str(ident))
            if visible and not deleted and status in ('confirmed', 'archived'):
                for opportunity in set([*linked, *([direct] if direct else [])]):
                    if str(opportunity) in expected:
                        prior = expected[str(opportunity)]
                        expected[str(opportunity)] = when if prior is None else max(prior, when)
            return ident

        for i, opportunity in enumerate(own['opportunities'][:30]):
            if i < 29:
                add_visit('own', direct=opportunity, days=12, category='direct_with_normalized_link', visible=True)
            if i < 28:
                add_visit('own', linked=[opportunity], days=4 + i % 3, category='linked_only', visible=True)
                shared = [opportunity, own['opportunities'][(i + 1) % 28]]
                add_visit('own', direct=opportunity, linked=[opportunity], days=8, category='dual_same_visit', visible=True)
                add_visit('own', linked=shared, days=9, category='multi_link_only', visible=True)
                if i % 7 == 0:
                    add_visit('own', direct=opportunity, status='archived', days=2, category='archived', visible=True)
            add_visit('own', direct=opportunity, linked=[opportunity], status=('recording', 'transcribing', 'pending_confirm')[i % 3], days=0, category='draft_excluded')
            add_visit('own', direct=opportunity, linked=[opportunity], deleted=True, days=0, category='deleted_excluded')
            add_visit('hidden', linked=[opportunity], days=0, category='linked_unreadable', customer_id=own['customer'])
        for opportunity in specs['hidden']['opportunities']:
            add_visit('hidden', direct=opportunity, days=0, category='hidden_direct_unreadable')
        while sum(row[1] == own['ws'] for row in visits) < 600:
            add_visit('own', days=1, category='unrelated_customer_only')
        for i in range(60):
            opportunity = specs['foreign']['opportunities'][i % 3]
            add_visit('foreign', direct=opportunity, linked=[opportunity], days=0, category='cross_tenant')
        await c.executemany("""INSERT INTO activity.visit(id,workspace_id,customer_id,opportunity_id,recorder_user_ref_id,
          recorder_team_id,created_by_user_ref_id,form_version_id,status,interaction_at,archived_at,deleted_at,follow_up_record,next_action)
          VALUES($1,$2,$3,$4,$5,$6,$5,$7,$8,$9,$10,$11,'Synthetic performance follow up','Synthetic next action')""", visits)
        await c.executemany('INSERT INTO activity.visit_opportunity(workspace_id,visit_id,opportunity_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING', links)
        for kind, amount in (('collection', 10000), ('collection_ppl', 12000), ('forecast_recognized', 14000)):
            await c.execute("""INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,user_ref_id,kind,amount,
              updated_by_user_ref_id,period_type,period_start,period_end) VALUES($1,$2,'person',$3,$4,$5,$3,'quarter',$6,
              ($6::date+interval '3 months - 1 day')::date)""", own['ws'], anchor.year, own['user'], kind, amount, start)
        await c.execute('ANALYZE')
        result['fixture'] = dict(visible_opportunities=30, own_workspace_visits=600, foreign_visits=60,
            all_opportunities=39, links=await c.fetchval('SELECT count(*) FROM activity.visit_opportunity'), categories={k: len(v) for k, v in categories.items()},
            expected_last_follow_up_at=expected,
            constraint_note='V243 requires every direct opportunity to have exactly one matching normalized link; multi-link visits use direct=NULL. No constraints or triggers disabled.')
        (output / 'FIXTURE.json').write_text(encoded(result['fixture']))
        scope = SimpleNamespace(kind='self', member_ids=[own['user']], team_id=None)

        class Capture:
            async def fetch(self, sql, *values):
                self.sql, self.values = sql, values
                return []

        capture = Capture()
        await repository.opportunity_rows(capture, actor, scope, year=anchor.year, quarter=quarter)
        new_sql, values = capture.sql, capture.values
        assert old_sql.strip() != new_sql.strip(), 'candidate query has not changed'
        (output / 'OPPORTUNITY_ORIGINAL.sql').write_text(old_sql + '\n')
        (output / 'OPPORTUNITY_CANDIDATE.sql').write_text(new_sql + '\n')
        result['candidate_sql_sha256'] = hashlib.sha256(new_sql.encode()).hexdigest()

        @asynccontextmanager
        async def scoped():
            async with c.transaction(readonly=True, isolation='repeatable_read'):
                await c.execute(f'SET LOCAL ROLE "{role}"')
                await set_request_context(c, actor)
                await repository.assert_read_context(c, actor)
                assert await c.fetchval("SELECT current_setting('app.authorized_feature')") == 'work_report.read'
                assert not await c.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
                yield c

        async def compare(label, *, measure=False):
            async with scoped():
                rows = {}
                for variant, sql in (('original', old_sql), ('candidate', new_sql)):
                    began = perf_counter()
                    rows[variant] = [dict(r) for r in await c.fetch(sql, *values)]
                    result['timings'].setdefault(label, {})[variant + '_wall_ms'] = (perf_counter() - began) * 1000
                assert rows['original'] == rows['candidate'], label + ' complete rows differ'
                (output / (label.upper() + '_ROWS.json')).write_text(encoded(rows['candidate']))
                result['checks'].append(label + ': original/candidate complete rows equal (' + str(len(rows['candidate'])) + ')')
                if measure:
                    assert len(rows['candidate']) == 30
                    assert len({r['id'] for r in rows['candidate']}) == 30
                    for row in rows['candidate']:
                        assert row['last_follow_up_at'] == expected[row['id']], (row['name'], row['last_follow_up_at'], expected[row['id']])
                    hidden = await c.fetchval("SELECT security.authorization_visit('visit.read',$1::uuid) OR security.authorization_visit('work_report.read',$1::uuid)", categories['linked_unreadable'][0])
                    assert hidden is False
                    for variant, sql in (('original', old_sql), ('candidate', new_sql)):
                        plan = await c.fetchval('EXPLAIN (ANALYZE,BUFFERS,VERBOSE,FORMAT JSON) ' + sql, *values)
                        (output / ('EXPLAIN_' + variant.upper() + '.json')).write_text(encoded(plan))
                        result['plans'][variant] = {k: plan[0][k] for k in ('Planning Time', 'Execution Time')}
                        result['plans'][variant]['root_total_cost'] = plan[0]['Plan']['Total Cost']
                        result['plans'][variant]['root_actual_rows'] = plan[0]['Plan']['Actual Rows']
                return rows['candidate']

        baseline = await compare('authorized_baseline', measure=True)
        result['checks'].append('Expected direct/linked/dual/multi-link/archive maxima; drafts/deleted/unreadable/cross-tenant excluded; no duplicated opportunity rows')
        print(encoded({'phase': 'query_comparison_passed', 'plans': result['plans'], 'output': str(output)}), flush=True)
        mode = 'candidate'

        class Proxy:
            def __getattr__(self, key):
                return getattr(c, key)

            async def fetch(self, sql, *params, **kwargs):
                if sql.lstrip().startswith('/* review_current_opportunities_v1 */') and mode == 'original':
                    assert sql == new_sql and params == values
                    sql = old_sql
                return await c.fetch(sql, *params, **kwargs)

        class DB:
            settings = SimpleNamespace()

            @asynccontextmanager
            async def transaction(self, current_actor, readonly=False, isolation='read_committed'):
                assert current_actor == actor and readonly and isolation == 'repeatable_read'
                yield Proxy()  # HTTP pair executes inside the same scoped snapshot.

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_database] = lambda: DB()
        app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=actor)

        @app.exception_handler(WorkReportError)
        async def error(request, exc):
            return JSONResponse({'code': exc.code, 'detail': exc.detail}, status_code=exc.status)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic') as client:
            async def http_pair(endpoint, **params):
                nonlocal mode
                pair = {}
                async with scoped():
                    for variant in ('original', 'candidate'):
                        mode = variant
                        response = await client.get('/api/v1/review/' + endpoint, params=dict(scope='self', anchor_date=str(anchor), period_type='day', **params))
                        assert response.status_code == 200, (endpoint, response.status_code, response.text)
                        pair[variant] = response.json()
                    assert pair['original'] == pair['candidate'], (endpoint, params, 'HTTP projections differ')
                result['http_pairs'].append(dict(endpoint=endpoint, params=params, identical=True, sha256=digest(pair['candidate'])))
                (output / ('HTTP_PAIR_' + str(len(result['http_pairs'])) + '.json')).write_text(encoded(pair['candidate']))
                return pair['candidate']

            overview = await http_pair('overview')
            page1 = await http_pair('opportunities', limit=3, offset=0)
            page2 = await http_pair('opportunities', limit=3, offset=3)
            open_count = sum(row['status'] == 'open' for row in baseline)
            assert page1['known_total'] == page2['known_total'] == open_count and open_count > 6
            assert len(page1['items']) == len(page2['items']) == 3
            assert not {r['id'] for r in page1['items']} & {r['id'] for r in page2['items']}
            result['checks'].append('Same-snapshot ASGI overview complete response and two pages equal; metric aggregates unchanged and known_total computed before page slicing')
            result['metric_values'] = {row['metric_code']: row['value'] for row in overview['metrics']}
            await c.execute('INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)', own['ws'], own['user'])
            for permission in ('visit.read', 'opportunity.read'):
                await c.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,$3,'deny')", own['ws'], own['user'], permission)
                revoked = await compare(permission.replace('.', '_') + '_revoked')
                if permission == 'visit.read':
                    assert len(revoked) == 30 and all(row['last_follow_up_at'] is None for row in revoked)
                    await http_pair('opportunities', filter='stale', limit=3)
                else:
                    assert revoked == []
                    denied_page = await http_pair('opportunities', limit=3)
                    assert denied_page['items'] == [] and denied_page['known_total'] == 0
                await c.execute('DELETE FROM config.account_permission_override WHERE workspace_id=$1 AND user_ref_id=$2 AND permission_code=$3', own['ws'], own['user'], permission)
            result['checks'].append('Same actor source-grant revocation remains equivalent: no visit dates after visit denial, no opportunity rows after opportunity denial')
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash, 'repository changed during verification'
        result['status'] = 'passed_isolated_review_query_performance'
        result['check_count'] = len(result['checks'])
        result['http_count'] = len(result['http_pairs']) * 2
        result['performance_scope'] = 'Synthetic local fixture only; wall/EXPLAIN timing is observational, not a production latency claim'
        result['explain_execution_speed_ratio'] = result['plans']['original']['Execution Time'] / result['plans']['candidate']['Execution Time']
    except BaseException as exc:
        failure = exc
        result.update(status='failed', failure_type=type(exc).__name__, failure_message=str(exc), traceback=traceback.format_exc())
    finally:
        current_feature.reset(feature_token)
        if c:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        result['cleanup'] = dict(database_absent=await admin.fetchval('SELECT count(*) FROM pg_database WHERE datname=$1', name) == 0,
            role_absent=await admin.fetchval('SELECT count(*) FROM pg_roles WHERE rolname=$1', role) == 0, private_cluster_retained=True)
        await admin.close()
        (output / 'RESULT.json').write_text(encoded(result))
        print(encoded(dict(status=result['status'], result=str(output / 'RESULT.json'), sha256=hashlib.sha256((output / 'RESULT.json').read_bytes()).hexdigest())), flush=True)
    if failure:
        raise failure


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', required=True)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--original-sql', required=True)
    parser.add_argument('--output-dir', required=True)
    asyncio.run(verify(parser.parse_args()))
