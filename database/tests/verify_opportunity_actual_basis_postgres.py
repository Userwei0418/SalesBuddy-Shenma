"""V200 finance decisions/source dependencies, only in disposable local PG.

Requires explicit local Unix socket. Creates random synthetic databases/roles;
never uses production DSNs, existing business databases, remote APIs or backups.
"""
from __future__ import annotations
import asyncio
from datetime import date
from decimal import Decimal
import json
import hashlib
import os
from uuid import uuid4
import asyncpg
from verify_feishu_dependency_sync_postgres import fixture, source, ROOT, SCHEMAS
from verify_feishu_customer_member_sync_postgres import denied, events, rollback, watermark
from verify_fde_postgres import context
from verify_phone_first_login_postgres import effective_acl
from verify_runtime_grants_postgres import migrate_through
from migrate import apply, item

MIGRATION=ROOT/'migrations/V200__opportunity_actual_basis_and_finance_sync.sql'
TABLE='crm.opportunity_actual_basis'
HELPERS=('crm.guard_opportunity_actual_basis()', 'ops.capture_opportunity_finance_dependency()',
 'ops.feishu_source_before_opportunity_finance(uuid,text,uuid)',
 'security.grant_opportunity_actual_basis_runtime()',
 'security.reconcile_runtime_grants()', 'security.reconcile_runtime_grants_before_opportunity_actual_basis()')

async def grant_role(c, role, service='api'):
    await c.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
    rule=json.loads((ROOT.parent/'tools/installation/runtime_permissions.json').read_text())['services'][service]
    for schema in rule['schemas']:
        await c.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
    for table, rights in rule['tables'].items():
        await c.execute(f'GRANT {",".join(rights)} ON {table} TO "{role}"')
    for table, grants in rule['columns'].items():
        for right, cols in grants.items():
            await c.execute(f'GRANT {right}({",".join(cols)}) ON {table} TO "{role}"')
    for sig in rule['functions']:
        if await c.fetchval('SELECT to_regprocedure($1) IS NOT NULL',sig):
            await c.execute(f'GRANT EXECUTE ON FUNCTION {sig} TO "{role}"')

async def management(c,f):
    await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,'administrator','workspace')",f['ws'],f['user'])
    await c.execute('UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1',f['ws'])
    await context(c,f['ws'],f['user'],'administrator')

async def decision(c,f,*,source_code='entries', history=None, kind='recognized', year=2026, quarter=3, fingerprint='c'*64, reason='Synthetic reviewed evidence'):
    return await c.fetchval('''INSERT INTO crm.opportunity_actual_basis(workspace_id,opportunity_id,year,quarter,kind,
        source,selected_history_id,source_fingerprint,reason,decided_by_user_ref_id)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING id''',
        f['ws'],f['opportunity'],year,quarter,kind,source_code,history,fingerprint,reason,f['user'])

async def actual(c,f,*,opp='opportunity',value=Decimal('0.00'),kind='collection'):
    return await c.fetchval('''INSERT INTO crm.customer_actual(workspace_id,customer_id,opportunity_id,kind,amount,
        occurred_on,source_ref,request_id,confirmed_by_user_ref_id) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id''',
        f['ws'], f['customer'],f[opp] if opp else None,kind,value,date(2026,9,29),'synthetic-'+uuid4().hex,uuid4(),f['user'])

async def parents(c,after,expected,*,origin='business',direct=()):
    rows=await events(c,after)
    p=[r for r in rows if r['object_kind']=='opportunity']
    assert sorted([(r['workspace_id'],r['object_id']) for r in p],key=str)==sorted(expected,key=str),rows
    assert all(not r['first_formal_create'] and r['historical'] and not r['notification_planned'] and r['queue_origin']==origin for r in p),p
    assert all(r['object_kind'] in ('opportunity',*direct) for r in rows),rows
    assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM ops.feishu_event WHERE sequence_no>$1 AND object_kind='opportunity' AND notification_snapshot IS NOT NULL)",after)

async def business(c):
    return {table:await c.fetchval(f'SELECT jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text FROM {table} t')
        for table in ('crm.customer','crm.opportunity','crm.customer_actual','crm.opportunity_period_actual_snapshot','ops.feishu_event')}

def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

async def rls_fingerprint(c):
    tables=[dict(r) for r in await c.fetch("""SELECT n.nspname||'.'||c.relname AS name,
        c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p') ORDER BY 1""",SCHEMAS)]
    policies=[dict(r) for r in await c.fetch("""SELECT schemaname,tablename,policyname,
        permissive,roles::text[],cmd,qual,with_check FROM pg_policies
        WHERE schemaname=ANY($1::text[]) ORDER BY 1,2,3""",SCHEMAS)]
    for row in policies: row['roles']=sorted(row['roles'])
    return fingerprint({'tables':tables,'policies':policies})

async def verify():
    host=os.environ.get('PGHOST','')
    if not host.startswith('/'):
        raise ValueError('Explicit local Unix-socket PGHOST required')
    config=dict(host=host,port=int(os.environ.get('PGPORT','5432')),user=os.environ.get('PGUSER','postgres'),password='',passfile='/dev/null',ssl=False)
    admin=await asyncpg.connect(database='postgres',**config)
    suffix=uuid4().hex[:12]; db='sb_actual_basis_'+suffix
    roles={k:'sb_basis_'+k+'_'+suffix for k in ('api','late','worker','reader')}
    c=None; checks=[]; contracts={}
    try:
        await admin.execute(f'CREATE DATABASE "{db}" TEMPLATE template0')
        c=await asyncpg.connect(database=db,**config)
        await migrate_through(c,195)
        for version in (197,198,199):
            await apply(c,item(f'V{version}',next((ROOT/'migrations').glob(f'V{version}__*.sql'))),ROOT)
        a,b=await fixture(c,'BASIS_A'),await fixture(c,'BASIS_B')
        await management(c,a); await management(c,b); await context(c,a['ws'],a['user'],'administrator')
        await grant_role(c,roles['api']); await grant_role(c,roles['worker'],'worker')
        await c.execute(f'CREATE ROLE "{roles["reader"]}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await c.execute(f'GRANT USAGE ON SCHEMA crm,security TO "{roles["reader"]}"')
        await c.execute(f'GRANT SELECT ON crm.customer_actual,crm.opportunity TO "{roles["reader"]}"')
        for schema in ('ops','security','crm'):
            await c.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT EXECUTE ON FUNCTIONS TO "{roles["api"]}",salegent_feishu_worker')
        await c.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA crm GRANT ALL ON TABLES TO "{roles["api"]}",salegent_feishu_worker')
        contracts['production_before_rls']=await rls_fingerprint(c)
        before=await business(c)
        source_meta=tuple(await c.fetchrow("SELECT oid,proowner,proacl::text FROM pg_proc WHERE oid='ops.feishu_source(uuid,text,uuid)'::regprocedure"))
        old_sources={kind:await source(c,a,kind) for kind in ('opportunity','customer','visit','task','actual','forecast','period_actual_snapshot')}
        await apply(c,item('V200',MIGRATION),ROOT)
        await c.execute('SET row_security=on')
        contracts['production_after_rls']=await rls_fingerprint(c)
        contracts['production_after_ledger']=fingerprint(dict(await c.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum')))
        assert await business(c)==before
        assert source_meta==tuple(await c.fetchrow("SELECT oid,proowner,proacl::text FROM pg_proc WHERE oid='ops.feishu_source(uuid,text,uuid)'::regprocedure"))
        assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM ops.schema_migration WHERE version='V196')")
        for kind,old in old_sources.items():
            new=await source(c,a,kind)
            assert {k:v for k,v in new.items() if not k.startswith('finance_') and (k!='workspace_id' or k in old)}==old
        checks.append('production_chain_without_v196_preserves_business_queue_source_entry_identity_and_all_existing_source_fields')
        for role in (*roles.values(),'salegent_feishu_worker'):
            if not await c.fetchval('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)',role): continue
            for sig in HELPERS:
                assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')",role,sig),(role,sig)
        assert not await c.fetchval("SELECT has_table_privilege('salegent_feishu_worker',$1,'SELECT')",TABLE)
        assert await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT')",roles['api'],TABLE)
        for role in ('api','worker','reader'):
            assert await c.fetchval("SELECT has_table_privilege($1,$2,'SELECT')",roles[role],TABLE)
            assert not await c.fetchval("SELECT has_table_privilege($1,$2,'DELETE')",roles[role],TABLE)
        assert not await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT')",roles['reader'],TABLE)
        checks.append('hostile_default_acl_removed_private_helpers_unexposed_runtime_rights_narrow_and_no_deletes')
        async with rollback(c):
            n=await watermark(c); d=await decision(c,a,source_code='historical',history=a['period_actual_snapshot'])
            await parents(c,n,[(a['ws'],a['opportunity'])])
            audit=await c.fetchrow("SELECT actor_user_ref_id,changed_fields FROM ops.audit_log WHERE object_id=$1 AND action_code='crm.opportunity_actual_basis.insert'",str(d))
            assert audit and audit['actor_user_ref_id']==a['user']
            raw=await source(c,a,'opportunity')
            assert raw['finance_decisions'][0]['selected_history_id']==str(a['period_actual_snapshot'])
            assert raw['finance_company_code']==str(a['ws'])
            assert isinstance(raw['finance_actuals'][0]['amount'],str) and isinstance(raw['finance_history'][0]['raw_amount'],str)
            assert len(raw['finance_actuals'])==len(raw['finance_history'])==len(raw['finance_decisions'])==1
            n=await watermark(c)
            assert await c.execute('UPDATE crm.opportunity_actual_basis SET version_no=version_no+1,updated_at=clock_timestamp() WHERE id=$1',d)=='UPDATE 0'
            await parents(c,n,[])
            await denied(c,"UPDATE crm.opportunity_actual_basis SET reason='Changed' WHERE id=$1",d,code='40001')
            await denied(c,'UPDATE crm.opportunity_actual_basis SET year=2025,version_no=version_no+1 WHERE id=$1',d,code='23514')
            await c.execute("UPDATE crm.opportunity_actual_basis SET source='entries',selected_history_id=NULL,reason='Choose entries',version_no=version_no+1 WHERE id=$1",d)
            await parents(c,n,[(a['ws'],a['opportunity'])])
            assert await c.fetchval('SELECT version_no FROM crm.opportunity_actual_basis WHERE id=$1',d)==2
            assert await c.fetchval("SELECT count(*) FROM ops.audit_log WHERE object_id=$1 AND action_code LIKE 'crm.opportunity_actual_basis.%'",str(d))==2
        checks.append('historical_and_entries_decisions_emit_one_silent_parent_event_version_guard_noop_and_audit_are_transactional')
        async with rollback(c):
            n=await watermark(c)
            for kwargs in ({'source_code':'historical'},{'source_code':'entries','history':a['period_actual_snapshot']},
                           {'source_code':'historical','history':b['period_actual_snapshot']},
                           {'source_code':'historical','history':a['period_actual_snapshot'],'quarter':2},
                           {'fingerprint':'bad'},{'reason':' '},{'reason':'x'*1001}):
                try:
                    async with c.transaction(): await decision(c,a,**kwargs)
                except asyncpg.CheckViolationError: pass
                else: raise AssertionError(kwargs)
            await decision(c,a)
            try:
                async with c.transaction(): await decision(c,a)
            except asyncpg.UniqueViolationError: pass
            else: raise AssertionError('duplicate bucket')
            await parents(c,n,[(a['ws'],a['opportunity'])])
        checks.append('same_bucket_history_reference_and_unique_decision_constraints_reject_cross_tenant_wrong_period_invalid_fingerprint_reason')
        async with rollback(c):
            n=await watermark(c); aid=await actual(c,a,value=Decimal('9999999999999999.99'))
            await parents(c,n,[(a['ws'],a['opportunity'])],direct=('actual',))
            raw=await source(c,a,'opportunity'); row=next(x for x in raw['finance_actuals'] if x['id']==str(aid))
            assert row['amount']=='9999999999999999.99'
            n=await watermark(c)
            await c.execute("UPDATE crm.customer_actual SET voided_at=clock_timestamp(),voided_by_user_ref_id=$2,void_reason='Synthetic correction' WHERE id=$1",aid,a['user'])
            await parents(c,n,[(a['ws'],a['opportunity'])],direct=('actual',))
            row=next(x for x in (await source(c,a,'opportunity'))['finance_actuals'] if x['id']==str(aid))
            assert row['voided_at'] and row['updated_at']==row['voided_at']
            n=await watermark(c); await actual(c,a,opp=None); await parents(c,n,[],direct=('actual',))
            assert len((await source(c,a,'opportunity'))['finance_actuals'])==2
        checks.append('large_decimal_kept_as_text_void_changes_parent_and_retains_evidence_customer_only_amounts_not_allocated')
        async with rollback(c):
            n=await watermark(c)
            h=await c.fetchval('''INSERT INTO crm.opportunity_period_actual_snapshot(workspace_id,opportunity_id,year,quarter,kind,source_field,raw_amount,source_unit,source_record_id,import_batch_id)
                SELECT workspace_id,opportunity_id,year,quarter,kind,'second-source',9999999999999999.999999,'wan_cny',source_record_id,import_batch_id
                FROM crm.opportunity_period_actual_snapshot WHERE id=$1 RETURNING id''',a['period_actual_snapshot'])
            await parents(c,n,[(a['ws'],a['opportunity'])],origin='reconcile',direct=('period_actual_snapshot',))
            history=(await source(c,a,'opportunity'))['finance_history']
            assert len(history)==2 and next(x for x in history if x['id']==str(h))['raw_amount']=='9999999999999999.999999'
            await denied(c,'DELETE FROM crm.opportunity_period_actual_snapshot WHERE id=$1',h)
            n=await watermark(c)
            await c.execute("UPDATE crm.opportunity_forecast SET recognized_amount=11 WHERE id=$1",a['forecast'])
            await parents(c,n,[],direct=('forecast',))
        checks.append('multiple_history_sources_preserved_losslessly_immutable_history_and_forecast_no_redundant_parent_event')
        async with rollback(c):
            aid=await actual(c,a,value=Decimal('0.00'))
            raw=await source(c,a,'opportunity')
            assert next(x for x in raw['finance_actuals'] if x['id']==str(aid))['amount']=='0.00'
            assert not [x for x in (await source(c,b,'opportunity'))['finance_actuals'] if x['kind']=='collection']
            await c.execute('SET LOCAL ROLE salegent_feishu_worker')
            assert next(x for x in (await source(c,a,'opportunity'))['finance_actuals'] if x['id']==str(aid))['amount']=='0.00'
            await denied(c,'SELECT * FROM crm.opportunity_actual_basis')
            await denied(c,'SELECT ops.feishu_source_before_opportunity_finance($1,$2,$3)',a['sync'],'opportunity',a['opportunity'])
            await c.execute('RESET ROLE')
        checks.append('zero_value_is_explicit_distinct_from_no_fact_trusted_worker_source_works_without_direct_private_table_access')
        # Runtime inserts/updates exercise the real RLS and permission functions.
        async with rollback(c):
            await context(c,a['ws'],a['user'],'administrator'); await c.execute(f'SET LOCAL ROLE "{roles["api"]}"')
            d=await decision(c,a)
            assert await c.fetchval('SELECT count(*) FROM crm.opportunity_actual_basis WHERE workspace_id=$1',a['ws'])==1
            assert await c.fetchval('SELECT count(*) FROM crm.opportunity_actual_basis WHERE workspace_id=$1',b['ws'])==0
            await denied(c,"UPDATE crm.opportunity_actual_basis SET decided_by_user_ref_id=$2,reason='Bad actor',version_no=version_no+1 WHERE id=$1",d,a['second'])
            await denied(c,'DELETE FROM crm.opportunity_actual_basis WHERE id=$1',d)
            await denied(c,'SELECT ops.capture_opportunity_finance_dependency()')
            await context(c,a['ws'],a['user'],'manager')
            assert await c.execute("UPDATE crm.opportunity_actual_basis SET reason='Manager must not confirm',version_no=version_no+1 WHERE id=$1",d)=='UPDATE 0'
            try:
                async with c.transaction(): await decision(c,a,quarter=4)
            except asyncpg.InsufficientPrivilegeError: pass
            else: raise AssertionError('manager write')
            await context(c,a['ws'],a['second'],'administrator')
            try:
                async with c.transaction(): await decision(c,{**a,'user':a['second']},quarter=4)
            except asyncpg.InsufficientPrivilegeError: pass
            else: raise AssertionError('forged active role')
            await c.execute('RESET ROLE')
        checks.append('real_limited_api_rls_management_only_cross_tenant_hidden_actor_guard_and_private_helper_denial')
        async with rollback(c):
            await context(c,a['ws'],a['user'],'administrator')
            d=await decision(c,a)
            await context(c,b['ws'],b['user'],'administrator'); await c.execute(f'SET LOCAL ROLE "{roles["api"]}"')
            assert await c.fetchval('SELECT count(*) FROM crm.opportunity_actual_basis')==0
            assert await c.execute("UPDATE crm.opportunity_actual_basis SET reason='Foreign',version_no=version_no+1 WHERE id=$1",d)=='UPDATE 0'
            await c.execute('RESET ROLE')
            # Source worker is independent from ambient tenant context.
            assert len((await source(c,a,'opportunity'))['finance_decisions'])==1
            assert not (await source(c,b,'opportunity'))['finance_decisions']
        checks.append('explicit_source_workspace_filter_and_rls_isolate_identical_buckets_between_companies')
        await context(c,a['ws'],a['user'],'administrator')
        async with rollback(c):
            n=await watermark(c)
            d=await decision(c,a)
            await c.execute("UPDATE ops.feishu_event SET status='running',lease_token=$3,locked_until=clock_timestamp()+interval '1 minute' WHERE sequence_no>$1 AND object_id=$2",n,a['opportunity'],uuid4())
            await c.execute("UPDATE crm.opportunity_actual_basis SET reason='Second evidence',version_no=version_no+1 WHERE id=$1",d)
            await parents(c,n,2*[(a['ws'],a['opportunity'])])
            n=await watermark(c); await c.execute('DELETE FROM crm.opportunity_actual_basis WHERE id=$1',d)
            await parents(c,n,[(a['ws'],a['opportunity'])])
        checks.append('running_or_pending_parent_does_not_swallow_successor_and_maintenance_decision_delete_invalidates_parent')
        async with rollback(c):
            # Business APIs cannot rewrite the actual ledger. A maintenance
            # repair in this rollback-only fixture proves OLD and NEW routing.
            before_parent=await c.fetchval('SELECT to_jsonb(o)::text FROM crm.opportunity o WHERE id=$1',a['opportunity'])
            await c.execute('ALTER TABLE crm.customer_actual DISABLE TRIGGER actual_immutable')
            n=await watermark(c)
            await c.execute("UPDATE crm.customer_actual SET opportunity_id=$2 WHERE id=$1",a['actual'],a['other_opportunity'])
            await parents(c,n,[(a['ws'],a['opportunity']),(a['ws'],a['other_opportunity'])],direct=('actual',))
            n=await watermark(c)
            await c.execute("UPDATE crm.customer_actual SET opportunity_id=NULL WHERE id=$1",a['actual'])
            await parents(c,n,[(a['ws'],a['other_opportunity'])],direct=('actual',))
            assert await c.fetchval('SELECT to_jsonb(o)::text FROM crm.opportunity o WHERE id=$1',a['opportunity'])==before_parent
            await c.execute('ALTER TABLE crm.opportunity_period_actual_snapshot DISABLE TRIGGER period_actual_snapshot_immutable')
            n=await watermark(c)
            await c.execute('DELETE FROM crm.opportunity_period_actual_snapshot WHERE id=$1',a['period_actual_snapshot'])
            await parents(c,n,[(a['ws'],a['opportunity'])],origin='reconcile')
        checks.append('rollback_only_maintenance_parent_relink_null_and_history_delete_capture_old_new_without_mutating_opportunity')
        async with rollback(c):
            n=await watermark(c)
            await c.execute("SELECT set_config('app.feishu_historical_import','on',true)")
            await actual(c,a)
            await parents(c,n,[(a['ws'],a['opportunity'])],origin='reconcile',direct=('actual',))
        checks.append('historical_import_origin_preserved_for_actual_parent_and_source_evidence')
        for permission in ('actual.create','access.console','actual.read'):
            async with rollback(c):
                await context(c,a['ws'],a['user'],'administrator')
                d=await decision(c,a)
                await c.execute('INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2) ON CONFLICT DO NOTHING',a['ws'],a['user'])
                await c.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,$3,'deny')",a['ws'],a['user'],permission)
                await c.execute(f'SET LOCAL ROLE "{roles["api"]}"')
                await c.execute('SELECT security.authorization_refresh()')
                assert await c.execute("UPDATE crm.opportunity_actual_basis SET reason='Denied',version_no=version_no+1 WHERE id=$1",d)=='UPDATE 0'
                if permission=='actual.read':
                    assert await c.fetchval('SELECT count(*) FROM crm.opportunity_actual_basis')==0
                await c.execute('RESET ROLE')
        checks.append('real_permission_denials_for_console_actual_create_and_actual_read_override_management_role')
        async with rollback(c):
            await context(c,a['ws'],a['user'],'administrator')
            d=await decision(c,a)
            await c.execute("UPDATE platform.workspace SET status='suspended' WHERE id=$1",a['ws'])
            await c.execute(f'SET LOCAL ROLE "{roles["api"]}"')
            assert await c.fetchval('SELECT count(*) FROM crm.opportunity_actual_basis')==0
            assert await c.execute("UPDATE crm.opportunity_actual_basis SET reason='Suspended',version_no=version_no+1 WHERE id=$1",d)=='UPDATE 0'
            await c.execute('RESET ROLE')
        checks.append('inactive_company_blocks_existing_decision_read_and_update')
        async with rollback(c):
            await c.execute("UPDATE crm.customer SET data_kind='demo' WHERE id=$1",a['customer'])
            raw=await source(c,a,'opportunity')
            assert raw.get('excluded') or raw.get('deleted')
            assert 'finance_actuals' not in raw
        checks.append('excluded_projection_returns_before_financial_details_are_attached')
        # Direct finance concurrency: no FK KEY SHARE -> FOR UPDATE upgrade deadlock.
        x=await asyncpg.connect(database=db,**config); y=await asyncpg.connect(database=db,**config)
        try:
            await context(x,a['ws'],a['user'],'administrator'); await context(y,a['ws'],a['user'],'administrator')
            for cx in (x,y): await cx.execute("SET statement_timeout='5s'; SET lock_timeout='3s'")
            txs=[x.transaction(),y.transaction()]
            for tx in txs: await tx.start()
            # Hold both FK-equivalent shared locks before either insert. The
            # former FOR UPDATE implementation deterministically deadlocks here.
            for cx in (x,y):
                await cx.fetchval('SELECT id FROM crm.opportunity WHERE id=$1 FOR KEY SHARE',a['opportunity'])
            async def insert_and_commit(cx,tx):
                try:
                    rid=await actual(cx,a,value=Decimal('1.00'))
                    await tx.commit()
                    return rid
                except BaseException:
                    await tx.rollback()
                    raise
            n=await watermark(c)
            inserted=await asyncio.gather(insert_and_commit(x,txs[0]),insert_and_commit(y,txs[1]))
            assert len(set(inserted))==2
            await parents(c,n,2*[(a['ws'],a['opportunity'])],direct=('actual',))
        finally:
            await x.close(); await y.close()
        checks.append('concurrent_same_parent_actual_inserts_complete_without_lock_upgrade_deadlock')
        await grant_role(c,roles['late'])
        await c.execute('SELECT security.reconcile_runtime_grants()')
        assert await c.fetchval("SELECT has_table_privilege($1,$2,'INSERT')",roles['late'],TABLE)
        assert not await c.fetchval("SELECT has_table_privilege($1,$2,'DELETE')",roles['late'],TABLE)
        for sig in HELPERS:
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')",roles['late'],sig)
        checks.append('explicit_maintenance_reconcile_supports_late_runtime_without_private_functions_or_delete')
        assert await apply(c,item('V200',MIGRATION),ROOT)=='unchanged'
        n=await watermark(c); await migrate_through(c,200)
        await parents(c,n,[])
        assert await c.fetchval("SELECT EXISTS(SELECT 1 FROM ops.schema_migration WHERE version='V196')")
        await c.execute('SELECT security.grant_opportunity_actual_basis_runtime()')
        checks.append('checksum_replay_and_complete_canonical_migration_chain_preserve_events_and_contract')
        contracts['canonical_after_rls']=await rls_fingerprint(c)
        contracts['canonical_after_ledger']=fingerprint(dict(await c.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum')))
        print(json.dumps({'passed':len(checks),'checks':checks,'skipped':0,'contracts':contracts}))
    finally:
        if c: await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        for role in roles.values(): await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()

if __name__=='__main__': asyncio.run(verify())
