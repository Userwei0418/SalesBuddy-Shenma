"""V195+V197 (without V196) and normal upgrade to targeted insight sync.

Creates only a randomized database on an explicit local PostgreSQL socket.
All fixtures are synthetic; no production DSN, backup, credential or API is read.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from uuid import uuid4

import asyncpg

from verify_feishu_customer_member_sync_postgres import (
    ROOT, denied, events, expect, fixture, rollback, watermark,
)
from verify_fde_postgres import context
from verify_phone_first_login_postgres import effective_acl
from verify_runtime_grants_postgres import migrate_through
from migrate import apply, item, migrate

MIGRATION = ROOT / 'migrations/V198__feishu_customer_insight_targeted_sync.sql'
MEMBER_MIGRATION = ROOT / 'migrations/V197__feishu_customer_member_targeted_sync.sql'
SIGNATURE = 'ops.capture_feishu_customer_insight_event()'
SCHEMAS = ['crm', 'ops', 'security', 'config', 'platform', 'common', 'activity', 'workflow', 'insight', 'agent', 'control']
sys.path.insert(0, str(ROOT.parent / 'backend/src'))
from sales_backend.domain.feishu_sync.projection import encode_scalar, project


async def insight_fixture(conn, label, *, connected=True):
    f = await fixture(conn, label, connected=connected)
    # The V197 fixture only enables Feishu/history. This fixture also exercises
    # the ordinary worker's risk-generation RLS under an enabled AI module.
    await conn.execute('''UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1
        AND module_code IN (SELECT module_code FROM control.permission_module
        WHERE permission_code IN ('risk.auto_review','risk.read'))''', f['ws'])
    f['rule'] = await conn.fetchval('''INSERT INTO config.rule_set(workspace_id,rule_code,name,
        rule_type,version_no,status,definition) VALUES($1,$2,'Synthetic quadrant rule','quadrant',1,'active','{}')
        RETURNING id''', f['ws'], str(uuid4()))
    f['opportunity'] = await conn.fetchval('''INSERT INTO crm.opportunity(workspace_id,customer_id,
        name,owner_user_ref_id,created_by_user_ref_id) VALUES($1,$2,'Synthetic insight project',$3,$3) RETURNING id''',
        f['ws'], f['customer'], f['user'])
    form = await conn.fetchval("SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1")
    f['visit'] = await conn.fetchval('''INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,
        recorder_user_ref_id,created_by_user_ref_id,form_version_id,status,interaction_at)
        VALUES($1,$2,$3,$4,$4,$5,'archived',clock_timestamp()) RETURNING id''',
        f['ws'], f['customer'], f['opportunity'], f['user'], form)
    return f


async def score(conn, f, customer='customer', *, value=70, subject='user'):
    return await conn.fetchval('''INSERT INTO insight.quadrant_score(workspace_id,customer_id,
        potential_score,relationship_score,quadrant_code,rule_set_id,input_snapshot,subject_user_ref_id)
        VALUES($1,$2,$3,60,'main_attack',$4,'{}',$5) RETURNING id''',
        f['ws'], f[customer], value, f['rule'], f[subject])


async def risk(conn, f, customer='customer', *, severity='high', title='Synthetic risk', linked=False):
    return await conn.fetchval('''INSERT INTO insight.risk(workspace_id,customer_id,opportunity_id,
        source_visit_id,risk_type_code,title,severity_code,owner_user_ref_id)
        VALUES($1,$2,$3,$4,'synthetic',$5,$6,$7) RETURNING id''', f['ws'], f[customer] if customer else None,
        f['opportunity'] if linked or customer is None else None,
        f['visit'] if linked else None, title, severity, f['user'])


async def source(conn, f, kind='customer', key='customer'):
    return json.loads(await conn.fetchval('SELECT ops.feishu_source($1,$2,$3)', f['sync'], kind, f[key]))


async def snapshot(conn):
    result = {}
    for table in ('crm.customer', 'crm.opportunity', 'activity.visit', 'insight.quadrant_score',
                  'insight.risk', 'config.feishu_connection', 'ops.feishu_event'):
        result[table] = await conn.fetchval(f'SELECT jsonb_agg(to_jsonb(r) ORDER BY id)::text FROM {table} r')
    result['functions'] = [tuple(r) for r in await conn.fetch('''SELECT p.oid,p.proowner,p.proacl::text,
        p.prosecdef,p.proconfig,p.prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname=ANY($1::text[]) AND p.proname<>'capture_feishu_customer_insight_event' ORDER BY p.oid''', SCHEMAS)]
    result['other_triggers'] = [tuple(r) for r in await conn.fetch('''SELECT oid,pg_get_triggerdef(oid)
        FROM pg_trigger WHERE NOT tgisinternal AND NOT(tgname='feishu_capture'
        AND tgrelid IN ('insight.quadrant_score'::regclass,'insight.risk'::regclass)) ORDER BY oid''')]
    result['policies'] = [tuple(r) for r in await conn.fetch('SELECT * FROM pg_policies ORDER BY schemaname,tablename,policyname')]
    result['rls'] = [tuple(r) for r in await conn.fetch('''SELECT c.oid,c.relrowsecurity,c.relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1::text[]) ORDER BY c.oid''', SCHEMAS)]
    result['default_acl'] = [tuple(r) for r in await conn.fetch('SELECT * FROM pg_default_acl ORDER BY oid')]
    return result


async def verify_basic_capture(conn):
    """Reusable rollback-only probe for a separately restored DB; applies no DDL.

    The caller supplies a maintenance connection. This creates fresh synthetic
    rows inside a savepoint/transaction and always rolls them back on exit.
    """
    async with rollback(conn):
        f = await insight_fixture(conn, 'PROBE_' + uuid4().hex[:8])
        await context(conn, f['ws'], f['user'], 'manager')
        for kind, create in (('quadrant_score', score), ('risk', risk)):
            before = await watermark(conn)
            rid = await create(conn, f)
            await expect(conn, before, [(f, 'customer')])
            before = await watermark(conn)
            field, value = ('potential_score', 75) if kind == 'quadrant_score' else ('title', 'Changed probe risk')
            await conn.execute(f'UPDATE insight.{kind} SET {field}=$2 WHERE id=$1', rid, value)
            await expect(conn, before, [(f, 'customer')])
            before = await watermark(conn)
            await conn.execute(f'DELETE FROM insight.{kind} WHERE id=$1', rid)
            await expect(conn, before, [(f, 'customer')])
    return {'score_and_risk_insert_update_delete': True, 'only_parent_customer_events': True,
            'all_probe_business_and_events_rolled_back': True}


async def grant_worker_fixture(conn, role):
    # Use the shipped worker allowlist, excluding functions absent before V196.
    # Existing privileges are frozen before migration; the new helper is never
    # granted through a broad EXECUTE-on-all-functions test fixture.
    rules = json.loads((ROOT.parent / 'tools/installation/runtime_permissions.json').read_text())['services']['worker']
    for schema in rules['schemas']:
        await conn.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
    for table, privileges in rules['tables'].items():
        await conn.execute(f'GRANT {",".join(privileges)} ON TABLE {table} TO "{role}"')
    for table, grants in rules['columns'].items():
        for privilege, columns in grants.items():
            await conn.execute(f'GRANT {privilege}({",".join(columns)}) ON TABLE {table} TO "{role}"')
    for signature in rules['functions']:
        if await conn.fetchval('SELECT to_regprocedure($1) IS NOT NULL', signature):
            await conn.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO "{role}"')


async def verify():
    host = os.environ.get('PGHOST', '')
    if not host.startswith('/'):
        raise ValueError('An explicit local Unix-socket PGHOST is required')
    config = {'host': host, 'port': int(os.environ.get('PGPORT', '5432')),
              'user': os.environ.get('PGUSER', 'postgres'), 'password': '', 'passfile': '/dev/null', 'ssl': False}
    admin = await asyncpg.connect(database='postgres', **config)
    suffix = uuid4().hex[:12]
    db, runtime = 'sb_insight_sync_' + suffix, 'sb_insight_app_' + suffix
    conn = None
    checks = []
    created_role = False
    try:
        await admin.execute(f'CREATE DATABASE "{db}" TEMPLATE template0 ENCODING \'UTF8\'')
        conn = await asyncpg.connect(database=db, **config)
        await migrate_through(conn, 195)
        assert await apply(conn, item('V197', MEMBER_MIGRATION), ROOT) == 'applied'
        own, foreign, absent = [await insight_fixture(conn, label, connected=connected)
                                for label, connected in [('A', True), ('B', True), ('C', False)]]
        await context(conn, own['ws'], own['user'], 'manager')
        for table, create in (('quadrant_score', score), ('risk', risk)):
            async with rollback(conn):
                before = await watermark(conn)
                rid = await create(conn, own)
                captured = await events(conn, before)
                assert len(captured) == 1 and captured[0]['object_kind'] == 'refresh'
                assert captured[0]['object_id'] == own['ws']
                before = await watermark(conn)
                field, value = ('potential_score', 75) if table == 'quadrant_score' else ('title', 'Changed risk')
                await conn.execute(f'UPDATE insight.{table} SET {field}=$2 WHERE id=$1', rid, value)
                assert [e['object_kind'] for e in await events(conn, before)] == ['refresh']
        checks.append('old_v195_v197_score_and_risk_insert_update_each_reproduce_company_refresh')

        await conn.execute(f'CREATE ROLE "{runtime}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        created_role = True
        await grant_worker_fixture(conn, runtime)
        # Reproduce deployment-owner default ACLs; V198 must neutralize grants
        # on its own function without modifying these defaults or old objects.
        await conn.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA ops GRANT EXECUTE ON FUNCTIONS TO "{runtime}",salegent_feishu_worker')
        before_acl = {r: await effective_acl(conn, r, SCHEMAS) for r in (runtime, 'salegent_feishu_worker')}
        before = await snapshot(conn)
        migration = item('V198', MIGRATION)
        assert await apply(conn, migration, ROOT) == 'applied'
        assert await snapshot(conn) == before
        assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops.schema_migration WHERE version='V196')")
        assert await conn.fetchval("SELECT checksum_sha256 FROM ops.migration_checksum WHERE migration_key='V198'") == migration.checksum
        assert await apply(conn, migration, ROOT) == 'unchanged'
        for role, acl in before_acl.items():
            assert await effective_acl(conn, role, SCHEMAS) == acl
            assert not await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, SIGNATURE)
        f = await conn.fetchrow('SELECT prosecdef,proconfig FROM pg_proc WHERE oid=$1::regprocedure', SIGNATURE)
        assert f['prosecdef'] and f['proconfig'] == ['search_path=pg_catalog']
        assert not await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a
            WHERE p.oid=$1::regprocedure AND a.grantee<>p.proowner)''', SIGNATURE)
        checks.append('isolated_v198_on_v195_v197_keeps_v196_absent_exact_checksum_business_queue_rls_acls_and_other_triggers')
        checks.append('private_definer_strips_public_and_default_grants_without_changing_defaults')

        await verify_basic_capture(conn)
        checks.append('score_and_risk_insert_update_delete_only_enqueue_parent_and_probe_fully_rolls_back')

        for table, create in (('quadrant_score', score), ('risk', risk)):
            async with rollback(conn):
                rid = await create(conn, own)
                before = await watermark(conn)
                field = 'potential_score' if table == 'quadrant_score' else 'title'
                await conn.execute(f'UPDATE insight.{table} SET {field}={field} WHERE id=$1', rid)
                await expect(conn, before, [])
                await conn.execute(f'UPDATE insight.{table} SET customer_id=$2 WHERE id=$1', rid, own['other'])
                await expect(conn, before, [(own, 'customer'), (own, 'other')])
                before = await watermark(conn)
                await conn.execute(f'UPDATE insight.{table} SET workspace_id=$2,customer_id=$3 WHERE id=$1',
                                   rid, foreign['ws'], foreign['customer'])
                await expect(conn, before, [(own, 'other'), (foreign, 'customer')])
                assert await conn.fetchval("SELECT current_setting('app.workspace_id')") == str(own['ws'])
        checks.append('noop_update_skipped_old_new_parent_dedup_and_controlled_tenant_move_keep_ambient_context')

        async with rollback(conn):
            before = await watermark(conn)
            for create in (score, risk):
                await create(conn, own)
            await expect(conn, before, [(own, 'customer'), (own, 'customer')])
            await conn.execute("SELECT set_config('app.feishu_historical_import','on',true)")
            before = await watermark(conn)
            await score(conn, own, 'other')
            await risk(conn, own, 'other')
            await expect(conn, before, [(own, 'other'), (own, 'other')], 'reconcile')
        checks.append('business_and_explicit_historical_origins_preserved_no_first_create_or_group_notice')

        async with rollback(conn):
            before = await watermark(conn)
            rid = await conn.fetchval('''INSERT INTO insight.risk(workspace_id,customer_id,risk_type_code,
                title,severity_code,owner_user_ref_id,import_meta) VALUES($1,$2,'synthetic','Imported risk','high',$3,
                '{"import_type":"crm_history"}') RETURNING id''', own['ws'], own['customer'], own['user'])
            await expect(conn, before, [(own, 'customer')], 'reconcile')
            before = await watermark(conn)
            await conn.execute("UPDATE insight.risk SET title='Current risk edit' WHERE id=$1", rid)
            await expect(conn, before, [(own, 'customer')], 'business')
        checks.append('risk_crm_history_insert_without_context_stays_reconcile_subsequent_ordinary_update_stays_business')

        async with rollback(conn):
            first = await score(conn, own)
            before = await watermark(conn)
            await conn.execute("UPDATE insight.quadrant_score SET valid_to=clock_timestamp() WHERE id=$1", first)
            second = await score(conn, own, value=85)
            await expect(conn, before, [(own, 'customer'), (own, 'customer')])
            assert (await source(conn, own))['potential_score'] == 85
            before = await watermark(conn)
            await conn.execute('DELETE FROM insight.quadrant_score WHERE id=$1', second)
            await expect(conn, before, [(own, 'customer')])
            raw = await source(conn, own)
            projected = project('customer', raw, ['potential_score', 'relationship_score', 'quadrant_code'])
            for key in ('potential_score', 'relationship_score', 'quadrant_code'):
                assert projected[key] is None and encode_scalar(projected[key], 2 if key.endswith('score') else 1) is None
        checks.append('score_replacement_keeps_both_events_pending_and_last_active_delete_explicitly_clears_remote_fields')

        async with rollback(conn):
            original = {kind: await source(conn, own, kind, kind) for kind in ('opportunity', 'visit')}
            high = await risk(conn, own, title='High risk', linked=True)
            critical = await risk(conn, own, title='Critical risk', severity='critical')
            assert (await source(conn, own))['risk_title'] == 'Critical risk'
            before = await watermark(conn)
            await conn.execute("UPDATE insight.risk SET status='resolved' WHERE id=$1", critical)
            await expect(conn, before, [(own, 'customer')])
            assert (await source(conn, own))['risk_title'] == 'High risk'
            before = await watermark(conn)
            await conn.execute('UPDATE insight.risk SET deleted_at=clock_timestamp() WHERE id=$1', high)
            await expect(conn, before, [(own, 'customer')])
            assert (await source(conn, own))['risk_title'] is None
            assert {kind: await source(conn, own, kind, kind) for kind in original} == original
        checks.append('risk_severity_order_resolution_soft_delete_refresh_only_customer_opportunity_and_visit_projection_unchanged')

        async with rollback(conn):
            before = await watermark(conn)
            rid = await risk(conn, own, None)
            await conn.execute("UPDATE insight.risk SET title='Still no explicit customer' WHERE id=$1", rid)
            await conn.execute('DELETE FROM insight.risk WHERE id=$1', rid)
            await expect(conn, before, [])
            before = await watermark(conn)
            for create in (score, risk):
                await create(conn, absent)
            await expect(conn, before, [])
        checks.append('risk_without_explicit_customer_does_not_infer_parent_no_connection_produces_no_events')

        async with rollback(conn):
            rid = await risk(conn, own, linked=True)
            before = await watermark(conn)
            await conn.execute('UPDATE insight.risk SET customer_id=NULL WHERE id=$1', rid)
            await expect(conn, before, [(own, 'customer')])
            before = await watermark(conn)
            await conn.execute('UPDATE insight.risk SET customer_id=$2 WHERE id=$1', rid, own['other'])
            await expect(conn, before, [(own, 'other')])
        checks.append('risk_parent_to_null_and_back_refresh_only_existing_explicit_parents')

        async with rollback(conn):
            # A privileged legacy row may contain a mismatched tenant or future
            # cascade may remove its parent first. Neither gets a cross-tenant event.
            for table, create in (('quadrant_score', score), ('risk', risk)):
                rid = await create(conn, own)
                before = await watermark(conn)
                await conn.execute(f'UPDATE insight.{table} SET customer_id=$2 WHERE id=$1', rid, foreign['customer'])
                await expect(conn, before, [(own, 'customer')])
                before = await watermark(conn)
                await conn.execute(f'DELETE FROM insight.{table} WHERE id=$1', rid)
                await expect(conn, before, [])
        checks.append('privileged_mismatched_parent_never_generates_cross_tenant_event')

        async with rollback(conn):
            # Emulate a child row visited after a future parent cascade without
            # changing any real FK or allowing a production orphan.
            for table, create in (('quadrant_score', score), ('risk', risk)):
                constraints = await conn.fetch('''SELECT conname FROM pg_constraint
                    WHERE conrelid=$1::regclass AND confrelid='crm.customer'::regclass''', 'insight.' + table)
                for row in constraints:
                    await conn.execute(f'ALTER TABLE insight.{table} DROP CONSTRAINT "{row[0]}"')
                missing = {**own, 'customer': uuid4()}
                before = await watermark(conn)
                rid = await create(conn, missing)
                await conn.execute(f'DELETE FROM insight.{table} WHERE id=$1', rid)
                await expect(conn, before, [])
        checks.append('missing_parent_never_enqueues_unresolvable_event')

        async with rollback(conn):
            await conn.execute('UPDATE config.feishu_connection SET enabled=false WHERE id=$1', own['sync'])
            before = await watermark(conn)
            await score(conn, own)
            await risk(conn, own)
            await expect(conn, before, [(own, 'customer'), (own, 'customer')])
            await conn.execute("UPDATE platform.workspace SET status='suspended' WHERE id=$1", own['ws'])
            before = await watermark(conn)
            await score(conn, own, 'other')
            await risk(conn, own, 'other')
            await expect(conn, before, [(own, 'other'), (own, 'other')])
            await denied(conn, "SELECT ops.feishu_source($1,'customer',$2)", own['sync'], own['customer'])
        checks.append('disabled_unmapped_connection_and_suspended_workspace_keep_existing_capture_and_worker_gate')

        async with rollback(conn):
            await conn.execute("UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code='feishu'", own['ws'])
            before = await watermark(conn)
            await score(conn, own)
            await risk(conn, own)
            await expect(conn, before, [(own, 'customer'), (own, 'customer')])
            await denied(conn, "SELECT ops.feishu_source($1,'customer',$2)", own['sync'], own['customer'])
        checks.append('disabled_module_preserves_worker_gate')

        async with rollback(conn):
            before = await watermark(conn)
            for key in ('demo', 'deleted'):
                await score(conn, own, key)
                await risk(conn, own, key)
            await expect(conn, before, [(own, 'demo'), (own, 'demo'), (own, 'deleted'), (own, 'deleted')])
            assert (await source(conn, own, key='demo'))['excluded']
            assert (await source(conn, own, key='deleted'))['deleted_at']
        checks.append('demo_deleted_parents_keep_existing_exclusion_and_tombstone_projection')

        async with rollback(conn):
            await context(conn, own['ws'], own['user'], 'manager')
            before = await watermark(conn)
            await conn.execute('SET LOCAL row_security=on')
            await conn.execute(f'SET LOCAL ROLE "{runtime}"')
            assert not await conn.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
            assert await conn.fetchval('SELECT security.authorization_risk_generation($1,$2)', own['customer'], own['user'])
            await score(conn, own)
            await risk(conn, own)
            for table in ('quadrant_score', 'risk'):
                assert not await conn.fetchval(f'SELECT count(*) FROM insight.{table} WHERE workspace_id=$1', foreign['ws'])
            await denied(conn, 'SELECT ops.capture_feishu_customer_insight_event()')
            await denied(conn, "INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id) VALUES($1,$2,'refresh',$2)", own['sync'], own['ws'])
            await denied(conn, '''INSERT INTO insight.quadrant_score(workspace_id,customer_id,potential_score,
                relationship_score,quadrant_code,rule_set_id,input_snapshot,subject_user_ref_id)
                VALUES($1,$2,50,50,'main_attack',$3,'{}',$4)''', foreign['ws'], foreign['customer'], foreign['rule'], own['user'])
            await denied(conn, '''INSERT INTO insight.risk(workspace_id,customer_id,risk_type_code,title,severity_code,owner_user_ref_id)
                VALUES($1,$2,'synthetic','Cross tenant blocked','high',$3)''', foreign['ws'], foreign['customer'], own['user'])
            await conn.execute('RESET ROLE')
            await expect(conn, before, [(own, 'customer'), (own, 'customer')])
        checks.append('limited_worker_identity_can_write_own_insights_without_direct_helper_queue_or_cross_tenant_access')

        # Converge the independently applied V198/V197 patch with the regular
        # chain, which applies V196 later and leaves both exact patches intact.
        definition = await conn.fetchval('SELECT pg_get_functiondef($1::regprocedure)', SIGNATURE)
        before = await watermark(conn)
        await migrate(conn)
        assert await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops.schema_migration WHERE version='V196')")
        assert await conn.fetchval('SELECT pg_get_functiondef($1::regprocedure)', SIGNATURE) == definition
        await expect(conn, before, [])
        await verify_basic_capture(conn)
        checks.append('isolated_patch_later_normal_chain_fills_v196_without_replaying_or_changing_v198')
        before = await snapshot(conn)
        assert all(step['status'] == 'unchanged' for step in await migrate(conn))
        assert await snapshot(conn) == before
        checks.append('normal_replay_unchanged_no_backfill_or_business_queue_side_effects')

        # A second fresh database checks the ordinary V195->V196->V197->V198 path.
        normal_db = db + '_normal'
        await admin.execute(f'CREATE DATABASE "{normal_db}" TEMPLATE template0 ENCODING \'UTF8\'')
        normal = None
        try:
            normal = await asyncpg.connect(database=normal_db, **config)
            await migrate_through(normal, 197)
            base = await snapshot(normal)
            await migrate(normal)
            assert await snapshot(normal) == base
            await verify_basic_capture(normal)
        finally:
            if normal:
                await normal.close()
            await admin.execute(f'DROP DATABASE IF EXISTS "{normal_db}" WITH (FORCE)')
        checks.append('fresh_normal_upgrade_v197_to_v198_preserves_existing_state_and_passes_targeted_probe')
        print(json.dumps({'passed': len(checks), 'checks': checks}, ensure_ascii=False))
    finally:
        if conn:
            await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        if created_role:
            await admin.execute(f'DROP ROLE IF EXISTS "{runtime}"')
        await admin.close()


if __name__ == '__main__':
    asyncio.run(verify())
