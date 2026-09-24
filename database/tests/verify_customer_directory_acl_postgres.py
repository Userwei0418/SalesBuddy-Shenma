"""V078 -> V079 owner/EXECUTE ACL inheritance in a disposable database only."""

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
    database = "salegent_verify_directory_acl_" + suffix
    owner, observer, runtime = ["salegent_directory_" + kind + "_" + suffix for kind in ("owner", "observer", "runtime")]
    connection = None
    roles = []
    old = "security.company_customer_directory(text,integer,integer)"
    new = "security.company_customer_directory_page(text,integer,integer)"
    added = ["security.company_customer_directory_search(text,integer,integer,text,text,text[])",
             "security.company_customer_directory_search_names(text)", "security.company_customer_directory_industries()"]
    acl_sql = """SELECT a.grantee,a.privilege_type,a.is_grantable FROM pg_proc p
      CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
      WHERE p.oid=$1::regprocedure ORDER BY a.grantee,a.privilege_type,a.is_grantable"""
    try:
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0')
        connection = await asyncpg.connect(database=database, **config)
        await migrate_through(connection, 78)
        for role in (owner, observer, runtime):
            await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
            roles.append(role)
            await connection.execute(f'GRANT USAGE ON SCHEMA security,common,crm,platform TO "{role}"')
        await connection.execute(f'GRANT SELECT ON crm.customer,crm.customer_ownership,crm.customer_claim_request,platform.team,platform.user_ref TO "{owner}"')
        await connection.execute(f'ALTER FUNCTION {old} OWNER TO "{owner}"')
        await connection.execute(f'REVOKE ALL ON FUNCTION {old} FROM PUBLIC')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION {old} TO "{runtime}" WITH GRANT OPTION')
        # The new function's default owner differs from the directory's owner.
        # Customer-specific default grants also differ from the directory ACL.
        await connection.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA security GRANT EXECUTE ON FUNCTIONS TO "{observer}" WITH GRANT OPTION')
        before_acl = await connection.fetch(acl_sql, old)
        before_owner = await connection.fetchval("SELECT proowner FROM pg_proc WHERE oid=$1::regprocedure", old)
        await migrate(connection)
        for function in (old, new, *added):
            assert await connection.fetch(acl_sql, function) == before_acl
            assert await connection.fetchval("SELECT proowner FROM pg_proc WHERE oid=$1::regprocedure", function) == before_owner
        for function in added:
            assert not await connection.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", observer, function)
            assert await connection.fetchval("SELECT prosecdef AND proconfig=ARRAY['search_path=pg_catalog'] FROM pg_proc WHERE oid=$1::regprocedure", function)
        for privilege in ("EXECUTE", "EXECUTE WITH GRANT OPTION"):
            for role in (observer, runtime):
                assert await connection.fetchval("SELECT has_function_privilege($1,$2,$3)", role, new, privilege) == (role == runtime)
        assert await connection.fetchval("SELECT prosecdef AND proconfig=ARRAY['search_path=pg_catalog'] FROM pg_proc WHERE oid=$1::regprocedure", new)
        # The dump baseline disables row_security on the migration session.
        # Exercise the function with the same setting as a fresh runtime
        # connection, including the deliberately non-bypass function owner.
        await connection.execute("SET row_security = on")
        await connection.execute(f'SET ROLE "{runtime}"')
        # No valid identity: neither the total nor a reference row is exposed.
        result = json.loads(await connection.fetchval(f"SELECT {new.split('(')[0]}(NULL,50,0)"))
        assert result == {"items": [], "total": 0, "has_more": False, "next_offset": None}
        assert await connection.fetch(f"SELECT * FROM {old.split('(')[0]}(NULL,50,0)") == []
        assert json.loads(await connection.fetchval("SELECT security.company_customer_directory_search(NULL,50,0,NULL,NULL,ARRAY['商汤'])")) == result
        assert await connection.fetch("SELECT * FROM security.company_customer_directory_search_names(NULL)") == []
        assert json.loads(await connection.fetchval("SELECT security.company_customer_directory_industries()")) == []
        await connection.execute("RESET ROLE")
        assert all(step["status"] == "unchanged" for step in await migrate(connection))
        await connection.execute((ROOT / "migrations/V147__customer_claim_directory_filters.sql").read_text())
        for function in added:
            assert await connection.fetch(acl_sql, function) == before_acl
            assert await connection.fetchval("SELECT proowner FROM pg_proc WHERE oid=$1::regprocedure", function) == before_owner
        print(json.dumps({"passed": 4, "checks": ["original_owner_acl_and_grant_options_preserved",
            "deployer_default_acl_removed", "no_identity_has_no_references_or_total", "repeat_migration_unchanged"]}))
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
