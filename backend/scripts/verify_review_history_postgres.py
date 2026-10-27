# ruff: noqa: E402, E501, S108 -- Disposable opt-in fixture, fixed SQL, explicit private Unix socket.
"""Disposable PostgreSQL evidence for V253. Uses synthetic dates, never saved DSNs."""
import asyncio
import io
import json
import sys
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'database/tests'), str(ROOT / 'database/scripts'), str(ROOT / 'backend/src')]
from verify_runtime_grants_postgres import migrate_through

from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext


async def main():
    socket = sys.argv[1]
    assert socket.startswith('/tmp/sb-review-pg.') and socket.endswith('/socket')
    config = dict(host=socket, port=int(sys.argv[2]), user='postgres', password='', passfile='/dev/null', ssl=False)
    admin = await asyncpg.connect(database='postgres', **config)
    name, role = 'review_history_' + uuid4().hex[:8], 'review_history_api_' + uuid4().hex[:8]
    worker = role + '_worker'
    c = None
    checks = []
    owner_evidence = {}
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        c = await asyncpg.connect(database=name, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(c, 250)
        # Managed installation/upgrade explicitly requires a superuser maintenance
        # administrator. Preserve legacy helper owners and FORCE RLS unchanged.
        owner_evidence['executor'] = dict(await c.fetchrow('''SELECT current_user AS role,
            r.rolsuper,r.rolbypassrls FROM pg_roles r WHERE r.rolname=current_user'''))
        assert owner_evidence['executor']['rolsuper']
        helpers = ['security.authorization_has(text)',
                   'security.authorization_subject(text,text,uuid,uuid)',
                   'security.authorization_opportunity(text,uuid)',
                   'security.authorization_customer(text,uuid)',
                   'security.reconcile_runtime_grants()']
        legacy = {sig: await c.fetchval('SELECT proowner FROM pg_proc WHERE oid=$1::regprocedure',sig)
                  for sig in helpers}
        protected = ['config.permission_role', 'config.permission_role_grant',
                     'config.account_permission_role', 'config.account_authorization']
        flags = {table: tuple(await c.fetchrow('''SELECT relrowsecurity,relforcerowsecurity
            FROM pg_class WHERE oid=$1::regclass''',table)) for table in protected}
        assert all(enabled and forced for enabled,forced in flags.values())
        await c.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        rules = json.loads((ROOT / 'tools/installation/runtime_permissions.json').read_text())['services']['api']
        for schema in rules['schemas']:
            await c.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
        for table, privileges in rules['tables'].items():
            await c.execute(f'GRANT {",".join(privileges)} ON {table} TO "{role}"')
        for table, grants in rules['columns'].items():
            for privilege, columns in grants.items():
                await c.execute(f'GRANT {privilege}({",".join(columns)}) ON {table} TO "{role}"')
        for signature in rules['functions']:
            if await c.fetchval('SELECT to_regprocedure($1) IS NOT NULL', signature):
                await c.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO "{role}"')
        audit_select_before = await c.fetchval("SELECT has_table_privilege($1,'ops.audit_log','SELECT')", role)
        activity_mode = len(sys.argv)>3 and sys.argv[3]=='activity'
        with redirect_stdout(io.StringIO()):
            await migrate_through(c, 254 if activity_mode else 253)
        for sig, old_owner in legacy.items():
            renamed = sig.replace('reconcile_runtime_grants()', 'reconcile_runtime_grants_before_review_history()')
            assert await c.fetchval('SELECT proowner FROM pg_proc WHERE oid=$1::regprocedure',renamed) == old_owner
        for table, prior in flags.items():
            assert tuple(await c.fetchrow('SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=$1::regclass',table)) == prior
        owner_evidence['legacy_helper_owners_preserved'] = True
        owner_evidence['force_rls_preserved'] = protected
        owner_evidence['new_definers'] = [dict(row) for row in await c.fetch('''SELECT p.oid::regprocedure::text AS function,
            pg_get_userbyid(p.proowner) AS owner,p.prosecdef,p.proconfig FROM pg_proc p WHERE p.oid IN
            ('ops.refresh_review_history()'::regprocedure,
             'security.review_metric_baseline(text,uuid[],date,integer,integer,integer)'::regprocedure,
             'security.review_day_update_status(text,uuid[],timestamp with time zone,timestamp with time zone)'::regprocedure)''')]
        assert all(row['owner']==owner_evidence['executor']['role'] and row['prosecdef']
                   and 'row_security=off' in row['proconfig'] for row in owner_evidence['new_definers'])
        checks.append('V250_to_V253_migration_with_existing_restricted_api_role')
        checks.append('supported_maintenance_owner_preserved_legacy_helpers_and_force_rls_call_chain')
        await c.execute(f'CREATE ROLE "{worker}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await c.execute(f'GRANT USAGE ON SCHEMA ops,security,agent TO "{worker}"')
        await c.execute(f'GRANT EXECUTE ON FUNCTION ops.claim_job(text,integer,text[],boolean),security.work_report_source_access(jsonb,jsonb) TO "{worker}"')
        await c.execute(f'GRANT SELECT,UPDATE ON agent.work_report_snapshot TO "{worker}"')
        await c.execute('SELECT security.reconcile_runtime_grants()')
        await c.execute('SELECT security.reconcile_runtime_grants()')
        for typ in ('json', 'jsonb'):
            await c.set_type_codec(typ, schema='pg_catalog', encoder=json.dumps, decoder=json.loads, format='text')
        now = await c.fetchval("SELECT clock_timestamp() AT TIME ZONE 'Asia/Shanghai'")
        today, yesterday = now.date(), now.date() - timedelta(days=1)
        year, quarter = today.year, (today.month-1)//3+1
        spaces = []
        for label in ('own', 'other'):
            ws, team, uid, customer, oid = [uuid4() for _ in range(5)]
            spaces.append((ws,team,uid,customer,oid))
            await c.execute('INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$2)', ws, str(ws))
            await c.execute('UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1', ws)
            await c.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'TEST','Synthetic')", team, ws)
            await c.execute('INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$3,$3)', uid, ws, str(uid))
            await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'manager','workspace',$3)", ws, uid, team)
            await c.execute("INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role) VALUES($1,$2,$3,'manager')", ws, team, uid)
            await c.execute('INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,owner_user_ref_id,owner_team_id) VALUES($1,$2,$3,$3,$4,$4,$5)', customer, ws, label, uid, team)
            actor = ActorContext(workspace_id=str(ws), user_id=str(uid), role='manager', data_scope='workspace', team_ids=[str(team)])
            async with c.transaction():
                await set_request_context(c, actor)
                await c.execute("INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,amount,currency,probability,stage_code,status,created_by_user_ref_id) VALUES($1,$2,$3,$4,$5,$6,100,'CNY',70,'proposal','open',$5)", oid,ws,customer,label,uid,team)
                await c.execute("INSERT INTO crm.opportunity_forecast(workspace_id,opportunity_id,year,quarter,collection_amount,recognized_amount,collection_confidence) VALUES($1,$2,$3,$4,100,200,'high')",ws,oid,year,quarter)
        ws,team,uid,customer,oid=spaces[0]
        actor=ActorContext(workspace_id=str(ws),user_id=str(uid),role='manager',data_scope='workspace',team_ids=[str(team)])
        assert await c.fetchval('SELECT ops.review_capture_healthy()') is True
        await c.execute(f'SET ROLE "{worker}"')
        await c.fetchval('SELECT ops.refresh_review_history()')
        await c.execute('RESET ROLE')
        checks.append('late_restricted_worker_scheduler_executes_without_business_context_or_table_grants')
        actual_start=await c.fetchval('SELECT complete_from FROM ops.review_history_capture')
        assert actual_start is not None
        checks.append('audit_capture_activates_only_after_actual_post_migration_monitor')
        assert not await c.fetchval("SELECT has_table_privilege($1,'ops.review_metric_snapshot','SELECT')", role)
        assert await c.fetchval("SELECT has_table_privilege($1,'ops.audit_log','SELECT')", role) == audit_select_before
        assert not await c.fetchval("SELECT has_function_privilege($1,'ops.refresh_review_history()','EXECUTE')", role)
        checks.append('api_internal_snapshots_and_capture_denied_existing_audit_acl_unchanged')

        async def mutate(sql, *args):
            async with c.transaction():
                await set_request_context(c, actor)
                return await c.execute(sql, *args)

        async def read(kind, subjects, start=None, end=None):
            await c.execute(f'SET ROLE "{role}"')
            try:
                async with c.transaction(readonly=True, isolation='repeatable_read'):
                    await set_request_context(c, actor)
                    if start is not None:
                        return await c.fetchval('SELECT security.review_day_update_status($1,$2,$3,$4)',kind,subjects,start,end)
                    return await c.fetchval('SELECT security.review_metric_baseline($1,$2,$3,$4,$5,1)',kind,subjects,yesterday,year,quarter)
            finally:
                await c.execute('RESET ROLE')

        begin=await c.fetchval("SELECT date_trunc('day',clock_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai'")
        end=await c.fetchval('SELECT clock_timestamp()')
        first=await read('team',[team],begin,end)
        assert first['history_coverage']['status'] != 'complete'
        assert 'before_complete_capture' in first['history_coverage']['reason_codes']
        checks.append('first_partial_day_remains_unknown_not_untouched')
        # Synthetic coverage epoch only inside this disposable database.
        await c.execute("UPDATE ops.review_history_capture SET complete_from=$1::timestamptz-interval '1 day'", begin)
        empty_start=await c.fetchval('SELECT clock_timestamp()')
        await mutate('UPDATE crm.opportunity SET updated_at=clock_timestamp() WHERE id=$1',oid)
        end=await c.fetchval('SELECT clock_timestamp()')
        no_op=await read('team',[team],empty_start,end)
        assert no_op['history_coverage']['status']=='complete' and no_op['updated_ids']==[]
        await mutate("UPDATE crm.opportunity_forecast SET collection_confidence='low' WHERE opportunity_id=$1",oid)
        end=await c.fetchval('SELECT clock_timestamp()')
        changed=await read('team',[team],empty_start,end)
        assert changed['updated_ids']==[str(oid)]
        checks.append('unchanged_save_excluded_and_confidence_only_write_detected')
        quote_start=await c.fetchval('SELECT clock_timestamp()')
        await mutate("INSERT INTO crm.opportunity_quote_reference(workspace_id,opportunity_id,reference_no,title,created_by_user_ref_id) VALUES($1,$2,'Q-test','Synthetic quote',$3)",ws,oid,uid)
        quote=await read('team',[team],quote_start,await c.fetchval('SELECT clock_timestamp()'))
        assert quote['updated_ids']==[str(oid)]
        checks.append('quotation_only_child_write_is_real_update_presence')
        foreign=await read('team',[spaces[1][1]],empty_start,end)
        assert foreign['updated_ids']==[] and foreign['history_coverage']['status']!='complete'
        checks.append('cross_tenant_scope_never_returns_ids_or_complete_coverage')
        original_trigger=await c.fetchval("""SELECT pg_get_triggerdef(oid) FROM pg_trigger
            WHERE tgrelid='crm.opportunity_forecast'::regclass AND tgname='business_audit'""")
        await c.execute('DROP TRIGGER business_audit ON crm.opportunity_forecast')
        await c.execute('''CREATE TRIGGER business_audit AFTER UPDATE OF collection_confidence
            ON crm.opportunity_forecast FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row()''')
        assert await c.fetchval('SELECT ops.review_capture_healthy()') is False
        narrowed=await read('team',[team],empty_start,await c.fetchval('SELECT clock_timestamp()'))
        assert narrowed['history_coverage']['status']!='complete'
        assert narrowed['history_coverage']['reason_codes']==['capture_trigger_unavailable']
        await c.fetchval('SELECT ops.refresh_review_history()')
        assert await c.fetchval('SELECT complete_from FROM ops.review_history_capture') is None
        await c.execute('DROP TRIGGER business_audit ON crm.opportunity_forecast')
        await c.execute(original_trigger)
        assert await c.fetchval('SELECT ops.review_capture_healthy()') is True
        checks.append('narrowed_update_only_column_trigger_cannot_claim_complete_capture')
        await c.execute('ALTER TABLE crm.opportunity_forecast DISABLE TRIGGER business_audit')
        await c.fetchval('SELECT ops.refresh_review_history()')
        assert await c.fetchval('SELECT complete_from FROM ops.review_history_capture') is None
        await c.execute('ALTER TABLE crm.opportunity_forecast ENABLE TRIGGER business_audit')
        await c.fetchval('SELECT ops.refresh_review_history()')
        assert await c.fetchval('SELECT complete_from FROM ops.review_history_capture') > actual_start
        checks.append('observed_disabled_capture_resets_epoch_and_reenable_starts_new_coverage')
        missing=await read('team',[team])
        assert missing['status']=='no_baseline'
        # Test clock injection alters this disposable function only. Product has
        # no runtime timestamp argument and cannot manufacture a prior-day row.
        original=await c.fetchval("SELECT pg_get_functiondef('ops.refresh_review_history()'::regprocedure)")
        synthetic=f"'{yesterday.isoformat()} 23:50:12+08'::timestamptz"
        await c.execute(original.replace('statement_timestamp()',synthetic))
        first_count=await c.fetchval('SELECT ops.refresh_review_history()')
        assert first_count==2
        assert await c.fetchval('SELECT ops.refresh_review_history()')==0
        await c.execute(original.replace('statement_timestamp()',f"'{today.isoformat()} 00:00:12+08'::timestamptz"))
        assert await c.fetchval('SELECT ops.refresh_review_history()')==0
        await c.execute(original)
        good=await read('team',[team,team])
        assert good['status']=='available' and len(good['rows'])==1
        assert good['rows'][0]['id']==str(oid) and good['snapshot_date']==str(yesterday)
        assert good['rows'][0]['collection_confidence']=='low'
        assert good['captured_at'] is not None
        checks.append('scheduled_clock_fixture_captures_real_inputs_once_and_never_backdates_late_retry')
        denied=await read('team',[spaces[1][1]])
        assert denied['status']=='incomparable' and 'rows' not in denied
        checks.append('baseline_current_permission_and_tenant_gate_does_not_leak_payload')
        await c.execute('UPDATE config.authorization_scope_revision SET revision=revision+1 WHERE workspace_id=$1',ws)
        changed=await read('team',[team])
        assert changed['status']=='incomparable' and changed['reason_code']=='authorization_scope_changed'
        assert 'rows' not in changed
        checks.append('changed_authorization_invalidates_old_amounts_without_zero_fallback')
        if activity_mode:
            from verify_review_update_activity_postgres import run_activity_checks
            checks.extend(await run_activity_checks(c, role, actor, spaces, begin))
        print(json.dumps({'status':'PASS','layer':'disposable_postgres','checks':checks,
                          'owner_evidence':owner_evidence,'synthetic_clock_fixture':True,'production_calls':0},ensure_ascii=False,indent=2))
    finally:
        if c:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}","{worker}"')
        await admin.close()


if __name__=='__main__':
    asyncio.run(main())
