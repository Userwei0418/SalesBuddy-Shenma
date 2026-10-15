"""One-transaction migration and exact, commit-aware legacy role adoption."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from database_inventory import sql_name, verify_sequence_positions
from runtime_roles import SERVICES, identifier, load_policy, verify
from upgrade_permissions import cluster_identity, reconcile
from legacy_adoption_backup import inventory
from legacy_adoption_state import AdoptionError, checkpoint, fingerprint
from legacy_definer_dependencies import build_plan as definer_plan, apply_plan as apply_definer_plan


def migration_module(release):
    name = 'salesbuddy_legacy_adoption_migrate'
    spec = importlib.util.spec_from_file_location(name, Path(release)/'database/scripts/migrate.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def migration_ledger(release):
    module = migration_module(release)
    root = Path(release)/'database'
    files = [module.item('BASELINE_V1', root/'baseline/V001__current_schema.sql'),
             *(module.item(p.stem.split('__')[0], p) for p in sorted((root/'seeds').glob('S*.sql'))),
             *module.discover(root)]
    return {item.key: item.checksum for item in files}


async def identity(conn, spec):
    if (await conn.fetchval('SELECT current_database()') != spec['database']['name']
            or await conn.fetchval('SELECT current_user') != spec['database']['administrator']
            or not await conn.fetchval('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')
            or not 160000 <= int(await conn.fetchval('SHOW server_version_num')) < 170000):
        raise AdoptionError('Expected PostgreSQL 16 maintenance identity and database required')
    return {'database_oid': await conn.fetchval('SELECT oid FROM pg_database WHERE datname=current_database()'),
            'system_identifier': str(await conn.fetchval('SELECT system_identifier FROM pg_control_system()'))}


async def ledger(conn):
    return dict(await conn.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))


async def no_connections(conn):
    await conn.execute('SELECT pg_stat_clear_snapshot()')
    if await conn.fetchval('SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()'):
        raise AdoptionError('Stop all database clients before offline maintenance; none are terminated automatically')


async def drained(conn):
    # A retained running lease can represent an external side effect with an
    # unknown result. Never force it pending/succeeded to make deployment pass.
    if await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops.job WHERE status='running')"):
        raise AdoptionError('Background jobs are still running; drain or recover through the job workflow')
    if await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops.feishu_event WHERE status='running')"):
        raise AdoptionError('Feishu events are still running; resolve external results first')
    if await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops.feishu_delivery WHERE status='unknown')"):
        raise AdoptionError('Feishu delivery result is unknown; resolve before maintenance')


async def old_role_gate(conn, spec):
    """Only retire explicitly listed, exclusive runtime logins; preserve owners."""
    for role in spec['retire_roles']:
        row = await conn.fetchrow('SELECT oid,rolcanlogin,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication FROM pg_roles WHERE rolname=$1', role)
        if not row or not row['rolcanlogin'] or any(row[k] for k in ('rolsuper','rolbypassrls','rolcreatedb','rolcreaterole','rolreplication')):
            raise AdoptionError('Legacy login is missing or elevated; separate reviewed retirement required')
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM pg_auth_members WHERE roleid=$1)', row['oid']):
            raise AdoptionError('Another identity can inherit or assume a legacy login; separate review required')
        if await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM pg_shdepend
            WHERE refclassid='pg_authid'::regclass AND refobjid=$1
              AND dbid<>0 AND dbid<>(SELECT oid FROM pg_database WHERE datname=current_database()))''', row['oid']):
            raise AdoptionError('Legacy login has dependencies in another database')
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datdba=$1)', row['oid']):
            raise AdoptionError('Legacy login owns a database; retirement needs an ownership plan')
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE usesysid=$1)', row['oid']):
            raise AdoptionError('Legacy login still has a connection, possibly to another database')
        if await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM pg_db_role_setting
                WHERE setrole=$1 AND setdatabase<>0 AND setdatabase<>
                (SELECT oid FROM pg_database WHERE datname=current_database()))''', row['oid']):
            raise AdoptionError('Legacy login has configuration for another database')


async def state(conn, journal):
    spec = journal['spec']
    if await identity(conn, spec) != journal['database_identity']:
        raise AdoptionError('Database identity changed since preparation')
    actual = await ledger(conn)
    if actual == journal['before_ledger']:
        if fingerprint(await cluster_identity(conn)) != journal['before_cluster']:
            raise AdoptionError('Old migration ledger but cluster identities changed; no automatic retry')
        return 'old'
    if actual == journal['after_ledger'] and journal.get('after_cluster'):
        if fingerprint(await cluster_identity(conn)) != journal['after_cluster']:
            raise AdoptionError('Target migration ledger but cluster identities changed; no automatic retry')
        policy = load_policy(Path(spec['new_release'])/'tools/installation/runtime_permissions.json')
        dependencies = await definer_plan(conn, spec['roles'], policy)
        receipt = journal.get('legacy_definer_dependencies')
        if dependencies['grants'] or (receipt and
                fingerprint(dependencies) != receipt.get('verified_plan_sha256')):
            raise AdoptionError('Legacy definer dependencies differ from the committed receipt')
        for role in spec['retire_roles']:
            if await conn.fetchval('SELECT rolcanlogin FROM pg_roles WHERE rolname=$1', role):
                raise AdoptionError('Legacy runtime login was not retired')
        return 'new'
    raise AdoptionError('Database commit state is unknown; retain credentials, keep services stopped and inspect')


async def migrate(conn, directory, journal, backup_manifest, credentials, *, fault=None):
    spec = journal['spec']
    if await state(conn, journal) != 'old':
        raise AdoptionError('Database already changed; use resume to determine committed state')
    await no_connections(conn)
    await drained(conn)
    await old_role_gate(conn, spec)
    policy = load_policy(Path(spec['new_release'])/'tools/installation/runtime_permissions.json')
    module = migration_module(spec['new_release'])
    async with conn.transaction():
        await conn.execute("SET LOCAL lock_timeout='5s'")
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('salesbuddy-legacy-adoption:'||current_database(),0))")
        await no_connections(conn)
        if await state(conn, journal) != 'old':
            raise AdoptionError('Database changed while taking the migration lock')
        rows = await conn.fetch("""SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.relkind IN ('r','p') AND n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' ORDER BY 1,2""")
        for row in rows:
            await conn.execute('LOCK TABLE '+sql_name(row['nspname'])+'.'+sql_name(row['relname'])+' IN ACCESS EXCLUSIVE MODE')
        if await inventory(conn) != backup_manifest['inventory']:
            raise AdoptionError('Database changed after verified backup')
        original_cluster = await cluster_identity(conn)
        for service in sorted(SERVICES):
            role = identifier(spec['roles'][service])
            literal = await conn.fetchval('SELECT quote_literal($1::text)', credentials[service])
            await conn.execute(f'CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {literal}')
            await conn.execute(f'GRANT CONNECT ON DATABASE {identifier(spec["database"]["name"])} TO {role}')
            await conn.execute(f'ALTER ROLE {role} IN DATABASE {identifier(spec["database"]["name"])} SET search_path=pg_catalog,public')
            for parent in policy['services'][service]['memberships']:
                await conn.execute(f'GRANT {identifier(parent)} TO {role} WITH INHERIT FALSE, SET FALSE')
        checkpoint(directory, journal, 'roles_uncommitted', fault=fault)
        # Nested migrate() transactions are savepoints under this explicit outer
        # transaction. The entire ledger, role provisioning and grants commit once.
        results = await module.migrate(conn, Path(spec['new_release'])/'database')
        if [r['key'] for r in results if r['status'] != 'unchanged'] != journal['pending']:
            raise AdoptionError('Applied migration list differs from approved plan')
        checkpoint(directory, journal, 'migrations_uncommitted', fault=fault)
        await conn.execute(f'REVOKE CREATE ON DATABASE {identifier(spec["database"]["name"])} FROM PUBLIC')
        await conn.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        await reconcile(conn, spec['roles'], policy)
        # Existing SECURITY DEFINER functions retain their historical owners
        # across CREATE OR REPLACE. Their nested calls need separate authority;
        # granting EXECUTE to the new API role does not grant it to that owner.
        dependencies = await definer_plan(conn, spec['roles'], policy)
        journal['legacy_definer_dependencies'] = await apply_definer_plan(conn, dependencies, spec['roles'], policy)
        for role in spec['retire_roles']:
            await conn.execute(f'ALTER ROLE {identifier(role)} NOLOGIN')
        await verify(conn, spec['roles'], policy)
        await verify_sequence_positions(conn)
        if await ledger(conn) != journal['after_ledger']:
            raise AdoptionError('Target ledger differs after migration')
        after_cluster = await cluster_identity(conn)
        assert_cluster_changes(original_cluster, after_cluster, spec)
        journal['after_cluster'] = fingerprint(after_cluster)
        journal['after_inventory'] = fingerprint(await inventory(conn))
        checkpoint(directory, journal, 'before_database_commit', fault=fault)
    # A crash or lost commit acknowledgment here leaves the before-commit receipt
    # with the exact expected target fingerprints and durable original passwords.
    if fault:
        fault('commit_acknowledgment')
    checkpoint(directory, journal, 'database_committed', fault=fault)


async def verify_connections(spec, credentials):
    import asyncpg
    results = []
    for service in sorted(SERVICES):
        data = spec['database']
        # Separate authenticated connections validate the generated credentials.
        conn = await asyncpg.connect(host=spec['runtime_host'], port=data['port'], database=data['name'],
                                     user=spec['roles'][service], password=credentials[service], ssl=data['sslmode'])
        try:
            if await conn.fetchval('SELECT current_user') != spec['roles'][service]:
                raise AdoptionError('Runtime credential resolves to another identity')
            if service in ('api', 'worker') and await conn.fetchval('SELECT count(*) FROM crm.customer'):
                raise AdoptionError('Runtime sees customers without a tenant context')
            if service == 'platform':
                if await conn.fetchval("SELECT has_schema_privilege(current_user,'crm','USAGE')"):
                    raise AdoptionError('Platform identity has direct company business access')
            results.append(service)
        finally:
            await conn.close()
    return results


def assert_cluster_changes(before, after, spec):
    """Only new identities and reviewed legacy NOLOGIN may alter the cluster."""
    old_roles, old_memberships, old_settings = before
    new_roles, new_memberships, new_settings = after
    new_oids = {row['oid'] for row in new_roles if row['rolname'] in spec['roles'].values()}
    retained = [dict(row) for row in new_roles if row['oid'] not in new_oids]
    for row in retained:
        if row['rolname'] in spec['retire_roles']:
            row['rolcanlogin'] = True
    if (retained != old_roles
            or [row for row in new_memberships if row['member'] not in new_oids] != old_memberships
            or [row for row in new_settings if row['setrole'] not in new_oids] != old_settings):
        raise AdoptionError('Migration unexpectedly changed an existing cluster identity')
