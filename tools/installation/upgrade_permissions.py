"""Reconcile current-database grants after a reviewed, transactional upgrade.

Caller must first verify the old instance and keep the transaction open. Never
creates cluster roles, changes passwords or grants inherited group authority.
"""
from runtime_roles import (SCHEMAS, SERVICES, COLUMN_PRIVILEGES, RolePolicyError,
                           identifier, validate_policy, verify_service_roles, fingerprint)


async def cluster_identity(connection):
    # Compare only in memory, never log credential hashes. Detect migrations that
    # unexpectedly change any cluster identity, not only our four runtime roles.
    roles = [dict(r) for r in await connection.fetch('SELECT * FROM pg_authid ORDER BY oid')]
    memberships = [dict(r) for r in await connection.fetch('SELECT * FROM pg_auth_members ORDER BY roleid,member,grantor')]
    settings = [dict(r) for r in await connection.fetch('SELECT * FROM pg_db_role_setting ORDER BY setdatabase,setrole')]
    return roles, memberships, settings


async def reconcile(connection, roles, policy):
    """Product upgrades always require all four existing runtime identities."""
    if set(roles) != SERVICES or len(set(roles.values())) != 4:
        raise RolePolicyError('Four existing runtime identities required')
    return await reconcile_service_roles(connection, roles, policy)


async def reconcile_service_roles(connection, roles, policy):
    """Apply the shared exact policy to the explicitly running service set."""
    policy = validate_policy(policy)
    if not connection.is_in_transaction():
        raise RolePolicyError('Upgrade grants must share the migration transaction')
    if not await connection.fetchval('SELECT rolsuper FROM pg_roles WHERE rolname=current_user'):
        raise RolePolicyError('Upgrade requires the explicit maintenance administrator')
    if not roles or not set(roles) <= SERVICES or len(set(roles.values())) != len(roles):
        raise RolePolicyError('Distinct identities for known runtime services are required')
    for role in roles.values(): identifier(role)
    ledger = dict(await connection.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
    if fingerprint(ledger) != policy['migration_fingerprint']:
        raise RolePolicyError('New migration ledger does not match reviewed permissions')
    columns = await connection.fetch('''SELECT n.nspname,c.relname,a.attname
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p','v','m','f')
          AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL''', SCHEMAS)
    # New objects may inherit PostgreSQL PUBLIC EXECUTE; remove ambient authority
    # before applying the exact new contract. No global role attributes change.
    await connection.execute('ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC')
    for schema in SCHEMAS:
        for kind in ('TABLES', 'SEQUENCES', 'FUNCTIONS'):
            await connection.execute(f'REVOKE ALL ON ALL {kind} IN SCHEMA {schema} FROM PUBLIC')
        await connection.execute(f'REVOKE ALL ON SCHEMA {schema} FROM PUBLIC')
    for role in roles.values():
        quoted = identifier(role)
        for schema in SCHEMAS:
            for kind in ('TABLES', 'SEQUENCES', 'FUNCTIONS'):
                await connection.execute(f'REVOKE ALL ON ALL {kind} IN SCHEMA {schema} FROM {quoted}')
            await connection.execute(f'REVOKE ALL ON SCHEMA {schema} FROM {quoted}')
    for row in columns:
        table = identifier(row['nspname'])+'.'+identifier(row['relname'])
        for principal in ['PUBLIC', *(identifier(r) for r in roles.values())]:
            for privilege in COLUMN_PRIVILEGES:
                await connection.execute(f'REVOKE {privilege}({identifier(row["attname"])}) ON {table} FROM {principal}')
    for service, role in roles.items():
        rules = policy['services'][service]; quoted = identifier(role)
        for schema in rules['schemas']:
            await connection.execute(f'GRANT USAGE ON SCHEMA {schema} TO {quoted}')
        for kind, sql_kind in (('tables','TABLE'),('sequences','SEQUENCE')):
            for name, privileges in rules[kind].items():
                await connection.execute(f'GRANT {",".join(privileges)} ON {sql_kind} {name} TO {quoted}')
        for name, grants in rules['columns'].items():
            for privilege, names in grants.items():
                await connection.execute(f'GRANT {privilege}({",".join(names)}) ON {name} TO {quoted}')
        for signature in rules['functions']:
            await connection.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO {quoted}')
        for table, row_policy in rules['policies'].items():
            await connection.execute(f'ALTER POLICY {row_policy} ON {table} TO salegent_feishu_worker,{quoted}')
    return await verify_service_roles(connection, roles, policy)
