"""V195/V196 -> V197 targeted capture, using synthetic disposable local PG data.

Requires an explicit local Unix-socket PGHOST. Never reads DATABASE_URL,
credentials, backups, or remote systems. Only the randomized test database and
NOLOGIN/NOBYPASSRLS role are created and removed.
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import asyncpg

from verify_fde_postgres import context
from verify_phone_first_login_postgres import effective_acl
from verify_runtime_grants_postgres import migrate_through
# These fixture helpers add database/scripts to the standalone script path.
from migrate import apply, item, transactional_sql


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / 'migrations/V197__feishu_customer_member_targeted_sync.sql'
SIGNATURE = 'ops.capture_feishu_customer_member_event()'


@asynccontextmanager
async def rollback(conn):
    transaction = conn.transaction()
    await transaction.start()
    try:
        yield
    finally:
        await transaction.rollback()


async def denied(conn, sql, *args, code='42501'):
    try:
        async with conn.transaction():
            await conn.execute(sql, *args)
    except asyncpg.PostgresError as error:
        assert error.sqlstate == code, (error.sqlstate, str(error))
        return
    raise AssertionError('Expected rejection')


async def fixture(conn, label, *, connected=True):
    ids = {key: uuid4() for key in ('ws', 'user', 'second', 'customer', 'other', 'demo', 'deleted', 'sync')}
    ws = ids['ws']
    await conn.execute('''INSERT INTO platform.workspace(id,external_workspace_id,name)
        VALUES($1,$2,'Synthetic targeted sync company')''', ws, str(ws))
    await conn.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1 AND module_code IN ('feishu','history')", ws)
    for key in ('user', 'second'):
        await conn.execute('''INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name)
            VALUES($1,$2,$3,$4,$4)''', ids[key], ws, str(ids[key]), label + '_' + key)
        await conn.execute('''INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code)
            VALUES($1,$2,'manager','workspace')''', ws, ids[key])
    await context(conn, ws, ids['user'], 'manager')
    for key in ('customer', 'other', 'demo', 'deleted'):
        await conn.execute('''INSERT INTO crm.customer(id,workspace_id,name,normalized_name,
            created_by_user_ref_id,data_kind,deleted_at)
            VALUES($1,$2,$3,$3,$4,$5,CASE WHEN $6 THEN clock_timestamp() END)''',
            ids[key], ws, label + '_' + key, ids['user'], 'demo' if key == 'demo' else 'production', key == 'deleted')
    if connected:
        await conn.execute('''INSERT INTO config.feishu_connection(id,workspace_id,revision,enabled,
            validated_revision,settings,updated_by) VALUES($1,$2,1,true,1,$3,$4)''',
            ids['sync'], ws, json.dumps({'workspace_id': str(ws), 'connection_id': str(ids['sync']),
                'notification': {'enabled': True, 'on_create': ['customer']}}), ids['user'])
    return ids


async def add(conn, f, customer='customer', user='user'):
    await conn.execute('''INSERT INTO crm.customer_sales_member(workspace_id,customer_id,user_ref_id)
        VALUES($1,$2,$3) ON CONFLICT DO NOTHING''', f['ws'], f[customer], f[user])


async def watermark(conn):
    return await conn.fetchval('SELECT COALESCE(max(sequence_no),0) FROM ops.feishu_event')


async def events(conn, after):
    return [dict(row) for row in await conn.fetch('''SELECT connection_id,workspace_id,object_kind,object_id,
        first_formal_create,historical,notification_planned,queue_origin
        FROM ops.feishu_event WHERE sequence_no>$1 ORDER BY sequence_no''', after)]


async def expect(conn, after, parents, origin='business'):
    actual = await events(conn, after)
    expected = [{'connection_id': f['sync'], 'workspace_id': f['ws'], 'object_kind': 'customer', 'object_id': f[key],
        'first_formal_create': False, 'historical': True, 'notification_planned': False, 'queue_origin': origin}
        for f, key in parents]
    assert sorted(actual, key=str) == sorted(expected, key=str), (actual, expected)


async def snapshot(conn):
    return {
        'business': await conn.fetchval('SELECT jsonb_agg(to_jsonb(c) ORDER BY id)::text FROM crm.customer c'),
        'members': await conn.fetchval('SELECT jsonb_agg(to_jsonb(m) ORDER BY customer_id,user_ref_id)::text FROM crm.customer_sales_member m'),
        'connections': await conn.fetchval('SELECT jsonb_agg(to_jsonb(c) ORDER BY id)::text FROM config.feishu_connection c'),
        'events': await conn.fetchval('SELECT jsonb_agg(to_jsonb(e) ORDER BY sequence_no)::text FROM ops.feishu_event e'),
        'functions': [tuple(row) for row in await conn.fetch('''SELECT oid,proowner,proacl::text,prosrc FROM pg_proc
            WHERE oid IN ('ops.capture_feishu_event()'::regprocedure,'ops.feishu_reconcile(uuid)'::regprocedure,
            'ops.feishu_source(uuid,text,uuid)'::regprocedure) ORDER BY oid''')],
        'other_triggers': [tuple(row) for row in await conn.fetch('''SELECT oid,pg_get_triggerdef(oid) FROM pg_trigger
            WHERE NOT tgisinternal AND NOT (tgrelid='crm.customer_sales_member'::regclass AND tgname='feishu_capture') ORDER BY oid''')],
    }


async def verify():
    host = os.environ.get('PGHOST', '')
    if not host.startswith('/'):
        raise ValueError('An explicit local Unix-socket PGHOST is required')
    config = {'host': host, 'port': int(os.environ.get('PGPORT', '5432')),
              'user': os.environ.get('PGUSER', 'postgres'), 'password': '', 'passfile': '/dev/null', 'ssl': False}
    admin = await asyncpg.connect(database='postgres', **config)
    suffix = uuid4().hex[:12]
    db, runtime = 'sb_targeted_sync_' + suffix, 'sb_targeted_app_' + suffix
    conn = None
    checks = []
    created_role = False
    try:
        await admin.execute(f'CREATE DATABASE "{db}" TEMPLATE template0 ENCODING \'UTF8\'')
        conn = await asyncpg.connect(database=db, **config)
        await migrate_through(conn, 195)
        own, foreign, unconnected = await fixture(conn, 'A'), await fixture(conn, 'B'), await fixture(conn, 'C', connected=False)
        await context(conn, own['ws'], own['user'], 'manager')
        async with rollback(conn):
            before = await watermark(conn)
            await add(conn, own)
            old = await events(conn, before)
            assert len(old) == 1 and old[0]['object_kind'] == 'refresh' and old[0]['object_id'] == own['ws']
        checks.append('v195_reproduces_single_membership_company_refresh')

        # V197 SQL itself depends only on V195. Roll back this compatibility
        # probe; the actual normal upgrade below still applies V196 first.
        async with rollback(conn):
            await conn.execute(transactional_sql(MIGRATION))
            before = await watermark(conn)
            await add(conn, own)
            await expect(conn, before, [(own, 'customer')])
        checks.append('v197_sql_runs_on_v195_without_phone_login_dependency')

        # An isolated V197 hotfix must record the exact migration checksum,
        # leave V196 unapplied, and later converge with the normal migration
        # runner. Exercise the complete sequence in a rollback-only scope.
        async with rollback(conn):
            before_hotfix = await snapshot(conn)
            migration = item('V197', MIGRATION)
            assert await apply(conn, migration, ROOT) == 'applied'
            assert await snapshot(conn) == before_hotfix
            assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops.schema_migration WHERE version='V196')")
            assert await conn.fetchval("SELECT checksum_sha256 FROM ops.migration_checksum WHERE migration_key='V197'") == migration.checksum
            assert await apply(conn, migration, ROOT) == 'unchanged'
            async with rollback(conn):
                before = await watermark(conn)
                await add(conn, own)
                await expect(conn, before, [(own, 'customer')])
            definition = await conn.fetchval('SELECT pg_get_functiondef($1::regprocedure)', SIGNATURE)
            await migrate_through(conn, 197)
            assert await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops.schema_migration WHERE version='V196')")
            assert await conn.fetchval('SELECT pg_get_functiondef($1::regprocedure)', SIGNATURE) == definition
            assert await apply(conn, migration, ROOT) == 'unchanged'
            async with rollback(conn):
                before = await watermark(conn)
                await add(conn, own)
                await expect(conn, before, [(own, 'customer')])
        checks.append('isolated_v197_on_v195_records_exact_checksum_replays_noop_and_later_v196_converges')

        await migrate_through(conn, 196)
        await conn.execute(f'CREATE ROLE "{runtime}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        created_role = True
        await conn.execute(f'GRANT USAGE ON SCHEMA crm,ops,security,common,platform TO "{runtime}"')
        await conn.execute(f'GRANT SELECT ON crm.customer,crm.customer_sales_member TO "{runtime}"')
        await conn.execute(f'GRANT EXECUTE ON FUNCTION security.restore_historical_customer_ownership(uuid,uuid,uuid) TO "{runtime}"')
        schemas = ['crm', 'ops', 'security', 'config', 'platform', 'common', 'activity', 'workflow']
        before_acl = {role: await effective_acl(conn, role, schemas) for role in (runtime, 'salegent_feishu_worker')}
        before_upgrade = await snapshot(conn)
        await migrate_through(conn, 197)
        assert await snapshot(conn) == before_upgrade
        for role, expected in before_acl.items():
            assert await effective_acl(conn, role, schemas) == expected
            assert not await conn.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', role, SIGNATURE)
        function = await conn.fetchrow('SELECT prosecdef,proconfig,proacl::text FROM pg_proc WHERE oid=$1::regprocedure', SIGNATURE)
        assert function['prosecdef'] and function['proconfig'] == ['search_path=pg_catalog']
        assert not await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM pg_proc p,
            aclexplode(p.proacl) a WHERE p.oid=$1::regprocedure AND a.grantee=0)''', SIGNATURE)
        checks.append('normal_upgrade_preserves_business_queue_config_other_triggers_functions_and_effective_acls')

        # The same function is invoked by the real attached table trigger.
        async with rollback(conn):
            before = await watermark(conn)
            await add(conn, own)
            await expect(conn, before, [(own, 'customer')])
            before = await watermark(conn)
            await add(conn, own)
            await expect(conn, before, [])
        checks.append('insert_targets_customer_once_repeated_on_conflict_is_noop_no_notice')

        async with rollback(conn):
            await conn.execute("SELECT set_config('app.feishu_historical_import','on',true)")
            before = await watermark(conn)
            await add(conn, own)
            await expect(conn, before, [(own, 'customer')], 'reconcile')
        checks.append('historical_import_keeps_reconcile_origin_and_no_notice')

        async with rollback(conn):
            team, batch, record = uuid4(), uuid4(), uuid4()
            await conn.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'RESTORE','Synthetic restore team')", team, own['ws'])
            await conn.execute("INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role) VALUES($1,$2,$3,'manager')", own['ws'], team, own['second'])
            await conn.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,'administrator','workspace')", own['ws'], own['user'])
            await context(conn, own['ws'], own['user'], 'administrator')
            await conn.execute("SELECT set_config('app.feishu_historical_import','on',true)")
            await conn.execute('''INSERT INTO ops.crm_import_batch(id,workspace_id,source_system,source_base_id,
                manifest_sha256,status,source_snapshot_at,created_by_user_ref_id)
                VALUES($1,$2,'feishu','synthetic-source',repeat('a',64),'approved',clock_timestamp(),$3)''', batch, own['ws'], own['user'])
            await conn.execute('''INSERT INTO ops.crm_import_record(id,workspace_id,batch_id,source_table_id,
                source_record_id,object_kind,source_sha256,raw_fields,normalized,target_id,expected_target_version,status)
                VALUES($1,$2,$3,'synthetic-table','synthetic-record','customer',repeat('b',64),'{}',
                '{"source_owner_names":["Synthetic manager"],"current_owner_account":"A_SECOND"}',$4,1,'approved')''',
                record, own['ws'], batch, own['customer'])
            before = await watermark(conn)
            await conn.execute(f'SET LOCAL ROLE "{runtime}"')
            restored = json.loads(await conn.fetchval('SELECT security.restore_historical_customer_ownership($1,$2,$3)', record, own['second'], team))
            assert restored['status'] == 'history_restored'
            await conn.execute('RESET ROLE')
            await expect(conn, before, [(own, 'customer'), (own, 'customer')], 'reconcile')
            assert await conn.fetchval('SELECT owner_user_ref_id FROM crm.customer WHERE id=$1', own['customer']) == own['second']
        checks.append('real_history_restore_as_limited_role_emits_only_two_parent_events_without_company_refresh')

        async with rollback(conn):
            await add(conn, own)
            before = await watermark(conn)
            await conn.execute('UPDATE crm.customer_sales_member SET joined_at=joined_at WHERE customer_id=$1', own['customer'])
            await expect(conn, before, [])
            await conn.execute('UPDATE crm.customer_sales_member SET user_ref_id=$2 WHERE customer_id=$1', own['customer'], own['second'])
            await expect(conn, before, [(own, 'customer')])
            before = await watermark(conn)
            await conn.execute('DELETE FROM crm.customer_sales_member WHERE customer_id=$1', own['customer'])
            await expect(conn, before, [(own, 'customer')])
        checks.append('noop_update_skips_changed_member_and_delete_each_target_one_parent')

        async with rollback(conn):
            await add(conn, own)
            before = await watermark(conn)
            await conn.execute('UPDATE crm.customer_sales_member SET customer_id=$2 WHERE customer_id=$1', own['customer'], own['other'])
            await expect(conn, before, [(own, 'customer'), (own, 'other')])
            before = await watermark(conn)
            await conn.execute('''UPDATE crm.customer_sales_member SET workspace_id=$2,customer_id=$3,user_ref_id=$4
                WHERE customer_id=$1''', own['other'], foreign['ws'], foreign['customer'], foreign['user'])
            await expect(conn, before, [(own, 'other'), (foreign, 'customer')])
            assert await conn.fetchval("SELECT current_setting('app.workspace_id')") == str(own['ws'])
        checks.append('parent_move_and_controlled_cross_tenant_move_refresh_only_old_new_parent_and_preserve_context')

        async with rollback(conn):
            before = await watermark(conn)
            await add(conn, unconnected)
            await expect(conn, before, [])
            await denied(conn, '''INSERT INTO crm.customer_sales_member(workspace_id,customer_id,user_ref_id)
                VALUES($1,$2,$3)''', own['ws'], foreign['customer'], own['user'], code='23503')
            await expect(conn, before, [])
        checks.append('no_connection_skips_cross_workspace_parent_mismatch_is_fk_rejected')

        async with rollback(conn):
            await conn.execute('UPDATE config.feishu_connection SET enabled=false WHERE id=$1', own['sync'])
            before = await watermark(conn)
            await add(conn, own)
            await expect(conn, before, [(own, 'customer')])
            await conn.execute("UPDATE platform.workspace SET status='suspended' WHERE id=$1", own['ws'])
            before = await watermark(conn)
            await add(conn, own, 'other')
            await expect(conn, before, [(own, 'other')])
            assert not await conn.fetchval('SELECT security.workspace_is_active($1)', own['ws'])
            await denied(conn, "SELECT ops.feishu_source($1,'customer',$2)", own['sync'], own['customer'])
        checks.append('capture_preserves_disabled_connection_and_inactive_tenant_worker_gate_contract')

        async with rollback(conn):
            await conn.execute("UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code='feishu'", own['ws'])
            before = await watermark(conn)
            await add(conn, own)
            await expect(conn, before, [(own, 'customer')])
            await denied(conn, "SELECT ops.feishu_source($1,'customer',$2)", own['sync'], own['customer'])
        checks.append('disabled_feishu_module_still_rejects_source_projection')

        async with rollback(conn):
            before = await watermark(conn)
            await add(conn, own, 'demo')
            await add(conn, own, 'deleted')
            await expect(conn, before, [(own, 'demo'), (own, 'deleted')])
            for key in ('demo', 'deleted'):
                projection = json.loads(await conn.fetchval("SELECT ops.feishu_source($1,'customer',$2)", own['sync'], own[key]))
                assert projection['id'] == str(own[key])
                assert projection.get('excluded') if key == 'demo' else projection.get('deleted_at')
        checks.append('existing_demo_and_deleted_parent_preserve_normal_worker_tombstone_or_exclusion')

        async with rollback(conn):
            # Future cascading deletion can invoke a child trigger after its
            # parent has vanished. Isolate that state without changing real FKs.
            constraints = await conn.fetch('''SELECT conname FROM pg_constraint
                WHERE conrelid='crm.customer_sales_member'::regclass AND confrelid='crm.customer'::regclass''')
            for row in constraints:
                await conn.execute(f'ALTER TABLE crm.customer_sales_member DROP CONSTRAINT "{row[0]}"')
            before = await watermark(conn)
            missing = uuid4()
            await conn.execute('''INSERT INTO crm.customer_sales_member(workspace_id,customer_id,user_ref_id)
                VALUES($1,$2,$3)''', own['ws'], missing, own['user'])
            await conn.execute('DELETE FROM crm.customer_sales_member WHERE customer_id=$1', missing)
            await expect(conn, before, [])
        checks.append('missing_parent_insert_delete_does_not_create_unresolvable_event')

        before = await watermark(conn)
        async with rollback(conn):
            await conn.execute('''INSERT INTO crm.customer_sales_member(workspace_id,customer_id,user_ref_id)
                VALUES($1,$2,$3),($1,$4,$3),($1,$2,$5)''',
                own['ws'], own['customer'], own['user'], own['other'], own['second'])
            await expect(conn, before, [(own, 'customer'), (own, 'other'), (own, 'customer')])
        await expect(conn, before, [])
        checks.append('multirow_targets_only_affected_parents_all_enqueued_events_rollback_with_business')

        async with rollback(conn):
            await conn.execute(f'SET LOCAL ROLE "{runtime}"')
            await denied(conn, 'SELECT ops.capture_feishu_customer_member_event()')
            await denied(conn, 'INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id) VALUES($1,$2,\'refresh\',$2)', own['sync'], own['ws'])
            await denied(conn, 'INSERT INTO crm.customer_sales_member(workspace_id,customer_id,user_ref_id) VALUES($1,$2,$3)', own['ws'], own['customer'], own['user'])
        checks.append('unprivileged_caller_gains_neither_direct_trigger_execution_nor_queue_or_membership_writes')

        before = await snapshot(conn)
        # Keep this historical verifier pinned to its own migration boundary;
        # later independent triggers are covered by their own upgrade suites.
        await migrate_through(conn, 197)
        assert await snapshot(conn) == before
        checks.append('migration_replay_has_no_backfill_business_or_queue_side_effects')
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
