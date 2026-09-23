"""V077 -> V078 exact function ACL inheritance despite deployer default grants."""
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

import asyncpg

from verify_runtime_grants_postgres import migrate_through

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from migrate import migrate


async def main():
    config = {'host': os.environ.get('PGHOST', '/tmp'), 'user': os.environ.get('PGUSER', 'postgres')}
    admin = await asyncpg.connect(database='postgres', **config)
    suffix = uuid.uuid4().hex[:12]
    database = 'salegent_verify_typed_acl_' + suffix
    observer, runtime = ['salegent_acl_' + kind + '_' + suffix for kind in ('observer', 'worker')]
    connection = None
    roles = []
    try:
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0')
        connection = await asyncpg.connect(database=database, **config)
        await migrate_through(connection, 77)
        for role in (observer, runtime):
            await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
            roles.append(role)
        await connection.execute(f'GRANT USAGE ON SCHEMA ops TO "{observer}","{runtime}"')
        await connection.execute('REVOKE ALL ON FUNCTION ops.claim_job(text,integer) FROM PUBLIC')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION ops.claim_job(text,integer) TO "{runtime}" WITH GRANT OPTION')
        # Simulate customer-specific defaults that must not widen this new definer.
        await connection.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA ops GRANT EXECUTE ON FUNCTIONS TO "{observer}" WITH GRANT OPTION')
        await migrate(connection)
        old = 'ops.claim_job(text,integer)'
        acl_sql = """SELECT a.grantee,a.privilege_type,a.is_grantable FROM pg_proc p
          CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
          WHERE p.oid=$1::regprocedure ORDER BY a.grantee,a.privilege_type,a.is_grantable"""
        for new in ('ops.claim_job(text,integer,text[],boolean)', 'ops.reject_invalid_job_actor(uuid,uuid)'):
            for privilege in ('EXECUTE', 'EXECUTE WITH GRANT OPTION'):
                for role in (observer, runtime):
                    before = await connection.fetchval('SELECT has_function_privilege($1,$2,$3)', role, old, privilege)
                    after = await connection.fetchval('SELECT has_function_privilege($1,$2,$3)', role, new, privilege)
                    assert before == after == (role == runtime), (new, role, privilege, before, after)
            assert await connection.fetch(acl_sql, old) == await connection.fetch(acl_sql, new)
            assert await connection.fetchval('SELECT proowner FROM pg_proc WHERE oid=$1::regprocedure', old) == \
                   await connection.fetchval('SELECT proowner FROM pg_proc WHERE oid=$1::regprocedure', new)
        assert all(step['status'] == 'unchanged' for step in await migrate(connection))
        print(json.dumps({'passed': 3, 'checks': ['deployer_default_grantee_removed',
            'old_execute_acl_owner_and_grant_options_exactly_inherited', 'repeat_deployment_unchanged']}))
    finally:
        if connection:
            await connection.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        for role in reversed(roles):
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(120):
            await main()
    asyncio.run(bounded())
