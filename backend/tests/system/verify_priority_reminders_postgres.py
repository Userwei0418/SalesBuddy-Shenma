"""V191 reminder idempotence on a private, disposable PostgreSQL 16 cluster.

The existing cluster harness uses a fresh Unix socket and no TCP listener.
Only synthetic records are created; no business instance or notifier is used.
"""
from __future__ import annotations

import asyncio
import getpass
import json
from pathlib import Path
from uuid import uuid4

import asyncpg
import verify_legacy_adoption_postgres as harness

ROOT = Path(__file__).resolve().parents[3]
FUNCTION = "ops.run_priority_reminder_monitor()"
WORKER = "priority_reminder_test_worker"
DEFINER = "priority_reminder_test_owner"
MIGRATION = ROOT / "database/migrations/V191__priority_reminder_idempotency.sql"


async def company(conn):
    workspace, person, customer = uuid4(), uuid4(), uuid4()
    await conn.execute(
        "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'隔离提醒验收')",
        workspace, str(workspace),
    )
    await conn.execute(
        "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) "
        "VALUES($1,$2,$3,'合成收件人')", person, workspace, str(person),
    )
    await conn.execute(
        "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id) "
        "VALUES($1,$2,'合成客户','合成客户',$3)", customer, workspace, person,
    )
    return workspace, person, customer


async def source(conn, kind, tenant, *, archived=False):
    workspace, person, customer = tenant
    if kind == "risk":
        identifier = await conn.fetchval(
            "INSERT INTO insight.risk(workspace_id,customer_id,risk_type_code,title,description,"
            "severity_code,owner_user_ref_id,deleted_at) VALUES($1,$2,'budget_risk',"
            "'合成风险','合成描述','high',$3,CASE WHEN $4 THEN clock_timestamp() END) RETURNING id",
            workspace, customer, person, archived,
        )
    else:
        identifier = await conn.fetchval(
            "INSERT INTO workflow.task(workspace_id,title,description,creator_user_ref_id,"
            "due_at,association_kind,deleted_at) VALUES($1,'合成任务','合成描述',$2,"
            "clock_timestamp()+interval '6 hours','daily',"
            "CASE WHEN $3 THEN clock_timestamp() END) RETURNING id", workspace, person, archived,
        )
        await conn.execute(
            "INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_role) "
            "VALUES($1,$2,$3,'sales')", identifier, workspace, person,
        )
    return identifier


async def notice(conn, kind, tenant, object_id, status):
    return await conn.fetchval(
        "INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,template_code,title,"
        "body,object_type,object_id,status,dedupe_key,payload,sent_at,read_at,failure_reason) "
        "VALUES($1,$2,$3,'保留历史标题','保留历史正文',$4,$5,$6,$7,"
        "'{\"preserve_history\":true}',clock_timestamp()-interval '1 day',"
        "clock_timestamp()-interval '1 hour','保留历史原因') RETURNING id",
        tenant[0], tenant[1], "priority_risk" if kind == "risk" else "priority_task_due",
        kind, object_id, status, f"priority_monitor:{kind}:{object_id}",
    )


async def invoke(conn):
    async with conn.transaction():
        await conn.execute(f"SET LOCAL ROLE {WORKER}")
        assert not await conn.fetchval(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        return await conn.fetchval(f"SELECT {FUNCTION}")


async def legacy_rejection(conn, baseline, expected_key):
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute(baseline)
        try:
            await invoke(conn)
        except asyncpg.UniqueViolationError as error:
            assert error.constraint_name == "uq_notification_dedupe_present"
            assert expected_key in error.detail
        else:
            raise AssertionError("Legacy monitor did not reproduce the dedupe failure")
    finally:
        await transaction.rollback()


async def concurrent_first_insert(config, owner, tenant, kind):
    object_id = await source(owner, kind, tenant)
    first, second = await asyncpg.connect(**config), await asyncpg.connect(**config)
    transaction = first.transaction()
    pending = None
    await transaction.start()
    try:
        assert await invoke(first) == 1
        # Keep the first insert uncommitted. The second monitor's snapshot cannot
        # see it, so this deterministically exercises the unique-index arbiter.
        pending = asyncio.create_task(invoke(second))
        for _ in range(200):
            if await owner.fetchval(
                "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1",
                second.get_server_pid(),
            ):
                break
            assert not pending.done(), "Concurrent insert did not wait for the occupied key"
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("Concurrent insert did not reach a PostgreSQL lock wait")
        await transaction.commit()
        assert await asyncio.wait_for(pending, 5) == 0
        assert await owner.fetchval(
            "SELECT count(*) FROM workflow.notification WHERE object_id=$1", object_id,
        ) == 1
    finally:
        if first.is_in_transaction():
            await transaction.rollback()
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await first.close()
        await second.close()


async def exercise(_root, host, _pg_bin):
    config = {"host": host, "user": getpass.getuser(), "port": 5432}
    admin = await asyncpg.connect(database="postgres", **config)
    await admin.execute("CREATE DATABASE priority_reminder_fixture TEMPLATE template0")
    await admin.close()
    config["database"] = "priority_reminder_fixture"
    conn = await asyncpg.connect(**config)
    try:
        migrated = await harness.fixture.migrate.migrate(conn, ROOT / "database")
        assert {"key": "V191", "status": "applied"} in migrated
        await conn.execute(f"CREATE ROLE {WORKER} NOLOGIN NOSUPERUSER NOBYPASSRLS")
        await conn.execute(f"CREATE ROLE {DEFINER} NOLOGIN NOSUPERUSER BYPASSRLS")
        await conn.execute(f"GRANT USAGE ON SCHEMA ops TO {WORKER}")
        await conn.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO {WORKER}")
        await conn.execute(f"GRANT USAGE ON SCHEMA ops,workflow,insight,crm TO {DEFINER}")
        await conn.execute(
            f"GRANT SELECT ON workflow.task,workflow.task_assignee,insight.risk,crm.customer TO {DEFINER}"
        )
        await conn.execute(f"GRANT SELECT,INSERT,UPDATE ON workflow.notification TO {DEFINER}")
        await conn.execute(f"ALTER FUNCTION {FUNCTION} OWNER TO {DEFINER}")
        assert not await conn.fetchval(
            "SELECT has_table_privilege($1,'workflow.notification','INSERT')", WORKER,
        )
        metadata_sql = (
            "SELECT proowner,proacl,prosecdef,proconfig FROM pg_proc WHERE oid=$1::regprocedure"
        )
        before = await conn.fetchrow(metadata_sql, FUNCTION)
        index_before = await conn.fetchval(
            "SELECT pg_get_indexdef('workflow.uq_notification_dedupe_present'::regclass)"
        )
        await conn.execute(MIGRATION.read_text())
        assert await conn.fetchrow(metadata_sql, FUNCTION) == before
        assert await conn.fetchval(
            "SELECT pg_get_indexdef('workflow.uq_notification_dedupe_present'::regclass)"
        ) == index_before

        baseline = (ROOT / "database/baseline/V001__current_schema.sql").read_text()
        start = baseline.index(f"CREATE FUNCTION {FUNCTION}")
        baseline = baseline[start:baseline.index("\n$$;", start) + 4]
        baseline = baseline.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
        first, other = await company(conn), await company(conn)
        cancelled_risk = await source(conn, "risk", first)
        await notice(conn, "risk", first, cancelled_risk, "cancelled")
        new_risk = await source(conn, "risk", first)
        await legacy_rejection(conn, baseline, f"priority_monitor:risk:{cancelled_risk}")
        assert not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM workflow.notification WHERE object_id=$1)", new_risk,
        )
        cancelled_task = await source(conn, "task", first)
        await notice(conn, "task", first, cancelled_task, "cancelled")
        await legacy_rejection(conn, baseline, f"priority_monitor:task:{cancelled_task}")

        for kind in ("risk", "task"):
            for status in ("read", "pending", "sent", "delivered", "failed"):
                item = await source(conn, kind, first)
                await notice(conn, kind, first, item, status)
            archived = await source(conn, kind, first, archived=True)
            await notice(conn, kind, first, archived, "cancelled")
            await source(conn, kind, first, archived=True)  # No archived source is newly notified.
        history = await conn.fetch("SELECT * FROM workflow.notification ORDER BY id")
        new_task = await source(conn, "task", first)
        other_risk = await source(conn, "risk", other)
        assert await invoke(conn) == 3  # Occupied keys do not roll back these valid new reminders.
        assert await invoke(conn) == 0
        assert await conn.fetch(
            "SELECT * FROM workflow.notification WHERE id=ANY($1::uuid[]) ORDER BY id",
            [row["id"] for row in history],
        ) == history
        assert {row["object_id"] for row in await conn.fetch(
            "SELECT object_id FROM workflow.notification WHERE id<>ALL($1::uuid[])",
            [row["id"] for row in history],
        )} == {new_risk, new_task, other_risk}
        assert await conn.fetchval(
            "SELECT workspace_id=$1 AND recipient_user_ref_id=$2 FROM workflow.notification WHERE object_id=$3",
            other[0], other[1], other_risk,
        )

        # The original eligibility/cancellation lifecycle is retained. Cancellation
        # leaves its key and history occupied even if a source becomes eligible again.
        await conn.execute("UPDATE insight.risk SET status='resolved' WHERE id=$1", new_risk)
        await conn.execute("UPDATE workflow.task SET deleted_at=clock_timestamp() WHERE id=$1", new_task)
        assert await invoke(conn) == 0
        cancelled = await conn.fetch(
            "SELECT * FROM workflow.notification WHERE object_id=ANY($1::uuid[]) ORDER BY id",
            [new_risk, new_task],
        )
        assert len(cancelled) == 2 and all(row["status"] == "cancelled" for row in cancelled)
        await conn.execute("UPDATE insight.risk SET status='pending' WHERE id=$1", new_risk)
        await conn.execute("UPDATE workflow.task SET deleted_at=NULL WHERE id=$1", new_task)
        assert await invoke(conn) == 0
        assert await conn.fetch(
            "SELECT * FROM workflow.notification WHERE object_id=ANY($1::uuid[]) ORDER BY id",
            [new_risk, new_task],
        ) == cancelled

        for kind in ("task", "risk"):
            await concurrent_first_insert(config, conn, first, kind)
        # Only the named key conflict is suppressed. Other integrity failures must
        # still abort instead of looking like a successful maintenance cycle.
        transaction = conn.transaction()
        await transaction.start()
        try:
            await source(conn, "risk", first)
            await conn.execute(
                "ALTER TABLE workflow.notification ADD CONSTRAINT synthetic_check "
                "CHECK(title<>'高风险提醒') NOT VALID"
            )
            try:
                await invoke(conn)
            except asyncpg.CheckViolationError as error:
                assert error.constraint_name == "synthetic_check"
            else:
                raise AssertionError("Unrelated integrity failure was swallowed")
        finally:
            await transaction.rollback()
        assert await invoke(conn) == 0
        assert all(row["status"] == "unchanged" for row in await harness.fixture.migrate.migrate(
            conn, ROOT / "database",
        ))
        print(json.dumps({
            "status": "passed", "schema": harness.fixture.TARGET, "legacy_failures_reproduced": ["risk", "task"],
            "history_states_preserved": ["cancelled", "read", "pending", "sent", "delivered", "failed"],
            "new_reminders_despite_occupied_keys": 3, "second_scan_new_reminders": 0,
            "concurrent_task_and_risk_once": True, "archived_sources_not_revived": True,
            "unrelated_integrity_error_propagated": True, "function_owner_acl_index_preserved": True,
            "production_data": False,
        }, indent=2))
    finally:
        await conn.close()


if __name__ == "__main__":
    harness.exercise = exercise
    harness.main()
