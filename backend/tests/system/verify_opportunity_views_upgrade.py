# ruff: noqa: S108 -- Explicit local PostgreSQL Unix socket allowlist, not temporary credentials.
# ruff: noqa: S608 -- SQL identifiers/fragments are internal constants or validated catalog identifiers.
"""V160 -> V161 preservation and late-runtime ACL checks on a disposable DB."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "database/scripts"), str(ROOT / "backend/src")]
from migrate import migrate  # noqa: E402 -- Local migration tooling path set above.

from sales_backend.db import _initialize_connection  # noqa: E402


async def verify():
    host = os.environ.get("PGHOST", "/tmp")
    if host not in {"localhost", "127.0.0.1", "::1", "/tmp", "/private/tmp"} and not host.startswith(
        ("/tmp/", "/private/tmp/")
    ):
        raise RuntimeError("Local disposable PostgreSQL only")
    pg = {"host": host, "port": int(os.environ.get("PGPORT", "5432")), "user": os.environ.get("PGUSER", "postgres")}
    suffix = uuid4().hex[:12]
    name = "salegent_verify_s3_upgrade_" + suffix
    roles = ["salegent_verify_role_" + uuid4().hex[:12] for _ in range(2)]
    admin = await asyncpg.connect(database="postgres", **pg, command_timeout=30)
    connection = None
    created_roles = []
    try:
        await admin.execute(f"CREATE DATABASE \"{name}\" TEMPLATE template0 ENCODING 'UTF8'")
        connection = await asyncpg.connect(database=name, **pg, command_timeout=30)
        await _initialize_connection(connection)
        with TemporaryDirectory(prefix="shenma-s3-v160-upgrade-") as directory:
            stage = Path(directory)
            for kind in ("baseline", "seeds"):
                (stage / kind).symlink_to(ROOT / "database" / kind, target_is_directory=True)
            (stage / "migrations").mkdir()
            for source in [*(ROOT / "database").glob("V*.sql"), *(ROOT / "database/migrations").glob("V*.sql")]:
                match = re.fullmatch(r"V(\d+)__.+\.sql", source.name)
                assert match
                if int(match.group(1)) <= 160:
                    target = stage / "migrations" if source.parent.name == "migrations" else stage
                    (target / source.name).symlink_to(source)
            assert (await migrate(connection, root=stage))[-1] == {"key": "V160", "status": "applied"}
            assert await connection.fetchval("SELECT to_regclass('crm.opportunity_saved_view')") is None
            workspace, team, user, customer, feishu = [uuid4() for _ in range(5)]
            await connection.execute(
                "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'S3隔离升级样例')",
                workspace,
                "s3-" + suffix,
            )
            await connection.execute(
                "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'S3_TEST','S3既有团队')",
                team,
                workspace,
            )
            await connection.execute(
                "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) "
                "VALUES($1,$2,'S3_USER','S3_USER','S3既有销售')",
                user,
                workspace,
            )
            await connection.execute(
                "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) "
                "VALUES($1,$2,'sales','self',$3)",
                workspace,
                user,
                team,
            )
            await connection.execute(
                "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) "
                "VALUES($1,$2,$3,'sales')",
                workspace,
                user,
                team,
            )
            await connection.execute(
                "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,owner_team_id,"
                "created_by_user_ref_id,company_reference,data_kind) "
                "VALUES($1,$2,'S3既有客户','s3既有客户',$3,$4,'S3_OLD','production')",
                customer,
                workspace,
                team,
                user,
            )
            await connection.execute(
                "INSERT INTO crm.contact(workspace_id,customer_id,name,is_primary,created_by_user_ref_id) "
                "VALUES($1,$2,'虚构联系人',true,$3)",
                workspace,
                customer,
                user,
            )
            await connection.execute(
                "INSERT INTO config.feishu_connection(id,workspace_id,revision,enabled,settings,updated_by) "
                "VALUES($1,$2,3,false,$3::jsonb,$4)",
                feishu,
                workspace,
                {
                    "workspace_id": str(workspace),
                    "connection_id": str(feishu),
                    "fixture_only": True,
                    "base_token": "synthetic-base",
                    "chat_id": "synthetic-chat",
                },
                user,
            )

            async def grant_runtime(role):
                await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
                created_roles.append(role)
                await connection.execute(f'GRANT USAGE ON SCHEMA platform,common,security,crm,config TO "{role}"')
                await connection.execute(f'GRANT SELECT ON platform.user_ref TO "{role}"')
                await connection.execute(
                    "GRANT EXECUTE ON FUNCTION security.resolve_account_actor(text,text,text),"
                    f'security.authorization_has(text),security.authorization_refresh() TO "{role}"'
                )

            await grant_runtime(roles[0])
            await connection.fetchval("SELECT security.reconcile_runtime_grants()")
            tables = [
                tuple(row)
                for row in await connection.fetch(
                    "SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE c.relkind='r' AND n.nspname=ANY($1::text[]) ORDER BY 1,2",
                    ["platform", "crm", "activity", "workflow", "insight", "config", "agent", "ops", "security"],
                )
            ]

            async def snapshot():
                result = {}
                for schema, table in tables:
                    if schema == "ops" and table in {"schema_migration", "migration_checksum"}:
                        continue
                    relation = '"' + schema + '"."' + table + '"'
                    result[relation] = tuple(
                        await connection.fetchrow(
                            "SELECT count(*),md5(COALESCE(string_agg(to_jsonb(t)::text,'|' "
                            "ORDER BY to_jsonb(t)::text),'')) FROM " + relation + " t"
                        )
                    )
                return result

            async def maintenance_acl():
                return await connection.fetchval(
                    "SELECT jsonb_build_object('owner',pg_get_userbyid(proowner),'acl',proacl::text) "
                    "FROM pg_proc WHERE oid='security.reconcile_runtime_grants()'::regprocedure"
                )

            before, acl_before = await snapshot(), await maintenance_acl()
            source = ROOT / "database/migrations/V161__personal_opportunity_saved_views.sql"
            (stage / "migrations" / source.name).symlink_to(source)
            migrated = await migrate(connection, root=stage)
            assert migrated[-1] == {"key": "V161", "status": "applied"}
            after = await snapshot()
            assert after == before, {
                table: (before[table], after[table]) for table in before if before[table] != after[table]
            }
            assert await maintenance_acl() == acl_before
            print(
                f"S3_UPGRADE_OK {len(before)} existing table counts and row digests unchanged; "
                "maintenance ACL preserved",
                flush=True,
            )
            assert all(item["status"] == "unchanged" for item in await migrate(connection, root=stage))
            print("S3_UPGRADE_OK migration replay is unchanged", flush=True)
            await grant_runtime(roles[1])
            await connection.fetchval("SELECT security.reconcile_runtime_grants()")
            for role in roles:
                for privilege in ("SELECT", "INSERT", "UPDATE"):
                    assert await connection.fetchval(
                        "SELECT has_table_privilege($1,'crm.opportunity_saved_view',$2)", role, privilege
                    )
                for privilege in ("DELETE", "TRUNCATE", "TRIGGER", "REFERENCES"):
                    assert not await connection.fetchval(
                        "SELECT has_table_privilege($1,'crm.opportunity_saved_view',$2)", role, privilege
                    )
                for function in ("security.reconcile_runtime_grants()", "crm.protect_opportunity_saved_view()"):
                    assert not await connection.fetchval(
                        "SELECT has_function_privilege($1,$2,'EXECUTE')", role, function
                    )
            assert not await connection.fetchval(
                "SELECT has_table_privilege('salegent_feishu_worker','crm.opportunity_saved_view','SELECT')"
            )
            rls = await connection.fetchrow(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
                "WHERE oid='crm.opportunity_saved_view'::regclass"
            )
            assert all(rls)
            assert await snapshot() == before
            print("S3_UPGRADE_OK existing and late runtime narrow ACL, worker denial and FORCE RLS", flush=True)
    finally:
        if connection is not None:
            await connection.close()
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid<>pg_backend_pid()", name
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        for role in created_roles:
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


if __name__ == "__main__":
    asyncio.run(verify())
