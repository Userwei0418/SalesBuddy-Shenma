#!/usr/bin/env python3
"""Late V196 after V195 + V197..V200 in a disposable PostgreSQL database.

Uses PGHOST/PGUSER for a local Unix-socket maintenance connection, never a
business DATABASE_URL. All identities are synthetic; no external calls.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import asyncpg

from verify_runtime_grants_postgres import migrate_through
from verify_fde_postgres import context
from migrate import apply, item, migrate, transactional_sql

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / 'migrations/V196__phone_first_login.sql'
PUBLIC_API = (
    'security.password_login_identifier(text,text,text)',
    'security.password_candidate(text,text,text,text)',
    'security.lock_password_login_identifier(text,text)',
)
PRIVATE = (
    'security.guard_demo_account_reservation()',
    'security.reconcile_runtime_grants()',
    'security.reconcile_runtime_grants_before_phone_first_login()',
)
LEGACY = (
    'security.password_login_identifier(text,text)',
    'security.password_candidate(text,text,text)',
    'security.lock_password_login_identifier(text)',
)
RESERVED = ('XS001', 'XS002', 'XS003', 'ZJ001', 'ZJ002', 'CPXS001', 'CPXS002',
            'CPXS003', 'CPXS004', 'FDEL001', 'FDE001', 'FDE002', 'FDE003', 'ZJL001')


async def effective_acl(conn, role, schemas):
    """Read actual effective permissions, including inherited/default grants."""
    queries = {
        'functions': '''SELECT p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname=ANY($2::text[]) AND has_function_privilege($1,p.oid,'EXECUTE')''',
        'tables': '''SELECT c.oid::regclass::text||':'||priv FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) priv
            WHERE n.nspname=ANY($2::text[]) AND c.relkind IN ('r','p','v','m','f')
              AND has_table_privilege($1,c.oid,priv)''',
        'columns': '''SELECT c.oid::regclass::text||':'||a.attname||':'||priv
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','REFERENCES']) priv
            WHERE n.nspname=ANY($2::text[]) AND c.relkind IN ('r','p','v','m','f')
              AND a.attnum>0 AND NOT a.attisdropped AND has_column_privilege($1,c.oid,a.attnum,priv)''',
        'sequences': '''SELECT c.oid::regclass::text||':'||priv FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN unnest(ARRAY['SELECT','UPDATE','USAGE']) priv
            WHERE n.nspname=ANY($2::text[]) AND c.relkind='S' AND has_sequence_privilege($1,c.oid,priv)''',
        'schemas': '''SELECT nspname||':'||priv FROM pg_namespace CROSS JOIN unnest(ARRAY['USAGE','CREATE']) priv
            WHERE nspname=ANY($2::text[]) AND has_schema_privilege($1,oid,priv)''',
    }
    return {kind: {row[0] for row in await conn.fetch(query, role, schemas)} for kind, query in queries.items()}


async def grant_previous_contract(conn, service, role, rules):
    for parent in rules['memberships']:
        await conn.execute(f'GRANT "{parent}" TO "{role}" WITH INHERIT FALSE, SET FALSE')
    for schema in rules['schemas']:
        await conn.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
    for kind, sql_kind in (('tables', 'TABLE'), ('sequences', 'SEQUENCE')):
        for name, privileges in rules[kind].items():
            await conn.execute(f'GRANT {",".join(privileges)} ON {sql_kind} {name} TO "{role}"')
    for name, grants in rules['columns'].items():
        for privilege, columns in grants.items():
            await conn.execute(f'GRANT {privilege}({",".join(columns)}) ON {name} TO "{role}"')
    for signature in rules['functions']:
        if signature not in PUBLIC_API:
            await conn.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO "{role}"')


async def workspace(conn, code):
    return await conn.fetchval(
        "INSERT INTO platform.workspace(external_workspace_id,name) VALUES($1,'Synthetic company') RETURNING id", code)


async def member(conn, ws, account, phone=None, status='active', password=True):
    uid = await conn.fetchval('''INSERT INTO platform.user_ref(
        workspace_id,external_user_id,account_code,display_name,phone_number,status)
        VALUES($1,$2,$3,'Synthetic member',$4,$5) RETURNING id''', ws, str(uuid4()), account, phone, status)
    await conn.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,'operations','workspace')", ws, uid)
    if password:
        await conn.execute("INSERT INTO platform.password_credential(user_ref_id,workspace_id,password_hash) VALUES($1,$2,'synthetic-hash')", uid, ws)
    return uid


async def denied(conn, query, *args, code='23505', constraint=None):
    try:
        async with conn.transaction():
            await conn.execute(query, *args)
    except asyncpg.PostgresError as error:
        assert error.sqlstate == code, (error.sqlstate, str(error))
        if constraint:
            assert error.constraint_name == constraint, error.constraint_name
        return error
    raise AssertionError('Expected rejection')


async def lookup(conn, identifier, kind='auto', company=None):
    rows = await conn.fetch('SELECT * FROM security.password_login_identifier($1,$2,$3)', company, identifier, kind)
    return [dict(row) for row in rows]


async def verify():
    host = os.environ.get('PGHOST', '/tmp')
    if not host.startswith('/'):
        raise ValueError('Only an explicit local Unix-socket cluster is accepted')
    config = dict(host=host, user=os.environ.get('PGUSER', 'postgres'), port=int(os.environ.get('PGPORT', '5432')))
    admin = await asyncpg.connect(database='postgres', **config)
    suffix = uuid4().hex[:12]
    db = 'salegent_verify_phone_' + suffix
    api, observer, late = ['sb_phone_' + kind + '_' + suffix for kind in ('api', 'observer', 'late')]
    connections, roles, checks = [], [], []
    try:
        await admin.execute(f'CREATE DATABASE "{db}" TEMPLATE template0')
        owner = await asyncpg.connect(database=db, **config)
        connections.append(owner)
        await migrate_through(owner, 195)
        # Production has these later migrations, but never applied V196. Do
        # not replay or omit them when proving the late phone-login upgrade.
        for version in (197, 198, 199, 200):
            path = next((ROOT / 'migrations').glob(f'V{version}__*.sql'))
            await apply(owner, item(f'V{version}', path), ROOT)
        original_ledger = await owner.fetch('SELECT * FROM ops.migration_checksum ORDER BY migration_key')
        assert not any(row['migration_key'] == 'V196' for row in original_ledger)
        isolation = await owner.fetch("""SELECT schemaname,tablename,policyname,roles,cmd,qual,with_check
            FROM pg_policies ORDER BY schemaname,tablename,policyname""")
        triggers = await owner.fetch("""SELECT oid,pg_get_triggerdef(oid) AS definition
            FROM pg_trigger WHERE NOT tgisinternal ORDER BY oid""")
        checks.append('production_v200_baseline_has_v197_to_v200_and_excludes_v196')
        a, b, demo = [await workspace(owner, name) for name in ('phone-qa-a', 'phone-qa-b', 'demo-sales-workspace')]
        first = await member(owner, a, 'FIRST', '13900001001')
        second = await member(owner, b, 'SECOND', '13900001001', status='inactive')
        old_reserved = await member(owner, b, 'XS001')
        preflight_sql = (ROOT / 'scripts/preflight_v196_phone_login.sql').read_text().replace('BEGIN READ ONLY;', '').replace('COMMIT;', '')
        report = json.loads(await owner.fetchval(preflight_sql))
        assert report == {'duplicate_phone_groups': 1, 'duplicate_phone_bindings': 2,
                          'reserved_account_rows': 1, 'legacy_auto_alias_ambiguities': 0, 'can_migrate': False}
        before = await owner.fetch('SELECT id,workspace_id,account_code,phone_number,status FROM platform.user_ref ORDER BY id')
        try:
            await migrate(owner)
        except asyncpg.CheckViolationError as error:
            assert error.message == 'PHONE_LOGIN_PREFLIGHT_FAILED'
            assert error.detail == 'duplicate_phone_groups=1; reserved_account_rows=1'
            assert '13900001001' not in str(error) and str(first) not in str(error)
        else:
            raise AssertionError('Conflicting history was silently migrated')
        assert before == await owner.fetch('SELECT id,workspace_id,account_code,phone_number,status FROM platform.user_ref ORDER BY id')
        assert await owner.fetchval("SELECT to_regclass('platform.user_ref_global_phone_unique')") is None
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM ops.migration_checksum WHERE migration_key='V196')")
        checks.append('preflight_blocks_with_counts_only_and_rolls_back_without_identity_changes')

        # Simulate explicit administrator reconciliation; the migration does not
        # perform this operation on an existing installation.
        await owner.execute('UPDATE platform.user_ref SET phone_number=NULL WHERE id=$1', second)
        await owner.execute("UPDATE platform.user_ref SET account_code='FORMER_RESERVED' WHERE id=$1", old_reserved)
        await member(owner, a, 'OPSADMIN', password=False)
        await member(owner, b, 'OPSADMIN', password=False)
        policy = json.loads((ROOT.parent / 'tools/installation/runtime_permissions.json').read_text())
        assert policy['schema'] == 'V200', 'Update this versioned probe when the canonical schema advances'
        schemas = sorted(set().union(*(set(rules['schemas']) for rules in policy['services'].values())))
        # Reproduce the reviewed V200 service contract (V196 adds only our three
        # functions). In particular, no old wide reconciler grants are present.
        for schema in schemas:
            for kind in ('TABLES', 'SEQUENCES', 'FUNCTIONS'):
                await owner.execute(f'REVOKE ALL ON ALL {kind} IN SCHEMA {schema} FROM PUBLIC')
            await owner.execute(f'REVOKE ALL ON SCHEMA {schema} FROM PUBLIC')
        service_roles = {service: 'sb_phone_contract_' + service + '_' + suffix for service in policy['services']}
        for service, role in service_roles.items():
            await owner.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
            roles.append(role)
            await grant_previous_contract(owner, service, role, policy['services'][service])
        previous_acl = {service: await effective_acl(owner, role, schemas) for service, role in service_roles.items()}
        for role in (api, observer, late):
            await owner.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
            roles.append(role)
            await owner.execute(f'GRANT USAGE ON SCHEMA security,common,platform TO "{role}"')
        await owner.execute(f'GRANT EXECUTE ON FUNCTION security.password_candidate(text,text,text),security.save_account_authorization(uuid,jsonb) TO "{api}"')
        await owner.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA security GRANT EXECUTE ON FUNCTIONS TO "{observer}"')
        legacy = await owner.fetch('SELECT oid,proowner,proacl,prosrc FROM pg_proc WHERE oid=ANY($1::regprocedure[]) ORDER BY oid', list(LEGACY))
        feishu_functions = await owner.fetch("""SELECT oid,proowner,proacl,prosrc,proconfig FROM pg_proc
            WHERE pronamespace='ops'::regnamespace ORDER BY oid""")
        # Exercise the actual migration body separately from the deployment
        # runner's deliberate whole-contract maintenance reconciliation.
        async with owner.transaction():
            await owner.execute(transactional_sql(MIGRATION))
        assert original_ledger == await owner.fetch('SELECT * FROM ops.migration_checksum ORDER BY migration_key')
        assert feishu_functions == await owner.fetch("""SELECT oid,proowner,proacl,prosrc,proconfig FROM pg_proc
            WHERE pronamespace='ops'::regnamespace ORDER BY oid""")
        assert isolation == await owner.fetch("""SELECT schemaname,tablename,policyname,roles,cmd,qual,with_check
            FROM pg_policies ORDER BY schemaname,tablename,policyname""")
        assert triggers == await owner.fetch("""SELECT oid,pg_get_triggerdef(oid) AS definition
            FROM pg_trigger WHERE NOT tgisinternal AND tgname<>'user_ref_demo_account_reserved' ORDER BY oid""")
        checks.append('late_v196_preserves_existing_ledger_feishu_functions_triggers_and_rls')
        for service, role in service_roles.items():
            current_acl = await effective_acl(owner, role, schemas)
            expected = {kind: set(items) for kind, items in previous_acl[service].items()}
            if service == 'api':
                expected['functions'].update(PUBLIC_API)
            assert current_acl == expected, ('Unexpected V196 privilege change', service,
                {kind: {'added': sorted(current_acl[kind]-expected[kind]), 'removed': sorted(expected[kind]-current_acl[kind])}
                 for kind in expected if current_acl[kind] != expected[kind]})
        checks.append('canonical_four_service_permissions_only_add_three_api_functions_no_historic_expansion')
        assert legacy == await owner.fetch('SELECT oid,proowner,proacl,prosrc FROM pg_proc WHERE oid=ANY($1::regprocedure[]) ORDER BY oid', list(LEGACY))
        for signature in PUBLIC_API:
            assert await owner.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", api, signature)
            for role in (observer, late, 'salegent_feishu_worker'):
                assert not await owner.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
        for signature in PRIVATE:
            for role in (api, observer, late, 'salegent_feishu_worker'):
                assert not await owner.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
        checks.append('new_api_acl_is_narrow_default_grants_removed_and_legacy_functions_unchanged')
        # Record the reviewed exact file so the runner can demonstrate repeat
        # deployment behavior without executing the already-tested migration.
        await migrate(owner, adopt_existing=True)
        await owner.execute(f'GRANT EXECUTE ON FUNCTION security.password_candidate(text,text,text),security.save_account_authorization(uuid,jsonb) TO "{late}"')
        await owner.fetchval('SELECT security.reconcile_runtime_grants()')
        for signature in PUBLIC_API:
            assert await owner.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", late, signature)
        assert all(item['status'] == 'unchanged' for item in await migrate(owner))
        checks.append('late_runtime_reconciliation_and_repeat_migration')

        client, contender = [await asyncpg.connect(database=db, **config) for _ in range(2)]
        connections.extend((client, contender))
        await client.execute(f'SET ROLE "{api}"')
        await denied(client, 'SELECT * FROM platform.password_credential', code='42501')
        await denied(client, 'SELECT * FROM platform.user_ref', code='42501')
        assert await lookup(client, ' 13900001001 ', 'phone') == [{'workspace': 'phone-qa-a', 'account_code': 'FIRST'}]
        assert await lookup(client, '13900001001', 'phone', ' PHONE-QA-A ') == [{'workspace': 'phone-qa-a', 'account_code': 'FIRST'}]
        assert await lookup(client, '13900001001', 'phone', 'phone-qa-b') == []
        assert await lookup(client, 'first', 'account') == [{'workspace': 'phone-qa-a', 'account_code': 'FIRST'}]
        for identifier, kind in [('FIRST', 'phone'), ('13900001001', 'account'), ('13900001001', 'unexpected'), ('13900001001', None), ('', 'auto'), (None, 'auto')]:
            assert await lookup(client, identifier, kind) == []
        candidate = json.loads(await client.fetchval("SELECT security.password_candidate(NULL,'13900001001','operations','phone')"))
        assert candidate['user_id'] == str(first) and candidate['workspace_id'] == str(a)
        assert candidate['password_hash'] == 'synthetic-hash'
        assert await client.fetchval("SELECT security.password_candidate('phone-qa-b','13900001001',NULL,'phone')") is None
        checks.append('typed_lookup_active_only_exact_company_and_no_direct_identity_table_access')

        duplicate_account = await member(owner, b, 'FIRST')
        numeric = await member(owner, b, '13900001001')
        assert await lookup(client, 'FIRST', 'account') == []
        assert await lookup(client, 'FIRST', 'account', 'phone-qa-b') == [{'workspace': 'phone-qa-b', 'account_code': 'FIRST'}]
        assert await lookup(client, '13900001001', 'phone') == [{'workspace': 'phone-qa-a', 'account_code': 'FIRST'}]
        assert await lookup(client, '13900001001', 'account') == [{'workspace': 'phone-qa-b', 'account_code': '13900001001'}]
        assert await lookup(client, '13900001001') == []
        assert await owner.fetch('SELECT * FROM security.password_login_identifier(NULL,$1)', '13900001001') == []
        assert await owner.fetch('SELECT * FROM security.password_login_identifier(NULL,$1)', 'FIRST') == []
        assert await owner.fetchval('SELECT security.password_account_workspace($1)', '13900001001') is None
        # Same row matched by account and phone is still one identity.
        await owner.execute("UPDATE platform.user_ref SET phone_number='13900001001' WHERE id=$1", first)
        single = await member(owner, a, '13900001002', '13900001002')
        assert len(await lookup(client, '13900001002')) == 1
        checks.append('duplicate_names_and_cross_company_numeric_aliases_require_unambiguous_login_type')

        other_candidate = await member(owner, demo, 'PHONE_CANDIDATE')
        await owner.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1", first)
        assert await lookup(client, '13900001001', 'phone') == []
        await denied(owner, 'UPDATE platform.user_ref SET phone_number=$1 WHERE id=$2', '13900001001', other_candidate, constraint='user_ref_global_phone_unique')
        await owner.execute("UPDATE platform.workspace SET status='suspended' WHERE id=$1", a)
        await owner.execute("UPDATE platform.user_ref SET status='active' WHERE id=$1", first)
        assert await lookup(client, '13900001001', 'phone') == []
        await denied(owner, 'UPDATE platform.user_ref SET phone_number=$1 WHERE id=$2', '13900001001', other_candidate, constraint='user_ref_global_phone_unique')
        await owner.execute("UPDATE platform.workspace SET status='active' WHERE id=$1", a)
        await owner.execute('UPDATE platform.user_ref SET phone_number=NULL WHERE id=$1', first)
        # Account alias collision is still independently protected in company b.
        await denied(owner, 'UPDATE platform.user_ref SET phone_number=$1 WHERE id=$2', '13900001001', second)
        await owner.execute("UPDATE platform.user_ref SET account_code='NUMERIC_LEGACY' WHERE id=$1", numeric)
        await owner.execute('UPDATE platform.user_ref SET phone_number=$1 WHERE id=$2', '13900001001', second)
        await owner.execute("UPDATE platform.user_ref SET status='active' WHERE id=$1", second)
        assert await lookup(client, '13900001001', 'phone') == [{'workspace': 'phone-qa-b', 'account_code': 'SECOND'}]
        checks.append('inactive_and_suspended_bindings_remain_owned_explicit_unbinding_enables_reassignment')

        deleted = await member(owner, a, 'DELETED_BINDING', '13900001004')
        await owner.execute('UPDATE platform.user_ref SET deleted_at=clock_timestamp() WHERE id=$1', deleted)
        await owner.execute("UPDATE platform.user_ref SET phone_number='13900001004' WHERE id=$1", other_candidate)
        await denied(owner, 'UPDATE platform.user_ref SET deleted_at=NULL WHERE id=$1', deleted,
                     constraint='user_ref_global_phone_unique')
        checks.append('deleted_account_releases_binding_but_conflicting_restore_is_rejected')

        for code in RESERVED:
            await member(owner, demo, code)
            error = await denied(owner, '''INSERT INTO platform.user_ref(workspace_id,external_user_id,account_code,display_name)
                VALUES($1,$2,$3,'Synthetic reserved')''', a, str(uuid4()), code,
                code='23514', constraint='user_ref_demo_account_reserved')
            assert error.message == 'DEMO_ACCOUNT_RESERVED' and error.detail is None
        for code in ('XS004', 'XS001_EXTRA', 'FDE004', 'CPXS005'):
            await member(owner, a, code)
        await denied(owner, "UPDATE platform.user_ref SET account_code='XS001' WHERE id=$1", first,
                     code='23514', constraint='user_ref_demo_account_reserved')
        await denied(owner, "UPDATE platform.user_ref SET workspace_id=$1 WHERE account_code='XS001' AND workspace_id=$2", a, demo,
                     code='23514', constraint='user_ref_demo_account_reserved')
        checks.append('exact_fourteen_demo_names_reserved_without_prefix_or_opsadmin_restriction')

        # Two independent company locks do not serialize here; the global unique
        # index is the final arbiter while the first transaction is uncommitted.
        transaction = owner.transaction()
        await transaction.start()
        task = None
        try:
            await owner.execute("UPDATE platform.user_ref SET phone_number='13900001003' WHERE id=$1", first)
            pid = await contender.fetchval('SELECT pg_backend_pid()')
            task = asyncio.create_task(contender.execute("UPDATE platform.user_ref SET phone_number='13900001003' WHERE id=$1", duplicate_account))
            for _ in range(100):
                if await owner.fetchval('SELECT cardinality(pg_blocking_pids($1))>0', pid):
                    break
                await asyncio.sleep(.01)
            else:
                raise AssertionError('Global duplicate bind did not wait on first transaction')
            await transaction.commit()
            transaction = None
            try:
                await task
            except asyncpg.UniqueViolationError as error:
                assert error.constraint_name == 'user_ref_global_phone_unique'
            else:
                raise AssertionError('Concurrent duplicate phone binding accepted')
        finally:
            if transaction:
                await transaction.rollback()
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        checks.append('concurrent_cross_company_binding_has_exactly_one_winner')

        # The typed proof must remain current through session issuance. A removed
        # phone cannot silently become an account-code proof, even on the same row.
        await context(client, a, single, 'operations')
        assert await client.fetchval("SELECT security.lock_password_login_identifier('13900001002','phone')")
        await owner.execute('UPDATE platform.user_ref SET phone_number=NULL WHERE id=$1', single)
        assert not await client.fetchval("SELECT security.lock_password_login_identifier('13900001002','phone')")
        assert await client.fetchval("SELECT security.lock_password_login_identifier('13900001002','account')")
        await owner.execute("UPDATE platform.user_ref SET phone_number='13900001002' WHERE id=$1", single)
        transaction = client.transaction()
        await transaction.start()
        task = None
        try:
            assert await client.fetchval("SELECT security.lock_password_login_identifier('13900001002','phone')")
            pid = await contender.fetchval('SELECT pg_backend_pid()')
            task = asyncio.create_task(contender.execute('UPDATE platform.user_ref SET phone_number=NULL WHERE id=$1', single))
            for _ in range(100):
                if await owner.fetchval('SELECT cardinality(pg_blocking_pids($1))>0', pid):
                    break
                await asyncio.sleep(.01)
            else:
                raise AssertionError('Binding removal did not wait for login proof lock')
            await transaction.commit()
            transaction = None
            await task
        finally:
            if transaction:
                await transaction.rollback()
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert not await client.fetchval("SELECT security.lock_password_login_identifier('13900001002','phone')")
        checks.append('typed_proof_rejects_removed_alias_and_blocks_concurrent_removal_until_commit')
        assert all(item['status'] == 'unchanged' for item in await migrate(owner))
        print(json.dumps({'passed': len(checks), 'checks': checks, 'production_data': False,
                          'external_calls': False}, ensure_ascii=False, indent=2))
    finally:
        for conn in reversed(connections):
            await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        for role in reversed(roles):
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


if __name__ == '__main__':
    asyncio.run(verify())
