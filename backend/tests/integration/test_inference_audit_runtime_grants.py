"""Reproduce production's inherited app role without the fixture's blanket grants."""

from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.services.agent_platform.audit import InferenceAudit
from tests.integration.test_operations_api import TransactionDatabase
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio

FINISH = "SELECT security.finish_inference_operation($1::uuid,$2,$3::jsonb,$4::jsonb,$5)"


async def api_receipt_permissions(connection):
    """Exact API read boundary; undo the integration fixture's blanket grants."""
    role = await connection.fetchval("SELECT current_user")
    assert role.startswith("salegent_verify_role_")
    await connection.execute("RESET ROLE")
    await connection.execute(f'REVOKE ALL ON agent.inference_operation FROM "{role}"')
    await connection.execute(f'GRANT INSERT ON agent.inference_operation TO "{role}"')
    await connection.fetchval("SELECT security.reconcile_runtime_grants()")
    await connection.execute(f'SET LOCAL ROLE "{role}"')
    assert await connection.fetchval(
        "SELECT has_table_privilege(current_user,'agent.inference_operation','INSERT')"
    )
    for privilege in ("SELECT", "UPDATE", "DELETE"):
        assert not await connection.fetchval(
            "SELECT has_table_privilege(current_user,'agent.inference_operation',$1)", privilege
        )
    return role


async def read_receipt_as_maintenance(connection, role, operation):
    await connection.execute("RESET ROLE")
    try:
        return await connection.fetchrow("SELECT * FROM agent.inference_operation WHERE id=$1::uuid", operation)
    finally:
        await connection.execute(f'SET LOCAL ROLE "{role}"')


async def start_receipt(connection, context):
    audit = InferenceAudit(
        TransactionDatabase(connection), context, str(uuid4()), mode="task_import",
        run_id=None, facts={"candidates": [{"source_line": 1}]}, config={"platform_enabled": False},
    )
    await audit.start()
    return audit


async def test_migration_copies_only_existing_runtime_receipt_privileges(connection):
    role = await connection.fetchval("SELECT current_user")
    # This transaction is rolled back by the integration fixture.
    await connection.execute("RESET ROLE")
    await connection.execute(f'REVOKE ALL ON agent.inference_operation FROM "{role}"')
    assert not await connection.fetchval(
        "SELECT has_table_privilege($1,'agent.inference_operation','INSERT')", role
    )
    root = Path(__file__).resolve().parents[3]
    sql = (root / "database/migrations/V061__inference_audit_runtime_grants.sql").read_text()
    sql = sql.removeprefix("BEGIN;").rsplit("COMMIT;", 1)[0]
    # The full migration has already run in the fresh schema; replay just its
    # permission operation against the pre-grant production condition.
    sql = sql.split("INSERT INTO ops.schema_migration", 1)[0]
    await connection.execute(sql)
    await connection.execute(f'SET LOCAL ROLE "{role}"')
    for privilege in ("SELECT", "INSERT", "UPDATE"):
        assert await connection.fetchval(
            "SELECT has_table_privilege(current_user,'agent.inference_operation',$1)", privilege
        )
    assert not await connection.fetchval(
        "SELECT has_table_privilege(current_user,'agent.inference_operation','DELETE')"
    )
    assert await connection.fetchval(
        "SELECT relrowsecurity FROM pg_class WHERE oid='agent.inference_operation'::regclass"
    )
    assert not await connection.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")


async def test_api_update_without_select_reproduces_failure_and_narrow_finish_succeeds(connection):
    context = await actor(connection, "XS001")
    role = await api_receipt_permissions(connection)
    audit = await start_receipt(connection, context)
    await connection.execute("RESET ROLE")
    await connection.execute(f'GRANT UPDATE ON agent.inference_operation TO "{role}"')
    await connection.execute(f'SET LOCAL ROLE "{role}"')
    # Production's prior API shape did have UPDATE. Its WHERE still required
    # private SELECT, which this test fixture previously granted accidentally.
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.execute(
                "UPDATE agent.inference_operation SET status='failed' WHERE id=$1::uuid AND status='running'",
                audit.operation_id,
            )
    await connection.execute("RESET ROLE")
    await connection.fetchval("SELECT security.reconcile_runtime_grants()")
    await connection.execute(f'SET LOCAL ROLE "{role}"')
    audit.event("route_failed", "senseaudio", error_code="TimeoutError")
    await audit.finish("failed", trace={"provider": "senseaudio"}, error_code="TimeoutError")
    row = await read_receipt_as_maintenance(connection, role, audit.operation_id)
    assert row["status"] == "failed" and row["completed_at"] is not None
    assert row["events"][0]["error_code"] == "TimeoutError"
    assert str(row["actor_user_ref_id"]) == context.user_id
    assert not await connection.fetchval("SELECT has_table_privilege(current_user,'agent.inference_operation','SELECT')")
    assert not await connection.fetchval("SELECT has_table_privilege(current_user,'agent.inference_operation','UPDATE')")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.fetch("SELECT * FROM agent.inference_operation")


@pytest.mark.parametrize("status", ["accepted", "failed", "cancelled", "reconciliation_required"])
async def test_only_one_terminal_completion_and_no_identity_changes(connection, status):
    context = await actor(connection, "XS001")
    role = await api_receipt_permissions(connection)
    audit = await start_receipt(connection, context)
    before = dict(await read_receipt_as_maintenance(connection, role, audit.operation_id))
    assert await connection.fetchval(FINISH, audit.operation_id, status, {"provider": "senseaudio"}, [], None)
    terminal = dict(await read_receipt_as_maintenance(connection, role, audit.operation_id))
    assert not await connection.fetchval(FINISH, audit.operation_id, "failed", {"tamper": True}, [], "rewritten")
    assert dict(await read_receipt_as_maintenance(connection, role, audit.operation_id)) == terminal
    unchanged = set(before) - {"status", "trace", "events", "error_code", "completed_at"}
    assert {key: terminal[key] for key in unchanged} == {key: before[key] for key in unchanged}
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.execute("UPDATE agent.inference_operation SET capability='tampered'")


async def test_completion_rejects_other_actor_role_workspace_or_empty_context(connection):
    context = await actor(connection, "XS001")
    role = await api_receipt_permissions(connection)
    audit = await start_receipt(connection, context)
    for workspace, user, role_code in (
        (str(uuid4()), context.user_id, context.role.value),
        (context.workspace_id, str(uuid4()), context.role.value),
        (context.workspace_id, context.user_id, "administrator"),
        ("", "", ""),
    ):
        await connection.execute(
            "SELECT set_config('app.workspace_id',$1,true),set_config('app.user_ref_id',$2,true),"
            "set_config('app.role_code',$3,true)", workspace, user, role_code,
        )
        assert not await connection.fetchval(FINISH, audit.operation_id, "failed", {}, [], None)
    row = await read_receipt_as_maintenance(connection, role, audit.operation_id)
    assert row["status"] == "running" and row["completed_at"] is None


@pytest.mark.parametrize("change", ["user", "role", "workspace"])
async def test_accounting_finishes_after_mid_call_revocation(connection, change):
    context = await actor(connection, "XS001")
    role = await api_receipt_permissions(connection)
    audit = await start_receipt(connection, context)
    await connection.execute("RESET ROLE")
    if change == "user":
        await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", context.user_id)
    elif change == "role":
        await connection.execute(
            "UPDATE platform.role_binding SET valid_to=clock_timestamp() WHERE user_ref_id=$1::uuid", context.user_id
        )
    else:
        await connection.execute("UPDATE platform.workspace SET status='suspended' WHERE id=$1::uuid", context.workspace_id)
    await connection.execute(f'SET LOCAL ROLE "{role}"')
    await audit.finish("cancelled", trace={}, error_code="CancelledError")
    row = await read_receipt_as_maintenance(connection, role, audit.operation_id)
    assert row["status"] == "cancelled" and row["completed_at"] is not None


@pytest.mark.parametrize("status,trace,events,error", [
    ("running", {}, [], None), (None, {}, [], None), ("failed", [], [], None),
    ("failed", None, [], None), ("failed", {}, {}, None), ("failed", {}, None, None),
    ("failed", {"oversize": "x" * 131073}, [], None), ("failed", {}, ["x" * 131073], None),
    ("failed", {}, [], "x" * 201),
])
async def test_completion_rejects_invalid_or_unbounded_metadata(connection, status, trace, events, error):
    context = await actor(connection, "XS001")
    role = await api_receipt_permissions(connection)
    audit = await start_receipt(connection, context)
    with pytest.raises(asyncpg.InvalidParameterValueError):
        async with connection.transaction():
            await connection.fetchval(FINISH, audit.operation_id, status, trace, events, error)
    assert (await read_receipt_as_maintenance(connection, role, audit.operation_id))["status"] == "running"


async def test_finish_grants_exclude_public_feishu_and_maintenance_entry(connection):
    await api_receipt_permissions(connection)
    assert not await connection.fetchval(
        "SELECT has_function_privilege('salegent_feishu_worker',"
        "'security.finish_inference_operation(uuid,text,jsonb,jsonb,text)','EXECUTE')"
    )
    assert not await connection.fetchval(
        "SELECT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a "
        "WHERE p.oid='security.finish_inference_operation(uuid,text,jsonb,jsonb,text)'::regprocedure "
        "AND a.grantee=0 AND a.privilege_type='EXECUTE')"
    )
    assert not await connection.fetchval(
        "SELECT has_function_privilege(current_user,'security.reconcile_runtime_grants()','EXECUTE')"
    )
    assert await connection.fetchval(
        "SELECT relrowsecurity FROM pg_class WHERE oid='agent.inference_operation'::regclass"
    )
