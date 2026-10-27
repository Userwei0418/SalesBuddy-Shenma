"""Report task reads with the exact Worker policy in an isolated PG16 cluster.

PG_BIN=/path/to/pg16/bin python database/tests/verify_work_report_worker_columns_postgres.py
Only synthetic data and a private Unix socket; no saved DSN, model or service.
The cluster is stopped and removed even if a check fails.
"""
import asyncio
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / p) for p in ("database/scripts", "tools/installation", "backend/src")]
from migrate import migrate
from runtime_roles import fingerprint, load_policy, verify_service_roles
from upgrade_permissions import reconcile_service_roles
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.repositories.work_report_facts import source_query
from sales_backend.repositories.work_report_task_activity import TASK_ACTIVITY_SQL

PORT = 56426
ROLE = "report_test_worker"
TABLE = "workflow.task_execution"


async def denied(c, statement, *args):
    try:
        async with c.transaction():
            await c.fetch(statement, *args)
    except asyncpg.InsufficientPrivilegeError as error:
        assert error.sqlstate == "42501"
        return
    raise AssertionError("Expected a database privilege denial")


async def seed(c):
    """Two real tenant/user identities and two tasks per identity, no fixture bypass."""
    actors, task_ids = [], []
    for _ in range(2):
        workspace, user, team = uuid4(), uuid4(), uuid4()
        await c.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Report ACL synthetic')", workspace, str(workspace))
        await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", workspace)
        await c.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'report-test','Synthetic team')", team, workspace)
        await c.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$3,'Synthetic salesperson')", user, workspace, str(user))
        await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'sales','self',$3)", workspace, user, team)
        await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')", workspace, user, team)
        permission_role = await c.fetchval("SELECT id FROM config.permission_role WHERE workspace_id=$1 AND builtin_role_code='sales'", workspace)
        for code in ("work_report.read", "work_report.write", "task.read"):
            await c.execute("""INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code)
                VALUES($1,$2,$3,'self') ON CONFLICT(role_id,permission_code) DO UPDATE SET scope_code='self'""", workspace, permission_role, code)
        ids = []
        for status in ("completed", "in_progress"):
            task = uuid4()
            ids.append(task)
            await c.execute("""INSERT INTO workflow.task(id,workspace_id,creator_user_ref_id,title,description,
                association_kind,status,execution_mode,due_at,completed_at,completion_note)
                VALUES($1,$2,$3,'Synthetic report task','Synthetic description','daily',$4,'execution_v3',clock_timestamp()+interval '1 day',
                  CASE WHEN $4='completed' THEN clock_timestamp() END,'Synthetic completion')""", task, workspace, user, status)
            await c.execute("""INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_role,responsibility)
                VALUES($1,$2,$3,'sales','owner')""", task, workspace, user)
            await c.execute("""INSERT INTO workflow.task_execution(task_id,workspace_id,state) VALUES($1,$2,$3)""", task, workspace,
                json.dumps({"protocol_version": 3, "latest_feedback": {"text": "Synthetic feedback"},
                    "next_step": "Synthetic next step", "completion_result": {"action": "submit_completion", "note": "Synthetic result"} if status == "completed" else None}))
        actors.append(ActorContext(workspace_id=str(workspace), user_id=str(user), role="sales", data_scope="self", team_ids=(str(team),)))
        task_ids.append(set(ids))
    return actors, task_ids


def queries(actor, now):
    args = ("self", [actor.user_id], None)
    return (
        (source_query("task"), args),
        (source_query("task", sales_week=True), args),
        (TASK_ACTIVITY_SQL, (*args, now - timedelta(days=7), now + timedelta(seconds=1), now)),
    )


async def verify(socket):
    config = dict(host=str(socket), port=PORT, database="postgres", password="", passfile="/dev/null", ssl=False)
    owner = await asyncpg.connect(user="report_test_owner", command_timeout=120, **config)
    worker = None
    try:
        for role in ("salegent_feishu_worker", "salegent_api", "salegent_worker", "salegent_platform"):
            await owner.execute(f"CREATE ROLE {role} NOLOGIN")
        with redirect_stdout(io.StringIO()):
            await migrate(owner, ROOT / "database", adopt_existing=True)
        policy = load_policy()
        assert policy["services"]["worker"]["columns"][TABLE] == {"SELECT": ["task_id", "workspace_id", "state"]}
        assert TABLE not in policy["services"]["worker"]["tables"]
        before_ledger = dict(await owner.fetch("SELECT migration_key,checksum_sha256 FROM ops.migration_checksum"))
        assert fingerprint(before_ledger) == policy["migration_fingerprint"]
        await owner.execute(f"CREATE ROLE {ROLE} LOGIN NOSUPERUSER NOBYPASSRLS")
        previous = deepcopy(policy)
        del previous["services"]["worker"]["columns"][TABLE]
        async with owner.transaction():
            await reconcile_service_roles(owner, {"worker": ROLE}, previous)
        # Task and execution projection must commit together, as required by
        # the existing deferred task-initialization constraint.
        async with owner.transaction():
            actors, task_ids = await seed(owner)
        worker = await asyncpg.connect(user=ROLE, command_timeout=30, **config)
        await worker.execute("SET row_security=on")
        assert dict(await worker.fetchrow("SELECT current_user,session_user")) == dict(current_user=ROLE, session_user=ROLE)
        assert not await worker.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
        now = datetime.now(UTC)
        async with worker.transaction(readonly=True, isolation="repeatable_read"):
            await set_request_context(worker, actors[0])
            for sql, args in queries(actors[0], now):
                await denied(worker, sql, *args)
        checks = ["old_worker_policy_reproduces_42501_for_daily_weekly_and_activity_queries"]
        async with owner.transaction():
            await reconcile_service_roles(owner, {"worker": ROLE}, policy)
        checks.append("official_policy_reconciliation_and_exact_effective_acl_pass")

        for actor, expected in zip(actors, task_ids, strict=True):
            async with worker.transaction(readonly=True, isolation="repeatable_read"):
                await set_request_context(worker, actor)
                assert await worker.fetchval("SELECT security.authorization_has('task.read')"), "seed task.read missing"
                assert await worker.fetchval("SELECT security.authorization_has('work_report.read')"), "seed work_report.read missing"
                assert await worker.fetchval("SELECT security.authorization_has('work_report.write')"), "seed work_report.write missing"
                for index, (sql, args) in enumerate(queries(actor, now)):
                    rows = await worker.fetch(sql, *args)
                    assert {str(row["id"]) for row in rows} == {str(i) for i in expected}, (index, len(rows), len(expected))
                    assert all(json.loads(row["execution"])["latest_feedback"]["text"] == "Synthetic feedback" for row in rows)
                rows = await worker.fetch(f"SELECT task_id,workspace_id,state FROM {TABLE}")
                assert {row["task_id"] for row in rows} == expected
                assert {str(row["workspace_id"]) for row in rows} == {actor.workspace_id}
        checks.append("real_daily_weekly_activity_reads_keep_material_and_cross_tenant_rls")

        # Removing only the business task permission must still suppress every
        # material path; the new physical column grant is not a business grant.
        await owner.execute("INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1::uuid,$2::uuid)", actors[0].workspace_id, actors[0].user_id)
        await owner.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1::uuid,$2::uuid,'task.read','deny')", actors[0].workspace_id, actors[0].user_id)
        async with worker.transaction(readonly=True, isolation="repeatable_read"):
            await set_request_context(worker, actors[0])
            assert await worker.fetchval("SELECT security.authorization_has('work_report.read')")
            assert not await worker.fetchval("SELECT security.authorization_has('task.read')")
            assert not await worker.fetch(f"SELECT task_id,workspace_id,state FROM {TABLE}")
            for sql, args in queries(actors[0], now):
                assert not await worker.fetch(sql, *args)
        checks.append("task_read_deny_still_blocks_direct_rows_and_all_report_material")

        # Use a writable transaction so a read-only transaction cannot mask a
        # missing ACL denial. Statements cannot mutate data even if misgranted.
        async with worker.transaction():
            await set_request_context(worker, actors[1])
            for sql in (
                f"SELECT updated_at FROM {TABLE} LIMIT 0", f"SELECT * FROM {TABLE} LIMIT 0",
                f"UPDATE {TABLE} SET state='{{}}' WHERE false RETURNING task_id",
                f"INSERT INTO {TABLE}(task_id,workspace_id,state) SELECT NULL,NULL,'{{}}'::jsonb WHERE false RETURNING task_id",
                f"DELETE FROM {TABLE} WHERE false RETURNING task_id",
            ):
                await denied(worker, sql)
        assert not await owner.fetchval("SELECT has_table_privilege($1,$2,'SELECT')", ROLE, TABLE)
        assert not await owner.fetchval("SELECT has_function_privilege($1,'workflow.task_execution_summary(uuid)','EXECUTE')", ROLE)
        assert before_ledger == dict(await owner.fetch("SELECT migration_key,checksum_sha256 FROM ops.migration_checksum"))
        await verify_service_roles(owner, {"worker": ROLE}, policy)
        checks.append("no_extra_column_whole_table_write_or_summary_execute_and_ledger_rls_unchanged")
        print(json.dumps({"status": "passed", "checks": checks, "schema": policy["schema"],
            "migration_fingerprint": policy["migration_fingerprint"], "rls_fingerprint": policy["rls_fingerprint"],
            "shared_or_production_database_accessed": False, "external_model_called": False}, indent=2), flush=True)
    finally:
        if worker:
            await worker.close()
        await owner.close()


def run():
    binary = Path(os.environ["PG_BIN"])
    # A short /tmp Unix socket is required on macOS; never accept a caller DSN.
    with tempfile.TemporaryDirectory(prefix="sb-report-worker-", dir="/tmp") as temporary:
        root = Path(temporary)
        data, socket = root / "pg", root / "socket"
        socket.mkdir()
        subprocess.run([str(binary / "initdb"), "-D", str(data), "-U", "report_test_owner", "--auth=trust", "--encoding=UTF8", "--no-locale"], check=True, capture_output=True)
        try:
            subprocess.run([str(binary / "pg_ctl"), "-D", str(data), "-l", str(root / "postgres.log"), "-o", f"-k {socket} -p {PORT} -c listen_addresses=''", "-w", "start"], check=True, capture_output=True)
            asyncio.run(verify(socket))
        finally:
            if (data / "postmaster.pid").exists():
                subprocess.run([str(binary / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"], check=True, capture_output=True)
        print("Temporary report Worker PostgreSQL cluster stopped and removed", flush=True)


if __name__ == "__main__":
    run()
