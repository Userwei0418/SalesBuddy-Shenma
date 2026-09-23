"""V082 keeps the existing function owner and every EXECUTE grant unchanged."""

import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

import asyncpg

from verify_runtime_grants_postgres import migrate_through

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from migrate import migrate


async def main():
    config = {"host": os.environ.get("PGHOST", "/tmp"), "user": os.environ.get("PGUSER", "postgres")}
    admin = await asyncpg.connect(database="postgres", **config)
    suffix = uuid.uuid4().hex[:12]
    database = "salegent_verify_fde_scope_" + suffix
    owner, runtime, observer = ["salegent_scope_" + label + "_" + suffix for label in ("owner", "runtime", "observer")]
    function = "security.fde_customer_in_scope(uuid)"
    metadata_sql = """SELECT proowner,proacl,prosecdef,proconfig,provolatile,proretset,
      prorettype,pronargs FROM pg_proc WHERE oid=$1::regprocedure"""
    connection, roles = None, []
    try:
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0')
        connection = await asyncpg.connect(database=database, **config)
        await migrate_through(connection, 81)
        for role in (owner, runtime, observer):
            await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
            roles.append(role)
            await connection.execute(f'GRANT USAGE ON SCHEMA security,common,crm,platform TO "{role}"')
        await connection.execute(f'GRANT SELECT ON crm.opportunity,crm.opportunity_participant,platform.user_ref,platform.role_binding,platform.team_membership,platform.team TO "{owner}"')
        await connection.execute(f'ALTER FUNCTION {function} OWNER TO "{owner}"')
        await connection.execute(f'REVOKE ALL ON FUNCTION {function} FROM PUBLIC')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION {function} TO "{runtime}" WITH GRANT OPTION')
        await connection.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA security GRANT EXECUTE ON FUNCTIONS TO "{observer}" WITH GRANT OPTION')
        before = await connection.fetchrow(metadata_sql, function)
        await migrate(connection)
        assert await connection.fetchrow(metadata_sql, function) == before
        assert await connection.fetchval("SELECT l.lanname FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang WHERE p.oid=$1::regprocedure", function) == "plpgsql"
        assert not await connection.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", observer, function)
        assert await connection.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE WITH GRANT OPTION')", runtime, function)
        # In-place replay does not reset grants to the deployer's default ACL.
        await connection.execute((ROOT / "migrations/V082__fde_customer_scope_read_path.sql").read_text())
        assert await connection.fetchrow(metadata_sql, function) == before
        assert all(step["status"] == "unchanged" for step in await migrate(connection))
        print(json.dumps({"passed": 4, "checks": ["owner_and_exact_acl_preserved", "security_metadata_unchanged",
              "deployer_default_grant_does_not_leak", "direct_replay_and_migration_replay_unchanged"]}))
    finally:
        if connection:
            await connection.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        for role in reversed(roles):
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


if __name__ == "__main__":
    async def bounded():
        async with asyncio.timeout(120):
            await main()
    asyncio.run(bounded())
