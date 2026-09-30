# ruff: noqa: S608 -- Identifiers are trusted catalog names or validated disposable role/database names.
"""V158 -> V159 keeps existing business rows and definer ACLs byte-for-byte."""

import os
import re
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "database/scripts"))
from migrate import migrate  # noqa: E402 -- Repository migration runner loaded from its explicit path.


async def verify():
    dsn = os.environ["SALES_TEST_DATABASE_URL"]
    runtime = os.environ["SALES_TEST_ROLE"]
    if not re.fullmatch(r"salegent_verify_role_[a-f0-9]+", runtime):
        raise RuntimeError("Disposable integration role required")
    check = await asyncpg.connect(dsn)
    try:
        if not re.fullmatch(
            r"salegent_verify_integration_[a-f0-9]+", await check.fetchval("SELECT current_database()")
        ):
            raise RuntimeError("Disposable integration database required")
    finally:
        await check.close()
    admin = await asyncpg.connect(dsn, database="postgres")
    name = "salegent_verify_integration_" + uuid4().hex[:12]
    conn = locker = None
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        conn = await asyncpg.connect(dsn, database=name)
        with tempfile.TemporaryDirectory(prefix="shenma-v159-upgrade-") as directory:
            stage = Path(directory)
            for kind in ("baseline", "seeds"):
                (stage / kind).symlink_to(ROOT / "database" / kind, target_is_directory=True)
            (stage / "migrations").mkdir()
            for source in [*(ROOT / "database").glob("V*.sql"), *(ROOT / "database/migrations").glob("V*.sql")]:
                version = int(re.match(r"V(\d+)", source.name).group(1))
                if version <= 158:
                    (stage / ("migrations" if source.parent.name == "migrations" else "") / source.name).symlink_to(
                        source
                    )
            await migrate(conn, root=stage)
            workspace, uid = uuid4(), uuid4()
            await conn.execute(
                "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'升级保护公司')",
                workspace,
                str(workspace),
            )
            await conn.execute(
                "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) "
                "VALUES($1,$2,$3,$3,'升级保护账号')",
                uid,
                workspace,
                str(uid),
            )
            for kind in ("production", "demo"):
                await conn.execute(
                    "INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id,data_kind) "
                    "VALUES($1,$2,$2,$3,$4)",
                    workspace,
                    kind,
                    uid,
                    kind,
                )
            await conn.execute("UPDATE security.deployment_account_quota SET max_active_accounts=65")
            await conn.execute(f'GRANT USAGE ON SCHEMA platform,security TO "{runtime}"')
            await conn.execute(f'GRANT SELECT ON platform.user_ref TO "{runtime}"')
            await conn.fetchval("SELECT security.reconcile_runtime_grants()")

            async def snapshot():
                result = {}
                tables = await conn.fetch("""SELECT n.nspname,c.relname FROM pg_class c
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE c.relkind='r' AND n.nspname IN
                    ('platform','crm','activity','workflow','insight','config','agent','ops','security')
                    AND EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='workspace_id')
                    ORDER BY 1,2""")
                for row in tables:
                    table = '"' + row["nspname"] + '"."' + row["relname"] + '"'
                    result[table] = await conn.fetchval(
                        "SELECT md5(COALESCE(string_agg(to_jsonb(t)::text,'|' ORDER BY to_jsonb(t)::text),'')) FROM "
                        + table
                        + " t"
                    )
                result["quota"] = await conn.fetchval(
                    "SELECT to_jsonb(q)::text FROM security.deployment_account_quota q"
                )
                result[
                    "acls"
                ] = await conn.fetchval("""SELECT jsonb_agg(
                    jsonb_build_object('oid',p.oid::text,'owner',p.proowner,'acl',p.proacl) ORDER BY p.oid)::text
                    FROM pg_proc p WHERE p.oid IN (
                    'security.weekly_source_count(timestamptz,timestamptz,timestamptz)'::regprocedure,
                    'security.publish_weekly_report_feishu(uuid,integer)'::regprocedure,'ops.feishu_weekly_source(uuid,uuid)'::regprocedure,
                    'ops.feishu_source(uuid,text,uuid)'::regprocedure)""")
                return result

            before = await snapshot()
            source = ROOT / "database/migrations/V159__synthetic_trial_boundaries.sql"
            target = stage / "migrations" / source.name
            target.write_bytes(source.read_bytes())
            locker = await asyncpg.connect(dsn, database=name)
            await locker.execute("BEGIN; LOCK TABLE crm.customer IN ACCESS EXCLUSIVE MODE")
            try:
                try:
                    await migrate(conn, root=stage)
                except asyncpg.LockNotAvailableError:
                    pass
                else:
                    raise AssertionError("Busy business table must abort migration promptly")
            finally:
                await locker.execute("ROLLBACK")
            assert await snapshot() == before
            assert await conn.fetchval(
                "SELECT to_regprocedure('security.synthetic_trial_marker(uuid,text,uuid)') IS NULL"
            )
            target.write_text(
                source.read_text().replace(
                    "INSERT INTO ops.schema_migration(version,description)",
                    "SELECT 1/0;\nINSERT INTO ops.schema_migration(version,description)",
                )
            )
            try:
                await migrate(conn, root=stage)
            except asyncpg.DivisionByZeroError:
                pass
            else:
                raise AssertionError("Injected late failure must rollback all DDL")
            assert await snapshot() == before
            target.write_bytes(source.read_bytes())
            assert (await migrate(conn, root=stage))[-1] == {"key": "V159", "status": "applied"}
            assert await snapshot() == before
            assert (await migrate(conn, root=stage))[-1] == {"key": "V159", "status": "unchanged"}
            assert await snapshot() == before
            assert await conn.fetchval(
                "SELECT has_function_privilege($1,'security.is_synthetic_trial_visit(uuid)','EXECUTE')", runtime
            )
            assert not await conn.fetchval(
                "SELECT has_function_privilege($1,'security.synthetic_trial_marker(uuid,text,uuid)','EXECUTE')", runtime
            )
            print(
                f"SYNTHETIC_TRIAL_UPGRADE_OK tables={len(before) - 2} rows_unchanged=true "
                "acl_unchanged=true busy_rollback=true late_rollback=true replay=true",
                flush=True,
            )
    finally:
        if locker:
            await locker.close()
        if conn:
            await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()
