"""Explicit database permissions for a NEW, migrated product installation.

This module neither migrates nor adopts an existing installation. The caller owns
the maintenance connection and stores generated credentials outside the artifact.
No runtime role may reconcile its own grants. Verification inspects effective
privileges, including PUBLIC, inherited and column grants, not just explicit ACLs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import secrets


POLICY_PATH = Path(__file__).with_name('runtime_permissions.json')
SERVICES = frozenset({'api', 'worker', 'platform', 'feishu'})
SCHEMAS = ('activity', 'agent', 'common', 'config', 'control', 'crm',
           'insight', 'ops', 'platform', 'security', 'workflow')
TABLE_PRIVILEGES = ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER')
COLUMN_PRIVILEGES = ('SELECT', 'INSERT', 'UPDATE', 'REFERENCES')
SEQUENCE_PRIVILEGES = ('USAGE', 'SELECT', 'UPDATE')
IDENTIFIER = re.compile(r'[a-z][a-z0-9_]{0,62}\Z')
OBJECT = re.compile(r'[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\Z')
SIGNATURE = re.compile(r'[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\([a-z0-9_, \[\]]*\)\Z')


class RolePolicyError(ValueError):
    pass


def fingerprint(ledger):
    return hashlib.sha256(json.dumps(dict(ledger), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise RolePolicyError('Invalid installation database identity')
    return '"' + value + '"'


def load_policy(path=POLICY_PATH):
    policy = json.loads(Path(path).read_text())
    return validate_policy(policy)


def validate_policy(policy):
    if (policy.get('format') != 'salesbuddy-runtime-permissions-v1'
            or set(policy.get('services', {})) != SERVICES
            or any(not re.fullmatch(r'[0-9a-f]{64}', policy.get(k, ''))
                   for k in ('migration_fingerprint', 'rls_fingerprint'))):
        raise RolePolicyError('Unknown runtime permission policy')
    for service, rules in policy['services'].items():
        if set(rules) != {'schemas', 'tables', 'functions', 'sequences', 'columns', 'memberships', 'policies'}:
            raise RolePolicyError('Incomplete service policy')
        if not set(rules['schemas']) <= set(SCHEMAS):
            raise RolePolicyError('Unknown application schema')
        if rules['memberships'] != (['salegent_feishu_worker'] if service == 'feishu' else []):
            raise RolePolicyError('Unexpected runtime role inheritance')
        for kind, allowed in (('tables', TABLE_PRIVILEGES[:4]), ('sequences', SEQUENCE_PRIVILEGES)):
            for name, privileges in rules[kind].items():
                if (not OBJECT.fullmatch(name) or name.split('.')[0] not in rules['schemas']
                        or not privileges or not set(privileges) <= set(allowed)
                        or len(privileges) != len(set(privileges))):
                    raise RolePolicyError('Invalid explicit object permission')
        for signature in rules['functions']:
            if not SIGNATURE.fullmatch(signature) or signature.split('.')[0] not in rules['schemas']:
                raise RolePolicyError('Invalid explicit function permission')
        if len(rules['functions']) != len(set(rules['functions'])):
            raise RolePolicyError('Duplicate function permission')
        for name, grants in rules['columns'].items():
            if not OBJECT.fullmatch(name) or name.split('.')[0] not in rules['schemas']:
                raise RolePolicyError('Invalid column permission target')
            for privilege, columns in grants.items():
                if privilege not in COLUMN_PRIVILEGES or not columns or any(not IDENTIFIER.fullmatch(c) for c in columns):
                    raise RolePolicyError('Invalid explicit column permission')
        expected_policies = {name: 'sync_worker' for name in (
            'config.feishu_connection', 'ops.feishu_delivery', 'ops.feishu_event', 'ops.feishu_record_map')}
        if rules['policies'] != (expected_policies if service == 'feishu' else {}):
            raise RolePolicyError('Unexpected service row policy')
    return policy


async def verify_rls(connection, roles, policy):
    tables = [dict(r) for r in await connection.fetch('''SELECT n.nspname||'.'||c.relname AS name,
        c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p') ORDER BY 1''', SCHEMAS)]
    rows = [dict(r) for r in await connection.fetch('''SELECT schemaname,tablename,policyname,
        permissive,roles::text[],cmd,qual,with_check FROM pg_policies
        WHERE schemaname=ANY($1::text[]) ORDER BY 1,2,3''', SCHEMAS)]
    direct = {service: {} for service in roles}
    for row in rows:
        for service, role in roles.items():
            if role in row['roles']:
                direct[service][row['schemaname']+'.'+row['tablename']] = row['policyname']
        row['roles'] = sorted(r for r in row['roles'] if r not in roles.values())
    if any(direct[s] != policy['services'][s]['policies'] for s in roles):
        raise RolePolicyError('Installed service row policies differ from the contract')
    if fingerprint({'tables': tables, 'policies': rows}) != policy['rls_fingerprint']:
        raise RolePolicyError('Row isolation policies differ from the reviewed migration baseline')


async def verify(connection, roles, policy=None):
    """Read-only check of all four services on the actual installation database."""
    if set(roles) != SERVICES or len(set(roles.values())) != len(SERVICES):
        raise RolePolicyError('Four distinct runtime identities are required')
    return await verify_service_roles(connection, roles, policy)


async def verify_service_roles(connection, roles, policy=None):
    """Verify exact privileges for explicitly present services using one policy.

    Local development has API, worker and platform identities, but no Feishu
    process. Product installation still calls verify(), which requires all four.
    No privilege, ownership, inheritance or RLS check is relaxed for a subset.
    """
    policy = validate_policy(policy) if policy is not None else load_policy()
    if not 160000 <= int(await connection.fetchval('SHOW server_version_num')) < 170000:
        raise RolePolicyError('This installation permission contract is validated for PostgreSQL 16')
    if not roles or not set(roles) <= SERVICES or len(set(roles.values())) != len(roles):
        raise RolePolicyError('Distinct identities for known runtime services are required')
    for role in roles.values():
        identifier(role)
    ledger = dict(await connection.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
    if fingerprint(ledger) != policy['migration_fingerprint']:
        raise RolePolicyError('Migration ledger has not been reviewed for this permission policy')
    await verify_rls(connection, roles, policy)
    for service, role in roles.items():
        rules = policy['services'][service]
        flags = await connection.fetchrow('''SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,
            rolreplication,rolbypassrls FROM pg_roles WHERE rolname=$1''', role)
        if not flags or not flags['rolcanlogin'] or any(v for k, v in flags.items() if k != 'rolcanlogin'):
            raise RolePolicyError(f'{service}: runtime identity missing or elevated')
        memberships = await connection.fetch('''SELECT parent.rolname,m.admin_option,m.inherit_option,m.set_option FROM pg_auth_members m
            JOIN pg_roles parent ON parent.oid=m.roleid JOIN pg_roles child ON child.oid=m.member
            WHERE child.rolname=$1''', role)
        if (sorted(r['rolname'] for r in memberships) != sorted(rules['memberships'])
                or any(r['admin_option'] or r['inherit_option'] or r['set_option'] for r in memberships)):
            raise RolePolicyError(f'{service}: unexpected inherited authority')
        if await connection.fetchval('''SELECT EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname<>$1
            AND r.rolname<>ALL($2::text[]) AND pg_has_role($1,r.oid,'MEMBER'))''', role, rules['memberships']):
            raise RolePolicyError(f'{service}: indirect role inheritance is not allowed')
        if await connection.fetchval("SELECT has_database_privilege($1,current_database(),'CREATE')", role):
            raise RolePolicyError(f'{service}: runtime may create schemas')
        if not await connection.fetchval("SELECT has_database_privilege($1,current_database(),'CONNECT')", role):
            raise RolePolicyError(f'{service}: runtime cannot connect')
        namespaces = await connection.fetch('''SELECT nspname,has_schema_privilege($1,oid,'USAGE') AS usage,
            has_schema_privilege($1,oid,'CREATE') AS create FROM pg_namespace
            WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema' ''', role)
        for ns in namespaces:
            if ns['create'] or (ns['nspname'] != 'public' and ns['usage'] != (ns['nspname'] in rules['schemas'])):
                raise RolePolicyError(f'{service}: unexpected schema privilege on {ns["nspname"]}')
        if await connection.fetchval('''SELECT EXISTS(SELECT 1 FROM pg_class c WHERE c.relowner=(SELECT oid FROM pg_roles WHERE rolname=$1))
            OR EXISTS(SELECT 1 FROM pg_proc p WHERE p.proowner=(SELECT oid FROM pg_roles WHERE rolname=$1))
            OR EXISTS(SELECT 1 FROM pg_namespace n WHERE n.nspowner=(SELECT oid FROM pg_roles WHERE rolname=$1))
            OR EXISTS(SELECT 1 FROM pg_database d WHERE d.datdba=(SELECT oid FROM pg_roles WHERE rolname=$1))''', role):
            raise RolePolicyError(f'{service}: runtime must not own database objects')
        if await connection.fetchval('''SELECT EXISTS(SELECT 1 FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace WHERE p.prosecdef
            AND n.nspname<>ALL($2::text[]) AND n.nspname NOT IN ('pg_catalog','information_schema')
            AND has_schema_privilege($1,n.oid,'USAGE') AND has_function_privilege($1,p.oid,'EXECUTE'))''', role, SCHEMAS):
            raise RolePolicyError(f'{service}: elevated function outside the application contract')
        rows = await connection.fetch('''SELECT c.oid,n.nspname||'.'||c.relname AS name,
            ARRAY(SELECT p FROM unnest($3::text[]) p WHERE has_table_privilege($1,c.oid,p)) AS privileges
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=ANY($2::text[]) AND c.relkind IN ('r','p','v','m','f')''', role, [*SCHEMAS, 'public'], TABLE_PRIVILEGES)
        present = {r['name'] for r in rows}
        if not rules['tables'].keys() <= present or not rules['columns'].keys() <= present:
            raise RolePolicyError(f'{service}: required table missing')
        for row in rows:
            if set(row['privileges']) != set(rules['tables'].get(row['name'], [])):
                raise RolePolicyError(f'{service}: table privileges differ on {row["name"]}')
        columns = await connection.fetch('''SELECT n.nspname||'.'||c.relname AS name,a.attname,
            ARRAY(SELECT p FROM unnest($3::text[]) p WHERE has_column_privilege($1,c.oid,a.attnum,p)) AS privileges
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid
            WHERE n.nspname=ANY($2::text[]) AND c.relkind IN ('r','p','v','m','f') AND a.attnum>0 AND NOT a.attisdropped''',
            role, [*SCHEMAS, 'public'], COLUMN_PRIVILEGES)
        for col in columns:
            expected = set(rules['tables'].get(col['name'], [])) & set(COLUMN_PRIVILEGES)
            expected |= {priv for priv, names in rules['columns'].get(col['name'], {}).items() if col['attname'] in names}
            if set(col['privileges']) != expected:
                raise RolePolicyError(f'{service}: column privileges differ on {col["name"]}.{col["attname"]}')
        sequences = await connection.fetch('''SELECT n.nspname||'.'||c.relname AS name,
            ARRAY(SELECT p FROM unnest($3::text[]) p WHERE has_sequence_privilege($1,c.oid,p)) AS privileges
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=ANY($2::text[]) AND c.relkind='S' ''', role, [*SCHEMAS, 'public'], SEQUENCE_PRIVILEGES)
        if not rules['sequences'].keys() <= {r['name'] for r in sequences}:
            raise RolePolicyError(f'{service}: required sequence missing')
        for row in sequences:
            if set(row['privileges']) != set(rules['sequences'].get(row['name'], [])):
                raise RolePolicyError(f'{service}: sequence privileges differ on {row["name"]}')
        functions = await connection.fetch('''SELECT p.oid::regprocedure::text AS signature,
            has_function_privilege($1,p.oid,'EXECUTE') AS allowed
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=ANY($2::text[])''', role, SCHEMAS)
        # Check EXECUTE even without schema usage: later schema grants must not
        # silently reveal an unreviewed dormant privilege.
        actual = {r['signature'] for r in functions if r['allowed']}
        if actual != set(rules['functions']):
            extra, missing = actual-set(rules['functions']), set(rules['functions'])-actual
            raise RolePolicyError(f'{service}: function privileges differ (extra={sorted(extra)}, missing={sorted(missing)})')
    return {'status': 'passed', 'services': sorted(roles), 'schema': policy['schema'],
            'effective_permissions_verified': True, 'http_verified': False}


async def provision_new(connection, *, database, roles, policy=None, passwords=None):
    """Create new logins atomically; return secrets only to the calling installer.

    Existing roles/data are never adopted or reset. PostgreSQL roles are global,
    so a role name collision anywhere on the cluster refuses the entire operation.
    Services remain stopped and no platform operator/company is created.
    """
    policy = validate_policy(policy) if policy is not None else load_policy()
    identifier(database)
    if set(roles) != SERVICES or len(set(roles.values())) != len(SERVICES):
        raise RolePolicyError('Four distinct runtime identities are required')
    for role in roles.values():
        identifier(role)
    # A durable installer can persist freshly generated secrets before SQL starts,
    # so an uncertain commit never strands an account with an unknown password.
    passwords = passwords if passwords is not None else {service: secrets.token_urlsafe(48) for service in SERVICES}
    if (set(passwords) != SERVICES or any(not isinstance(v, str) or not 32 <= len(v) <= 256
            or any(ord(c) < 33 for c in v) for v in passwords.values())
            or len(set(passwords.values())) != len(SERVICES)):
        raise RolePolicyError('Four distinct private runtime credentials are required')
    async with connection.transaction():
        await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('salesbuddy-runtime-install:'||current_database(),0))")
        if await connection.fetchval('SELECT current_database()') != database:
            raise RolePolicyError('Maintenance connection targets a different database')
        if not await connection.fetchval('SELECT rolsuper FROM pg_roles WHERE rolname=current_user'):
            raise RolePolicyError('Fresh role provisioning requires the installation maintenance administrator')
        if not 160000 <= int(await connection.fetchval('SHOW server_version_num')) < 170000:
            raise RolePolicyError('This installation permission contract is validated for PostgreSQL 16')
        ledger = dict(await connection.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
        if fingerprint(ledger) != policy['migration_fingerprint']:
            raise RolePolicyError('Migration ledger has not been reviewed for this permission policy')
        await verify_rls(connection, {}, policy)
        if await connection.fetchval('''SELECT EXISTS(SELECT 1 FROM platform.workspace)
            OR EXISTS(SELECT 1 FROM platform.user_ref) OR EXISTS(SELECT 1 FROM control.operator)'''):
            raise RolePolicyError('Fresh installation refuses existing tenants, accounts or operators')
        if await connection.fetchval('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=ANY($1::text[]))', list(roles.values())):
            raise RolePolicyError('Runtime identity already exists; refusing adoption')
        # A new database only. Remove ambient privileges, including functions
        # created with PostgreSQL's implicit PUBLIC EXECUTE default.
        await connection.execute(f'REVOKE ALL ON DATABASE {identifier(database)} FROM PUBLIC')
        await connection.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        await connection.execute('ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC')
        for schema in SCHEMAS:
            await connection.execute(f'REVOKE ALL ON SCHEMA {schema} FROM PUBLIC')
            await connection.execute(f'REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM PUBLIC')
            await connection.execute(f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA {schema} FROM PUBLIC')
            await connection.execute(f'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {schema} FROM PUBLIC')
        for service, role in roles.items():
            quoted = identifier(role)
            # Password never goes into a command line, artifact, log or exception.
            literal = await connection.fetchval('SELECT quote_literal($1::text)', passwords[service])
            await connection.execute(f'CREATE ROLE {quoted} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {literal}')
            await connection.execute(f'GRANT CONNECT ON DATABASE {identifier(database)} TO {quoted}')
            await connection.execute(f'ALTER ROLE {quoted} IN DATABASE {identifier(database)} SET search_path=pg_catalog,public')
            rules = policy['services'][service]
            for parent in rules['memberships']:
                # The legacy group identifies a Feishu worker inside reviewed SQL
                # functions. Do not inherit its grants in this or other databases,
                # and do not permit SET ROLE to regain them. All actual privileges
                # are granted explicitly on THIS database below (PostgreSQL 16+).
                await connection.execute(f'GRANT {identifier(parent)} TO {quoted} WITH INHERIT FALSE, SET FALSE')
            for schema in rules['schemas']:
                await connection.execute(f'GRANT USAGE ON SCHEMA {schema} TO {quoted}')
            for kind, sql_kind in (('tables', 'TABLE'), ('sequences', 'SEQUENCE')):
                for name, privileges in rules[kind].items():
                    await connection.execute(f'GRANT {",".join(privileges)} ON {sql_kind} {name} TO {quoted}')
            for name, grants in rules['columns'].items():
                for privilege, columns in grants.items():
                    await connection.execute(f'GRANT {privilege}({",".join(columns)}) ON {name} TO {quoted}')
            for signature in rules['functions']:
                await connection.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO {quoted}')
            for table, row_policy in rules['policies'].items():
                # Explicit current-database policy membership preserves the existing
                # reviewed predicate without inheriting cluster-wide group grants.
                await connection.execute(f'ALTER POLICY {row_policy} ON {table} TO salegent_feishu_worker,{quoted}')
        await verify(connection, roles, policy)
    return passwords
