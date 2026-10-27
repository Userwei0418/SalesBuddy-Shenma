# ruff: noqa: E501, S108, S608 -- Isolated synthetic DB, fixed SQL identifiers and explicit private socket.
"""Compare formal visit link queries under restricted RLS in a new synthetic DB."""

import argparse
import asyncio
import hashlib
import io
import json
import traceback
from contextlib import redirect_stdout
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
from verify_review_targets_postgres import ROOT, grants, migrate_through

from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.work_report import SHANGHAI
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
    assert 'explicit_links' not in old_sql and 'AS opportunities' in old_sql
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
            await migrate_through(c, 255)
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
        while sum(row[1] == own['ws'] for row in visits) < 220:
            add_visit('own', days=1, category='unrelated_customer_only')
        for i in range(8):
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
        result['fixture'] = dict(visible_opportunities=30, own_workspace_visits=sum(row[1] == own['ws'] for row in visits), foreign_visits=8,
            all_opportunities=39, links=await c.fetchval('SELECT count(*) FROM activity.visit_opportunity'), categories={k: len(v) for k, v in categories.items()},
            expected_last_follow_up_at=expected,
            constraint_note='V243 requires every direct opportunity to have exactly one matching normalized link; multi-link visits use direct=NULL. No constraints or triggers disabled.')
        (output / 'FIXTURE.json').write_text(encoded(result['fixture']))
        scope = SimpleNamespace(kind='self', member_ids=[own['user']], team_id=None)
        period = SimpleNamespace(start=moment-timedelta(days=30), cutoff=moment+timedelta(days=1))
        class Capture:
            async def fetch(self, sql, *values):
                self.sql, self.values = sql, values
                return []
        capture=Capture()
        await repository.formal_visits(capture,scope,period=period)
        new_sql,values=capture.sql,capture.values
        assert old_sql != new_sql
        async def compare(label):
            pair={}
            async with c.transaction(readonly=True,isolation='repeatable_read'):
                await c.execute(f'SET LOCAL ROLE "{role}"')
                await set_request_context(c,actor)
                assert not await c.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
                for variant,sql in [('original',old_sql),('candidate',new_sql)]:
                    started=perf_counter()
                    pair[variant]=[dict(row) for row in await c.fetch(sql,*values)]
                    result['timings'].setdefault(label,{})[variant+'_ms']=round((perf_counter()-started)*1000,3)
                assert pair['original']==pair['candidate'],label+' rows differ'
                result['checks'].append(dict(case=label,equal=True,rows=len(pair['candidate']),sha256=digest(pair['candidate'])))
            return pair['candidate']
        baseline=await compare('visible_direct_linked_customer_only')
        by_id={r['id']:r for r in baseline}
        forbidden=set(categories['draft_excluded']+categories['deleted_excluded']+categories['linked_unreadable']+categories['hidden_direct_unreadable']+categories['cross_tenant'])
        assert not set(by_id)&forbidden
        for visit_id in categories['unrelated_customer_only']:
            assert by_id[visit_id]['opportunities']==[],'Unrelated opportunity leaked from same customer'
        for row in baseline:
            ids=[str(x['id']) for x in row['opportunities']]
            assert len(ids)==len(set(ids)),'Direct plus normalized link counted twice'
            assert not set(ids)&set(map(str,specs['hidden']['opportunities']+specs['foreign']['opportunities']))
        assert any(len(r['opportunities'])>1 for r in baseline)
        result['checks'].append('direct/linked/dual/multi-link preserved; unrelated customer-only not inferred; draft/deleted/hidden/cross-tenant excluded')
        await c.execute('INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)',own['ws'],own['user'])
        for permission in ('opportunity.read','visit.read'):
            await c.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,$3,'deny')",own['ws'],own['user'],permission)
            changed=await compare(permission+'_revoked')
            if permission=='opportunity.read':
                assert all(r['opportunity_id'] is None and r['opportunity_name'] is None and not r['opportunities'] for r in changed)
            else:
                assert changed==[]
            await c.execute('DELETE FROM config.account_permission_override WHERE workspace_id=$1 AND user_ref_id=$2 AND permission_code=$3',own['ws'],own['user'],permission)
        result['status']='passed_formal_visit_link_equivalence_under_rls'
    except BaseException as exc:
        failure=exc
        result.update(status='failed',failure_type=type(exc).__name__,failure_message=str(exc),traceback=traceback.format_exc())
    finally:
        current_feature.reset(feature_token)
        if c:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        result['cleanup']=dict(database_absent=await admin.fetchval('SELECT count(*) FROM pg_database WHERE datname=$1',name)==0,role_absent=await admin.fetchval('SELECT count(*) FROM pg_roles WHERE rolname=$1',role)==0)
        await admin.close()
        (output/'RESULT.json').write_text(encoded(result))
        print(encoded(dict(status=result['status'],result=str(output/'RESULT.json'))),flush=True)
    if failure:
        raise failure


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--socket',required=True)
    parser.add_argument('--port',required=True,type=int)
    parser.add_argument('--original-sql',required=True)
    parser.add_argument('--output-dir',required=True)
    asyncio.run(verify(parser.parse_args()))
