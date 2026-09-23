"""V123 -> V124 projection-only upgrade on synthetic, disposable PostgreSQL data."""
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import asyncpg
from verify_fde_postgres import context
from verify_runtime_grants_postgres import migrate_through

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT.parent / "backend/src"))
from migrate import migrate
from sales_backend.feishu_worker import verify_worker_role


def local_host(value):
    if value in {"127.0.0.1", "localhost", "::1", "/tmp"}:
        return value
    if value.startswith("/tmp/") and ".." not in Path(value).parts:
        return value
    raise ValueError("Only loopback or a local /tmp socket is allowed")


async def main():
    config = {"host": local_host(os.environ.get("PGHOST", "/tmp")),
              "port": int(os.environ.get("PGPORT", "5432")), "user": os.environ.get("PGUSER", "postgres"),
              "password": "", "passfile": "/dev/null", "ssl": False}
    admin = await asyncpg.connect(database="postgres", **config)
    suffix = uuid.uuid4().hex[:12]
    database, worker, application = (prefix + suffix for prefix in
        ("salegent_creation_verify_", "salegent_creation_worker_", "salegent_creation_app_"))
    connection = None
    created_roles = []
    checks = []
    try:
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0')
        connection = await asyncpg.connect(database=database, **config)
        for typ in ("json", "jsonb"):
            await connection.set_type_codec(typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads)
        await migrate_through(connection, 123)
        ws, other_ws, operator, outsider, customer, excluded_customer, foreign_customer, sync, foreign_sync = [
            uuid.uuid4() for _ in range(9)]
        for workspace, person in ((ws, operator), (other_ws, outsider)):
            await connection.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Synthetic creation provenance')", workspace, str(workspace))
            await connection.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) VALUES($1,$2,$3,'Synthetic operator')", person, workspace, str(person))
            await connection.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,'administrator','workspace')", workspace, person)
        for cid, workspace, person, kind in (
            (customer, ws, operator, "production"), (excluded_customer, ws, operator, "demo"),
            (foreign_customer, other_ws, outsider, "production"),
        ):
            await connection.execute("INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,data_kind) VALUES($1,$2,$3,$3,$4,$5)", cid, workspace, str(cid), person, kind)
        specs = {
            "new_source": ({"source_fields": {"商机创建时间": " 2026-03-31T23:59:00+08:00 ", "unrelated": "DO_NOT_EXPORT"}, "raw_fields": {"商机创建时间": "2025-12-31"}}, " 2026-03-31T23:59:00+08:00 ", "history_source_fields"),
            "legacy": ({"raw_fields": {"商机创建时间": "2025/12/31"}}, "2025/12/31", "history_legacy_raw_fields"),
            "empty_source": ({"source_fields": {"商机创建时间": " \t"}, "raw_fields": {"商机创建时间": "2026-02-02"}}, "2026-02-02", "history_legacy_raw_fields"),
            "missing": ({}, None, "historical_unknown"),
            "explicit_null": ({"source_fields": {"商机创建时间": None}, "raw_fields": {"商机创建时间": ""}}, None, "historical_unknown"),
            "invalid": ({"source_fields": {"商机创建时间": "not yet confirmed"}, "raw_fields": {"商机创建时间": "2026-01-01"}}, "not yet confirmed", "history_source_fields"),
            "structured": ({"source_fields": {"商机创建时间": {"unparsed": "original cell"}}}, {"unparsed": "original cell"}, "history_source_fields"),
            "native": (None, None, "system_entry"),
        }
        await context(connection, ws, operator, "administrator")
        await connection.execute("SELECT set_config('app.feishu_historical_import','on',false)")
        ids = {}
        for label, (extra, _, _) in specs.items():
            meta = {"import_type": "crm_history", **extra} if extra is not None else {"source_fields": {"商机创建时间": "2001-01-01"}}
            ids[label] = await connection.fetchval("INSERT INTO crm.opportunity(workspace_id,customer_id,name,created_by_user_ref_id,created_at,import_meta) VALUES($1,$2,$3,$4,'2026-09-22T12:34:56+08:00',$5) RETURNING id", ws, customer, label, operator, meta)
        excluded = await connection.fetchval("INSERT INTO crm.opportunity(workspace_id,customer_id,name,created_by_user_ref_id,import_meta) VALUES($1,$2,'excluded opportunity',$3,$4) RETURNING id", ws, excluded_customer, operator, {"import_type": "crm_history", "source_fields": {"商机创建时间": "PRIVATE_DATE"}})
        deleted = await connection.fetchval("INSERT INTO crm.opportunity(workspace_id,customer_id,name,created_by_user_ref_id,deleted_at,import_meta) VALUES($1,$2,'deleted opportunity',$3,clock_timestamp(),$4) RETURNING id", ws, customer, operator, {"import_type": "crm_history", "source_fields": {"商机创建时间": "PRIVATE_DATE"}})
        await context(connection, other_ws, outsider, "administrator")
        foreign = await connection.fetchval("INSERT INTO crm.opportunity(workspace_id,customer_id,name,created_by_user_ref_id,import_meta) VALUES($1,$2,'other workspace opportunity',$3,$4) RETURNING id", other_ws, foreign_customer, outsider, {"import_type": "crm_history", "source_fields": {"商机创建时间": "OTHER_WORKSPACE_DATE"}})
        await connection.execute("SELECT set_config('app.feishu_historical_import','off',false)")
        for cid, workspace, person in ((sync, ws, operator), (foreign_sync, other_ws, outsider)):
            await context(connection, workspace, person, "administrator")
            await connection.execute("INSERT INTO config.feishu_connection(id,workspace_id,revision,settings,updated_by) VALUES($1,$2,1,$3,$4)", cid, workspace, {"workspace_id": str(workspace), "connection_id": str(cid)}, person)
        async def source(kind, oid, connection_id=sync):
            return await connection.fetchval("SELECT ops.feishu_source($1,$2,$3)", connection_id, kind, oid)
        before_sources = {label: await source("opportunity", oid) for label, oid in ids.items()}
        before_customer = await source("customer", customer)
        before_deleted = await source("opportunity", deleted)
        before_rows = await connection.fetchval("SELECT jsonb_agg(to_jsonb(o) ORDER BY id) FROM crm.opportunity o")
        before_config = await connection.fetchval("SELECT jsonb_agg(to_jsonb(c) ORDER BY id) FROM config.feishu_connection c")
        before_events = await connection.fetchval("SELECT count(*) FROM ops.feishu_event")
        before_acl = await connection.fetchrow("SELECT oid,proowner,proacl::text FROM pg_proc WHERE oid='ops.feishu_source(uuid,text,uuid)'::regprocedure")
        await migrate(connection)
        after_acl = await connection.fetchrow("SELECT oid,proowner,proacl::text FROM pg_proc WHERE oid='ops.feishu_source(uuid,text,uuid)'::regprocedure")
        assert dict(after_acl) == dict(before_acl)
        assert await connection.fetchval("SELECT jsonb_agg(to_jsonb(o) ORDER BY id) FROM crm.opportunity o") == before_rows
        assert await connection.fetchval("SELECT jsonb_agg(to_jsonb(c) ORDER BY id) FROM config.feishu_connection c") == before_config
        assert await connection.fetchval("SELECT count(*) FROM ops.feishu_event") == before_events
        checks.append("upgrade_preserves_source_oid_acl_all_business_fields_config_and_queue")
        await connection.execute(f'CREATE ROLE "{worker}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        created_roles.append(worker)
        await connection.execute(f'GRANT salegent_feishu_worker TO "{worker}"')
        await connection.execute(f'SET ROLE "{worker}"')
        await verify_worker_role(connection)
        assert not await connection.fetchval("SELECT has_table_privilege(current_user,'crm.opportunity','SELECT')")
        checks.append("dedicated_worker_passes_strict_definer_whitelist_without_business_read_grants")
        for label, (_, expected, provenance) in specs.items():
            value = await source("opportunity", ids[label])
            assert {k: v for k, v in value.items() if k not in {"original_created_at_raw", "original_created_at_source"}} == before_sources[label]
            assert value["original_created_at_source"] == provenance, (label, value["original_created_at_source"], provenance)
            if label == "native":
                assert value["original_created_at_raw"] == value["created_at"]
            elif label == "structured":
                assert json.loads(value["original_created_at_raw"]) == expected
            else:
                assert value["original_created_at_raw"] == expected
            assert "DO_NOT_EXPORT" not in json.dumps(value)
            checks.append("source_" + label)
        assert await source("customer", customer) == before_customer
        assert await source("opportunity", deleted) == before_deleted
        assert await source("opportunity", excluded) == {"id": str(excluded), "excluded": True}
        assert await source("opportunity", foreign) == {"id": str(foreign), "deleted": True}
        assert (await source("opportunity", foreign, foreign_sync))["original_created_at_raw"] == "OTHER_WORKSPACE_DATE"
        assert "original_created_at_raw" not in await source("opportunity", uuid.uuid4())
        checks.append("non_opportunity_and_deleted_excluded_cross_tenant_missing_scopes_remain_unchanged")
        try:
            await source("opportunity", ids["native"], uuid.uuid4())
        except asyncpg.InsufficientPrivilegeError:
            pass
        else:
            raise AssertionError("unknown connection was accepted")
        await connection.execute("RESET ROLE")
        await connection.execute(f'CREATE ROLE "{application}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        created_roles.append(application)
        await connection.execute(f'GRANT USAGE ON SCHEMA ops TO "{application}"')
        await connection.execute(f'SET ROLE "{application}"')
        try:
            await source("opportunity", ids["native"])
        except asyncpg.InsufficientPrivilegeError:
            pass
        else:
            raise AssertionError("PUBLIC/application caller gained source access")
        checks.append("unknown_connection_and_ungranted_application_role_rejected")
        await connection.execute("RESET ROLE")
        assert all(item["status"] == "unchanged" for item in await migrate(connection))
        assert await connection.fetchval("SELECT count(*) FROM ops.feishu_event") == before_events
        checks.append("migration_replay_and_source_reads_do_not_emit_backfill_or_notifications")
        print(json.dumps({"passed": len(checks), "checks": checks}, ensure_ascii=False))
    finally:
        if connection:
            await connection.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        for role in reversed(created_roles):
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())
