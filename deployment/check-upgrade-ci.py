"""Exercise a populated V125 -> current upgrade in a disposable local database.

No customer connection string, account or business data is used. The exact
customer ACL is mapped to a unique temporary role and checked after migration.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "database/scripts"), str(ROOT / "backend/src")]
from migrate import migrate
from sales_backend.auth.passwords import encode_password, verify_password
from sales_backend.db import set_request_context
from sales_backend.repositories.identity import IdentityRepository


async def main():
    host = os.environ.get("PGHOST", "/tmp")
    if host not in {"/tmp", "127.0.0.1", "localhost"}:
        raise SystemExit("Only a local disposable PostgreSQL instance is allowed")
    settings = {"host": host, "user": os.environ.get("PGUSER", "postgres")}
    admin = await asyncpg.connect(database="postgres", **settings)
    suffix = uuid4().hex[:12]
    name, role = "shenma_upgrade_" + suffix, "shenma_upgrade_role_" + suffix
    c, role_created = None, False
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        c = await asyncpg.connect(database=name, **settings)
        for typ in ("json", "jsonb"):
            await c.set_type_codec(typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads)
        with tempfile.TemporaryDirectory(prefix="shenma-v125-") as directory:
            baseline = Path(directory)
            shutil.copytree(ROOT / "database", baseline, dirs_exist_ok=True)
            for file in (baseline / "migrations").glob("V*.sql"):
                if int(file.name.split("__")[0][1:]) > 125:
                    file.unlink()
            await migrate(c, baseline)
        assert await c.fetchval("SELECT max(version) FROM ops.schema_migration") == "V125"
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
        role_created = True
        await c.execute((ROOT / "deployment/runtime-grants.sql").read_text().replace("shenma_runtime", role))
        companies = []
        password = "Disposable-upgrade-acceptance-9"
        roles = [("OPSADMIN", "administrator", "workspace")]
        for number in (1, 2):
            roles += [(f"{code}00{number}", kind, scope) for code, kind, scope in (
                ("XS", "sales", "self"), ("ZG", "supervisor", "team"),
                ("ZJL", "manager", "workspace"), ("FDE", "fde", "self"),
                ("FDEZG", "fde_lead", "team"), ("YY", "operations", "workspace"))]
        for index in range(2):
            workspace, team = uuid4(), uuid4()
            external = "isolated-upgrade-" + str(index)
            await c.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$2)", workspace, external)
            await c.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'TEST','隔离测试团队')", team, workspace)
            people = {}
            for code, kind, scope in roles:
                user = uuid4()
                await c.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)", user, workspace, code)
                await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)", workspace, user, kind, scope, team)
                await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,$4)", workspace, user, team, kind)
                await c.execute("INSERT INTO platform.password_credential(user_ref_id,workspace_id,password_hash,must_change_password) VALUES($1,$2,$3,false)", user, workspace, encode_password(password))
                people[code] = user
            for code in ("XS001", "XS002"):
                await c.execute("INSERT INTO crm.customer(workspace_id,name,normalized_name,owner_user_ref_id,owner_team_id,created_by_user_ref_id,level_code) VALUES($1,$2,$2,$3,$4,$3,'Tier-2')", workspace, external + code, people[code], team)
            companies.append((workspace, external, people))
        preserved = ("platform.user_ref", "platform.password_credential", "platform.role_binding",
                     "platform.team_membership", "crm.customer", "crm.customer_ownership")
        async def snapshot():
            return {table: sorted(json.dumps(dict(row), default=str, sort_keys=True) for row in await c.fetch(f"SELECT * FROM {table}")) for table in preserved}
        before = await snapshot()
        result = await migrate(c)
        assert [row["key"] for row in result if row["status"] == "applied"] == [f"V{i}" for i in range(126, 153)]
        assert await snapshot() == before, "Upgrade changed existing identities or business records"
        assert all(row["status"] == "unchanged" for row in await migrate(c))
        # The pg_dump baseline turns RLS off for its superuser restore session.
        # Fresh application connections use the normal enabled setting.
        await c.execute("SET row_security=on")
        identity = IdentityRepository()
        checked = 0
        for workspace, external, people in companies:
            for code, kind, scope in roles:
                credential = await c.fetchrow("SELECT * FROM platform.password_credential WHERE user_ref_id=$1", people[code])
                assert verify_password(password, credential["password_hash"]) and not credential["must_change_password"]
                async with c.transaction():
                    await c.execute(f'SET LOCAL ROLE "{role}"')
                    # Password-backed accounts deliberately cannot use the demo resolver.
                    assert await c.fetchval("SELECT count(*) FROM security.password_login_identifier($1,$2)", external, code) == 1
                    record = await c.fetchrow("SELECT * FROM security.resolve_account_actor($1,$2,$3)", external, code, kind)
                    assert record is not None, (external, code)
                    actor = identity._actor(record).context
                    assert actor.user_id == str(people[code]) and actor.role.value == kind
                    await set_request_context(c, actor)
                    assert not await c.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                    assert not await c.fetchval("SELECT has_table_privilege(current_user,'platform.password_credential','SELECT')")
                    assert not await c.fetchval("SELECT has_function_privilege(current_user,'security.reconcile_runtime_grants()','EXECUTE')")
                    assert await c.fetchval("SELECT count(*) FROM crm.customer WHERE workspace_id<>$1", workspace) == 0
                    if kind == "sales":
                        assert await c.fetchval("SELECT count(*) FROM crm.customer") == 1
                        assert not await c.fetchval("SELECT security.authorization_has('access.console')")
                    if kind == "administrator":
                        assert await c.fetchval("SELECT count(*) FROM crm.customer") == 2
                        assert await c.fetchval("SELECT security.authorization_has('authorization.accounts_manage')")
                    checked += 1
        print(json.dumps({"upgrade": "V125->V152", "applied": 27, "companies": 2,
                          "accounts_verified": checked, "existing_rows_unchanged": True,
                          "repeat_migration_unchanged": True, "runtime_acl_and_isolation": True}))
    finally:
        if c:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if role_created:
            await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())
