"""V159 -> V160 preserves customer data, custom authorization and Feishu settings.

Only local disposable PostgreSQL databases and non-bypass roles are accepted.
All fixtures are synthetic, including disabled Feishu settings with no credentials.
Run with the same PGHOST/PGPORT/PGUSER as verify_customer_creation_requests_postgres.py.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "database/scripts"), str(ROOT / "backend/src")]
from migrate import migrate
from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.repositories.identity import IdentityRepository


FUNCTIONS = (
    "security.customer_name_is_available(text)",
    "security.submit_customer_creation_request(jsonb)",
    "security.withdraw_customer_creation_request(uuid)",
    "security.customer_creation_request_lock(uuid)",
    "security.complete_customer_creation_request(uuid,text,text,uuid)",
)
ROLE_CODES = ("sales", "supervisor", "manager", "operations", "administrator")


async def verify() -> None:
    host = os.environ.get("PGHOST", "/tmp")
    if host not in {"localhost", "127.0.0.1", "::1"} and not host.startswith(("/tmp", "/private/tmp")):
        raise RuntimeError("Upgrade verification accepts only local PostgreSQL host/socket")
    pg = {"host": host, "port": int(os.environ.get("PGPORT", "5432")),
          "user": os.environ.get("PGUSER", "postgres")}
    suffix = uuid4().hex[:12]
    name = "salegent_verify_s2_upgrade_" + suffix
    runtime = "salegent_verify_role_" + suffix
    late_runtime = "salegent_verify_role_" + uuid4().hex[:12]
    admin = await asyncpg.connect(database="postgres", **pg, command_timeout=30)
    connection = None
    roles_created: list[str] = []
    checks: list[str] = []

    def passed(label):
        checks.append(label)
        print("S2_UPGRADE_OK " + label, flush=True)

    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0 ENCODING \'UTF8\'')
        connection = await asyncpg.connect(database=name, **pg, command_timeout=30)
        await _initialize_connection(connection)
        assert await connection.fetchval("SHOW server_encoding") == "UTF8"
        with TemporaryDirectory(prefix="shenma-s2-v159-upgrade-") as directory:
            stage = Path(directory)
            for kind in ("baseline", "seeds"):
                (stage / kind).symlink_to(ROOT / "database" / kind, target_is_directory=True)
            (stage / "migrations").mkdir()
            for source in [*(ROOT / "database").glob("V*.sql"), *(ROOT / "database/migrations").glob("V*.sql")]:
                match = re.fullmatch(r"V(\d+)__.+\.sql", source.name)
                assert match, source.name
                if int(match.group(1)) <= 159:
                    parent = stage / "migrations" if source.parent.name == "migrations" else stage
                    (parent / source.name).symlink_to(source)
            assert (await migrate(connection, root=stage))[-1] == {"key": "V159", "status": "applied"}
            assert await connection.fetchval("SELECT to_regclass('crm.customer_creation_request')") is None

            async def fixture(label):
                workspace, team = uuid4(), uuid4()
                external = "s2-upgrade-" + label + "-" + uuid4().hex
                users = {}
                await connection.execute(
                    "INSERT INTO platform.workspace(id,external_workspace_id,name,attributes) "
                    "VALUES($1,$2,$3,$4::jsonb)", workspace, external, "升级保留样例 " + label,
                    {"fixture": "s2-upgrade", "unchanged": True},
                )
                await connection.execute(
                    "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,$4)",
                    team, workspace, "S2_" + label, "升级保留团队 " + label,
                )
                for role_code in ROLE_CODES:
                    user = uuid4()
                    code = label.upper() + "_" + role_code.upper()
                    users[role_code] = {"id": user, "code": code}
                    await connection.execute(
                        "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) "
                        "VALUES($1,$2,$3,$3,$3)", user, workspace, code,
                    )
                    scope = "self" if role_code == "sales" else "team" if role_code == "supervisor" else "workspace"
                    await connection.execute(
                        "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) "
                        "VALUES($1,$2,$3,$4,$5)", workspace, user, role_code, scope, team,
                    )
                    await connection.execute(
                        "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) "
                        "VALUES($1,$2,$3,$4)", workspace, user, team, role_code,
                    )
                customer = uuid4()
                await connection.execute(
                    "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,owner_team_id,"
                    "created_by_user_ref_id,company_reference,company_verified_at,company_verified_by,data_kind) "
                    "VALUES($1,$2,$3,$3,$4,$5,$6,clock_timestamp(),$5,'production')",
                    customer, workspace, "V159 既有客户 " + label, team, users["operations"]["id"], "V159-" + label,
                )
                await connection.execute(
                    "INSERT INTO crm.contact(workspace_id,customer_id,name,is_primary,created_by_user_ref_id) "
                    "VALUES($1,$2,'虚构旧联系人',true,$3)", workspace, customer, users["operations"]["id"],
                )
                feishu_id = uuid4()
                await connection.execute(
                    "INSERT INTO config.feishu_connection(id,workspace_id,revision,enabled,settings,updated_by) "
                    "VALUES($1,$2,3,false,$3::jsonb,$4)", feishu_id, workspace,
                    {"workspace_id": str(workspace), "connection_id": str(feishu_id),
                     "fixture_only": True, "base_token": "synthetic-not-a-real-base",
                     "chat_id": "synthetic-not-a-real-chat", "tables": {"visit": "fixture-visit", "weekly": "fixture-weekly"}},
                    users["administrator"]["id"],
                )
                return {"id": workspace, "external": external, "team": team, "users": users}

            async with connection.transaction():
                stock = await fixture("stock")
                customized = await fixture("custom")
                custom_roles = {
                    row["builtin_role_code"]: row["id"] for row in await connection.fetch(
                        "SELECT id,builtin_role_code FROM config.permission_role WHERE workspace_id=$1",
                        customized["id"],
                    )
                }
                # A grant change without audit still makes a builtin non-stock.
                assert await connection.execute(
                    "DELETE FROM config.permission_role_grant WHERE role_id=$1 AND permission_code='customer.claim'",
                    custom_roles["sales"],
                ) == "DELETE 1"
                # A previously edited role with grants matching defaults is also preserved.
                for code in ("supervisor", "operations"):
                    await connection.execute(
                        "INSERT INTO config.authorization_audit(workspace_id,actor_user_ref_id,subject_type,subject_id,"
                        "before_snapshot,after_snapshot,reason) VALUES($1,$2,'role',$3,'{}','{}','Synthetic prior customization')",
                        customized["id"], customized["users"]["administrator"]["id"], custom_roles[code],
                    )
                await connection.execute(
                    "UPDATE config.permission_role SET status='inactive',version_no=4 WHERE id=$1", custom_roles["manager"],
                )
                assert await connection.execute(
                    "DELETE FROM config.permission_role_grant WHERE role_id=$1 AND permission_code='customer.create'",
                    custom_roles["administrator"],
                ) == "DELETE 1"
                explicit_role = uuid4()
                await connection.execute(
                    "INSERT INTO config.permission_role(id,workspace_id,name,description,version_no) "
                    "VALUES($1,$2,'S2 已自定义团队只读','Synthetic custom role retained verbatim',7)",
                    explicit_role, customized["id"],
                )
                await connection.execute(
                    "INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code,team_ids) "
                    "VALUES($1,$2,'customer.read','teams',ARRAY[$3::uuid])", customized["id"], explicit_role, customized["team"],
                )
                for company in (stock, customized):
                    user = company["users"]["sales"]["id"]
                    await connection.execute(
                        "INSERT INTO config.account_authorization(workspace_id,user_ref_id,version_no) VALUES($1,$2,5) "
                        "ON CONFLICT(workspace_id,user_ref_id) DO UPDATE SET version_no=5", company["id"], user,
                    )
                    await connection.execute(
                        "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) "
                        "VALUES($1,$2,'customer.update','deny')", company["id"], user,
                    )
                await connection.execute(
                    "INSERT INTO config.account_permission_role(workspace_id,user_ref_id,role_id,scope_code,team_ids) "
                    "VALUES($1,$2,$3,'teams',ARRAY[$4::uuid])", customized["id"],
                    customized["users"]["sales"]["id"], explicit_role, customized["team"],
                )

            async def grant_runtime(role_name):
                assert re.fullmatch(r"salegent_verify_role_[a-f0-9]+", role_name)
                await admin.execute(f'CREATE ROLE "{role_name}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
                roles_created.append(role_name)
                await connection.execute(f'GRANT USAGE ON SCHEMA platform,common,security,crm,config TO "{role_name}"')
                await connection.execute(f'GRANT SELECT ON platform.user_ref TO "{role_name}"')
                await connection.execute(
                    f'GRANT EXECUTE ON FUNCTION security.resolve_account_actor(text,text,text),'
                    f'security.authorization_has(text),security.authorization_refresh() TO "{role_name}"'
                )

            await grant_runtime(runtime)
            await connection.fetchval("SELECT security.reconcile_runtime_grants()")
            preserved_tables = [tuple(row) for row in await connection.fetch(
                "SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE c.relkind='r' AND n.nspname=ANY($1::text[]) "
                "AND EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='workspace_id') "
                "AND (n.nspname,c.relname) NOT IN (('config','permission_role'),('config','permission_role_grant')) "
                "ORDER BY 1,2", ["platform", "crm", "activity", "workflow", "insight", "config", "agent", "ops", "security"],
            )]

            async def preserved_snapshot():
                result = {}
                for schema, table in preserved_tables:
                    # Identifiers come only from pg_catalog, never task input.
                    relation = '"' + schema + '"."' + table + '"'
                    result[relation] = await connection.fetchrow(
                        "SELECT count(*) AS count,md5(COALESCE(string_agg(to_jsonb(t)::text,'|' "
                        "ORDER BY to_jsonb(t)::text),'')) AS digest FROM " + relation + " t"
                    )
                result["quota"] = await connection.fetchval("SELECT to_jsonb(q) FROM security.deployment_account_quota q")
                return result

            async def role_snapshot():
                return {str(row["id"]): dict(row) for row in await connection.fetch("SELECT * FROM config.permission_role")}

            async def grant_snapshot():
                return {(str(row["role_id"]), row["permission_code"]): dict(row)
                        for row in await connection.fetch("SELECT * FROM config.permission_role_grant")}

            async def defaults_snapshot():
                return {row["role_code"]: dict(row) for row in await connection.fetch("SELECT * FROM config.permission_role_default")}

            async def maintenance_acl():
                return await connection.fetchval(
                    "SELECT jsonb_build_object('owner',pg_get_userbyid(p.proowner),'acl',p.proacl::text) "
                    "FROM pg_proc p WHERE p.oid='security.reconcile_runtime_grants()'::regprocedure"
                )

            before = await preserved_snapshot()
            roles_before, grants_before, defaults_before = await role_snapshot(), await grant_snapshot(), await defaults_snapshot()
            acl_before = await maintenance_acl()
            stock_ids = {key for key, row in roles_before.items()
                         if row["workspace_id"] == stock["id"] and row["builtin_role_code"] in ROLE_CODES}
            assert len(stock_ids) == 5
            source = ROOT / "database/migrations/V160__customer_creation_request_approval.sql"
            (stage / "migrations" / source.name).symlink_to(source)
            result = await migrate(connection, root=stage)
            assert result[-1] == {"key": "V160", "status": "applied"}
            assert await preserved_snapshot() == before
            assert await maintenance_acl() == acl_before
            passed("existing_business_organization_accounts_overrides_feishu_and_quota_unchanged")

            roles_after, grants_after = await role_snapshot(), await grant_snapshot()
            assert roles_after.keys() == roles_before.keys()
            for role_id, prior in roles_before.items():
                current = roles_after[role_id]
                if role_id not in stock_ids:
                    assert current == prior, (role_id, "customized role changed")
                else:
                    assert current["version_no"] == prior["version_no"] + 1
                    assert {k: v for k, v in current.items() if k not in {"version_no", "updated_at"}} == {
                        k: v for k, v in prior.items() if k not in {"version_no", "updated_at"}
                    }
            assert {key: value for key, value in grants_after.items() if key in grants_before} == grants_before
            added = {key: value for key, value in grants_after.items() if key not in grants_before}
            assert len(added) == 5
            for role_id in stock_ids:
                builtin = roles_before[role_id]["builtin_role_code"]
                permission = "customer.create_request" if builtin in {"sales", "supervisor", "manager"} else "customer.create_review"
                grant = added[(role_id, permission)]
                assert grant["scope_code"] == ("self" if permission.endswith("request") else "workspace")
                assert grant["team_ids"] == []
            defaults_after = await defaults_snapshot()
            for code, prior in defaults_before.items():
                current = defaults_after[code]
                if code not in ROLE_CODES:
                    assert current == prior
                else:
                    permission = "customer.create_request" if code in {"sales", "supervisor", "manager"} else "customer.create_review"
                    assert current == {**prior, "permissions": prior["permissions"] + [{
                        "permission": permission, "scope": "self" if permission.endswith("request") else "workspace", "team_ids": [],
                    }]}
            passed("only_five_stock_roles_and_defaults_receive_exact_S2_grants")
            passed("grant_modified_audited_inactive_and_explicit_custom_roles_preserved")

            async def permissions(company, code):
                runtime_connection = await asyncpg.connect(database=name, **pg, command_timeout=15)
                await _initialize_connection(runtime_connection)
                try:
                    async with runtime_connection.transaction():
                        await runtime_connection.execute(f'SET LOCAL ROLE "{runtime}"')
                        assert not await runtime_connection.fetchval(
                            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
                        )
                        row = await runtime_connection.fetchrow(
                            "SELECT * FROM security.resolve_account_actor($1,$2,NULL)", company["external"], company["users"][code]["code"],
                        )
                        assert row, code
                        await set_request_context(runtime_connection, IdentityRepository._actor(row).context)
                        return {permission: await runtime_connection.fetchval("SELECT security.authorization_has($1)", permission)
                                for permission in ("customer.create_request", "customer.create_review", "customer.update")}
                finally:
                    await runtime_connection.close()

            for code in ROLE_CODES:
                effective = await permissions(stock, code)
                assert effective["customer.create_request"] == (code in {"sales", "supervisor", "manager"}), effective
                assert effective["customer.create_review"] == (code in {"operations", "administrator"}), effective
                if code == "sales":
                    assert not effective["customer.update"], effective
            for code in ("sales", "supervisor", "operations", "administrator"):
                effective = await permissions(customized, code)
                assert not effective["customer.create_request"] and not effective["customer.create_review"], (code, effective)
                if code == "sales":
                    assert not effective["customer.update"], effective
            passed("runtime_stock_permissions_and_existing_account_deny_override_are_effective")

            async def assert_acl(role_name):
                assert await connection.fetchval("SELECT has_table_privilege($1,'crm.customer_creation_request','SELECT')", role_name)
                for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                    assert not await connection.fetchval(
                        "SELECT has_table_privilege($1,'crm.customer_creation_request',$2)", role_name, privilege,
                    )
                for function in FUNCTIONS:
                    assert await connection.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role_name, function)
                    assert not await connection.fetchval(
                        "SELECT has_function_privilege('salegent_feishu_worker',$1,'EXECUTE')", function,
                    )
                for function in ("security.reconcile_runtime_grants()", "security.protect_customer_creation_request()"):
                    assert not await connection.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role_name, function)

            await assert_acl(runtime)
            assert not await connection.fetchval(
                "SELECT has_table_privilege('salegent_feishu_worker','crm.customer_creation_request','SELECT,INSERT,UPDATE,DELETE,TRUNCATE')"
            )
            passed("preexisting_runtime_gets_controlled_ACL_and_feishu_worker_stays_excluded")
            await grant_runtime(late_runtime)
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1,'crm.customer_creation_request','SELECT')", late_runtime,
            )
            replay = await migrate(connection, root=stage)
            assert all(step["status"] == "unchanged" for step in replay)
            await assert_acl(late_runtime)
            assert await preserved_snapshot() == before
            assert await role_snapshot() == roles_after
            assert await grant_snapshot() == grants_after
            assert await defaults_snapshot() == defaults_after
            assert await maintenance_acl() == acl_before
            assert await connection.fetchval("SELECT count(*) FROM crm.customer_creation_request") == 0
            passed("idempotent_replay_preserves_rows_and_reconciles_late_runtime_role")
            print(json.dumps({"status": "passed", "suite": "customer_creation_requests_v159_v160_upgrade",
                              "database": name, "preserved_workspace_tables": len(preserved_tables),
                              "stock_roles_extended": len(stock_ids), "checks": checks}, ensure_ascii=False, indent=2), flush=True)
    finally:
        if connection:
            await connection.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        for role_name in reversed(roles_created):
            await admin.execute(f'DROP ROLE "{role_name}"')
        await admin.close()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(verify(), timeout=240))
