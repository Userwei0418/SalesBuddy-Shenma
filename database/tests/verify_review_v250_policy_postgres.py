"""Opt-in V250 exact-policy proof on a disposable Unix-socket PostgreSQL 16.

Creates only randomized databases/roles and removes them in finally. Never reads
saved credentials or connects to production/LOCAL. No schema-wide GRANTs.
"""
import argparse
import asyncio
import io
import json
import sys
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / p) for p in ("database/scripts", "tools/installation", "backend/src")]
from migrate import migrate
from runtime_roles import fingerprint, load_policy, provision_new, verify
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from upgrade_permissions import reconcile
from verify_runtime_grants_postgres import migrate_through

TABLES = ("crm.sales_target", "crm.review_metric", "crm.review_objective",
          "crm.review_key_result", "crm.review_key_result_progress")
HELPER = "security.work_report_review_access(jsonb)"


async def rejected(c, statement, *args):
    try:
        async with c.transaction():
            await c.execute(statement, *args)
    except asyncpg.InsufficientPrivilegeError as exc:
        return str(exc)
    raise AssertionError("Expected insufficient_privilege")


async def scenario(admin, config, mode, result):
    suffix = uuid4().hex[:12]
    db = "sb_v250_acl_" + suffix
    roles = {s: "sb_v250_" + s + "_" + suffix for s in ("api", "worker", "platform", "feishu")}
    c = None
    row = {"mode": mode, "checks": []}
    result["scenarios"].append(row)
    try:
        await admin.execute(f"""CREATE DATABASE "{db}" TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'""")
        c = await asyncpg.connect(database=db, **config)
        policy = load_policy()
        # Frozen policy is only the historical upgrade baseline, never the output.
        old = json.loads((ROOT / "backend/tests/fixtures/review_release_contract.frozen.json").read_text())["policies"]["V244"]
        with redirect_stdout(io.StringIO()):
            await migrate_through(c, 244 if mode == "upgrade" else 250)
        if mode == "upgrade":
            await provision_new(c, database=db, roles=roles, policy=old)
            with redirect_stdout(io.StringIO()):
                await migrate(c)
            row["post_migration_policy"] = await verify(c, roles, policy)
            row["checks"].append("existing_V244_roles_receive_exact_V245_V246_grants")
        else:
            broken = {**deepcopy(policy), "services": deepcopy(old["services"])}
            await provision_new(c, database=db, roles=roles, policy=broken)
            await c.execute(f'SET ROLE "{roles["api"]}"')
            row["original_denial"] = await rejected(c, "SELECT security.work_report_review_access('{}'::jsonb)")
            await c.execute("RESET ROLE")
            row["checks"].append("late_V244_policy_reproduces_missing_review_helper")
        before = dict(await c.fetch("SELECT migration_key,checksum_sha256 FROM ops.migration_checksum"))
        async with c.transaction():
            row["reconciled_policy"] = await reconcile(c, roles, policy)
        async with c.transaction():
            await reconcile(c, roles, policy)
        after = dict(await c.fetch("SELECT migration_key,checksum_sha256 FROM ops.migration_checksum"))
        assert before == after
        assert fingerprint(after) == policy["migration_fingerprint"]
        row.update(migration_count=len(after), migration_fingerprint=fingerprint(after))
        row["checks"].append("exact_reconcile_idempotent_and_ledger_unchanged")
        # The baseline pg_dump sets row_security=off on its maintenance session.
        await c.execute("SET row_security=on")

        ws, uid, other_ws, other_uid = [uuid4() for _ in range(4)]
        for workspace, user in ((ws, uid), (other_ws, other_uid)):
            await c.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'ACL synthetic')", workspace, str(workspace))
            await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", workspace)
            await c.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$3,'ACL synthetic')", user, workspace, str(user))
            await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,'operations','workspace')", workspace, user)
            permission_role = await c.fetchval("SELECT id FROM config.permission_role WHERE workspace_id=$1 AND builtin_role_code='operations'", workspace)
            for code in ("work_report.read", "work_report.write", "agent.work_report", "target.read"):
                await c.execute("""INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code)
                  VALUES($1,$2,$3,'workspace') ON CONFLICT(role_id,permission_code) DO UPDATE SET scope_code='workspace'""",
                                workspace, permission_role, code)
        actor = ActorContext(workspace_id=str(ws), user_id=str(uid), role="operations", data_scope="workspace", team_ids=[])
        insert = """INSERT INTO agent.work_report_snapshot
          (workspace_id,owner_user_ref_id,selection,period,scope)
          VALUES($1,$2,'{"sources":[]}','{}','{}') RETURNING id"""
        for service in ("api", "worker"):
            await c.execute(f'SET ROLE "{roles[service]}"')
            async with c.transaction():
                await set_request_context(c, actor)
                assert not await c.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                dep = {"source_type": "review_subject", "scope_type": "person", "record_id": str(uid), "required_permissions": ["work_report.read"]}
                assert await c.fetchval("SELECT security.work_report_review_access($1::jsonb)", json.dumps(dep))
                dep["record_id"] = str(other_uid)
                assert not await c.fetchval("SELECT security.work_report_review_access($1::jsonb)", json.dumps(dep))
                assert not await c.fetchval("SELECT security.work_report_review_access('{}'::jsonb)")
                if service == "api":
                    sid = await c.fetchval(insert, ws, uid)
                    assert await c.fetchval("SELECT id FROM agent.work_report_snapshot WHERE id=$1", sid) == sid
                    await rejected(c, insert, other_ws, other_uid)
                else:
                    for table in TABLES:
                        await c.fetch(f"SELECT * FROM {table} LIMIT 1")
                        await rejected(c, f"DELETE FROM {table} WHERE false")
                    for table in ("crm.sales_target_batch_request", "crm.sales_target_change_request"):
                        await rejected(c, f"SELECT * FROM {table} LIMIT 0")
                for sql in ("SELECT * FROM platform.password_credential LIMIT 0",
                            "SELECT * FROM ops.migration_checksum LIMIT 0",
                            "SELECT security.reconcile_runtime_grants()"):
                    await rejected(c, sql)
            await c.execute("RESET ROLE")
            row["checks"].append(service + "_real_role_positive_and_cross_tenant_write_credential_denials")
        for service in ("platform", "feishu"):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", roles[service], HELPER)
        row["checks"].append("platform_feishu_receive_no_report_helper")
        row["final_policy"] = await verify(c, roles, policy)
        row["status"] = "passed"
    finally:
        if c:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        for role in roles.values():
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        row["cleaned"] = not await admin.fetchval("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1) OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname=ANY($2::text[]))", db, list(roles.values()))


async def main(args):
    host = str(Path(args.socket).resolve())
    if not host.startswith(("/tmp/sb-review-pg.", "/private/tmp/sb-review-pg.")):
        raise ValueError("Only an explicit disposable sb-review-pg Unix socket is allowed")
    config = {"host": host, "port": args.port, "user": "postgres", "password": "", "passfile": "/dev/null", "ssl": False}
    admin = await asyncpg.connect(database="postgres", **config)
    result = {"status": "running", "scenarios": [], "production_or_LOCAL_accessed": False}
    try:
        for mode in ("late_install", "upgrade"):
            await scenario(admin, config, mode, result)
        result["status"] = "passed"
    except Exception as exc:
        result.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        raise
    finally:
        await admin.close()
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", required=True)
    asyncio.run(main(parser.parse_args()))
