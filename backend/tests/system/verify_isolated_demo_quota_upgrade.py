"""V157 -> V158 atomic upgrade rehearsal on a disposable DB only."""

import os
from pathlib import Path
import re
import sys
import tempfile
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "database/scripts"))
from migrate import migrate


async def verify():
    dsn = os.environ["SALES_TEST_DATABASE_URL"]
    runtime = os.environ["SALES_TEST_ROLE"]
    if not re.fullmatch(r"salegent_verify_role_[a-f0-9]+", runtime):
        raise RuntimeError("Disposable verification role required")
    current = await asyncpg.connect(dsn)
    try:
        if not re.fullmatch(r"salegent_verify_integration_[a-f0-9]+", await current.fetchval("SELECT current_database()")):
            raise RuntimeError("Disposable integration database required")
    finally:
        await current.close()
    admin = await asyncpg.connect(dsn, database="postgres")
    name = "salegent_verify_integration_" + uuid4().hex[:12]
    conn = locker = None
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        conn = await asyncpg.connect(dsn, database=name)
        with tempfile.TemporaryDirectory(prefix="shenma-demo-quota-upgrade-") as folder:
            stage = Path(folder)
            for directory in ("baseline", "seeds"):
                (stage / directory).symlink_to(ROOT / "database" / directory, target_is_directory=True)
            (stage / "migrations").mkdir()
            for source in [*(ROOT / "database").glob("V*.sql"), *(ROOT / "database/migrations").glob("V*.sql")]:
                version = int(re.match(r"V(\d+)", source.name).group(1))
                if version < 158:
                    destination = stage / ("migrations" if source.parent.name == "migrations" else "") / source.name
                    destination.symlink_to(source)
            await migrate(conn, root=stage)
            workspace, person = uuid4(), uuid4()
            await conn.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'正式升级保护样例')", workspace, str(workspace))
            await conn.execute("""INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name)
                VALUES($1,$2,$3,$3,'正式账号保护样例')""", person, workspace, str(person))
            await conn.execute("""INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code)
                VALUES($1,$2,'administrator','workspace')""", workspace, person)
            await conn.execute("UPDATE security.deployment_account_quota SET max_active_accounts=65")
            await conn.execute(f'GRANT USAGE ON SCHEMA platform,security TO "{runtime}"')
            await conn.execute(f'GRANT SELECT ON platform.user_ref TO "{runtime}"')
            await conn.execute(f'GRANT EXECUTE ON FUNCTION security.deployment_account_quota_status() TO "{runtime}"')
            await conn.fetchval("SELECT security.reconcile_runtime_grants()")

            async def snapshot():
                return await conn.fetchval("""SELECT jsonb_build_object(
                    'quota',(SELECT to_jsonb(q) FROM security.deployment_account_quota q),
                    'workspace',(SELECT to_jsonb(w) FROM platform.workspace w WHERE id=$1),
                    'users',(SELECT jsonb_agg(to_jsonb(u) ORDER BY id) FROM platform.user_ref u),
                    'bindings',(SELECT jsonb_agg(to_jsonb(b) ORDER BY id) FROM platform.role_binding b),
                    'function_acl',(SELECT jsonb_agg(jsonb_build_object('oid',p.oid::text,'owner',p.proowner,'acl',p.proacl) ORDER BY p.oid)
                      FROM pg_proc p WHERE p.oid IN('security.guard_deployment_account_quota()'::regprocedure,
                      'security.deployment_account_quota_status()'::regprocedure)))""", workspace)

            before = await snapshot()
            old_guard = await conn.fetchval("SELECT pg_get_functiondef('security.guard_deployment_account_quota()'::regprocedure)")
            source = ROOT / "database/migrations/V158__isolated_demo_account_quota.sql"
            v158 = stage / "migrations" / source.name
            v158.write_bytes(source.read_bytes())
            locker = await asyncpg.connect(dsn, database=name)
            await locker.execute("SELECT pg_advisory_lock(hashtextextended('deployment-account-quota',0))")
            try:
                try:
                    await migrate(conn, root=stage)
                except asyncpg.LockNotAvailableError:
                    pass
                else:
                    raise AssertionError("Upgrade should fail promptly while account lock is busy")
            finally:
                await locker.execute("SELECT pg_advisory_unlock(hashtextextended('deployment-account-quota',0))")
            assert await snapshot() == before
            assert await conn.fetchval("SELECT to_regclass('security.isolated_demo_account_quota') IS NULL")
            # A late failure must undo all DDL/functions/reconciliation, not just
            # an error before table creation. This modified file is local only.
            v158.write_text(source.read_text().replace("INSERT INTO ops.schema_migration(version,description)",
                                                     "SELECT 1/0;\nINSERT INTO ops.schema_migration(version,description)"))
            try:
                await migrate(conn, root=stage)
            except asyncpg.DivisionByZeroError:
                pass
            else:
                raise AssertionError("Injected late failure did not abort")
            assert await snapshot() == before
            assert await conn.fetchval("SELECT pg_get_functiondef('security.guard_deployment_account_quota()'::regprocedure)") == old_guard
            assert await conn.fetchval("SELECT to_regclass('security.isolated_demo_account_quota') IS NULL")
            v158.write_bytes(source.read_bytes())
            result = await migrate(conn, root=stage)
            assert result[-1] == {"key": "V158", "status": "applied"}
            assert await snapshot() == before
            assert await conn.fetchval("SELECT count(*) FROM security.isolated_demo_account_quota") == 0
            assert not await conn.fetchval("SELECT has_table_privilege($1,'security.isolated_demo_account_quota','SELECT,INSERT,UPDATE,DELETE')", runtime)
            assert not await conn.fetchval("SELECT has_function_privilege($1,'security.register_isolated_demo_quota(uuid,integer)','EXECUTE')", runtime)
            replay = await migrate(conn, root=stage)
            assert replay[-1] == {"key": "V158", "status": "unchanged"}
            assert await snapshot() == before
            print("ISOLATED_DEMO_UPGRADE_OK formal_limit=65 formal_rows_unchanged=true guard_status_acl_unchanged=true busy_rollback=true late_failure_rollback=true replay_unchanged=true", flush=True)
    finally:
        if locker:
            await locker.close()
        if conn:
            await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await admin.close()
