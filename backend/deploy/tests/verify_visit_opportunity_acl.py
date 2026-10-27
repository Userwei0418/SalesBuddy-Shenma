"""Invoked only by an explicitly isolated PostgreSQL harness; no env or network discovery."""

from uuid import uuid4

import asyncpg
from repair_visit_opportunity_acl import apply_plan, inspect
from runtime_roles import identifier


async def verify(conn, role):
    # Caller supplies a disposable full V278 schema and an API role provisioned
    # from the reviewed source policy. No credentials or production config read.
    q = identifier(role)
    await conn.execute(f"REVOKE INSERT ON activity.visit_opportunity FROM {q}")
    before = await inspect(conn, role)
    assert before["effective"] == ["SELECT"]
    cases = []
    for name, sql in [
        ("elevated_role", f"ALTER ROLE {q} BYPASSRLS"),
        ("extra_table_acl", f"GRANT UPDATE ON activity.visit_opportunity TO {q}"),
        ("extra_column_acl", f"GRANT UPDATE(created_at) ON activity.visit_opportunity TO {q}"),
        ("grant_option", f"GRANT SELECT ON activity.visit_opportunity TO {q} WITH GRANT OPTION"),
        ("rls_disabled", "ALTER TABLE activity.visit_opportunity NO FORCE ROW LEVEL SECURITY"),
        ("policy_changed", "ALTER POLICY permission_insert ON activity.visit_opportunity WITH CHECK(true)"),
    ]:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(sql)
            try:
                await inspect(conn, role)
            except ValueError:
                cases.append(name)
            else:
                raise AssertionError(name + " was not rejected")
        finally:
            await tx.rollback()
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(f"GRANT SELECT ON activity.visit_opportunity TO {q} WITH GRANT OPTION")
        try:
            await apply_plan(conn, before)
        except ValueError:
            cases.append("stale_plan")
        else:
            raise AssertionError("stale plan applied")
    finally:
        await tx.rollback()
    async with conn.transaction():
        result = await apply_plan(conn, before)
    assert result["status"] == "passed"
    after = await inspect(conn, role)
    assert after["effective"] == ["SELECT", "INSERT"]
    try:
        async with conn.transaction():
            await apply_plan(conn, before)
    except ValueError:
        cases.append("repeat_apply_refused")
    else:
        raise AssertionError("repeat apply was not refused")
    # Use the actual non-superuser/non-bypass API role. An unbound/cross-tenant
    # insert is now blocked by RLS, not the missing table ACL; DELETE/UPDATE
    # remain denied even with the new INSERT grant.
    async with conn.transaction():
        await conn.execute(f"SET LOCAL ROLE {q}")
        for action, sql in [
            (
                "rls_blocks_unbound_insert",
                "INSERT INTO activity.visit_opportunity(workspace_id,visit_id,opportunity_id,created_by_user_ref_id) "
                "VALUES($1,$2,$3,$4)",
            ),
            ("update_still_denied", "UPDATE activity.visit_opportunity SET created_at=created_at"),
            ("delete_still_denied", "DELETE FROM activity.visit_opportunity"),
        ]:
            try:
                async with conn.transaction():
                    await conn.execute(sql, *([uuid4() for _ in range(4)] if action.startswith("rls") else []))
            except asyncpg.InsufficientPrivilegeError as exc:
                if action.startswith("rls"):
                    assert "row-level security" in str(exc), str(exc)
                cases.append(action)
            else:
                raise AssertionError(action + " succeeded")
    # Exercise a real allowed multi-opportunity link as the runtime role, with
    # synthetic objects rolled back. Existing archive/CRM fixtures keep their
    # normal authorization and tenant checks; no RLS policy is mocked.
    from sales_backend.db import set_request_context
    from sales_backend.repositories.visits import VisitRepository
    from tests.integration.test_fde_identity_tasks import fde_fixture
    from tests.integration.test_fde_owned_recording import fields_for, other_project

    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(f"SET LOCAL ROLE {q}")
        sales, opportunity, _, _ = await fde_fixture(conn)
        second = await other_project(conn, sales, opportunity)
        await set_request_context(conn, sales)
        visit = await VisitRepository().create(
            conn,
            sales,
            customer_id=opportunity["customer_id"],
            fields={**fields_for(opportunity), "opportunity_id": None},
        )
        await conn.executemany(
            "INSERT INTO activity.visit_opportunity(workspace_id,visit_id,opportunity_id,created_by_user_ref_id) "
            "VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid)",
            [(sales.workspace_id, visit["id"], item["id"], sales.user_id) for item in (opportunity, second)],
        )
        assert (
            await conn.fetchval("SELECT count(*) FROM activity.visit_opportunity WHERE visit_id=$1::uuid", visit["id"])
            == 2
        )
        await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cases.append("authorized_multi_opportunity_insert")
    finally:
        await tx.rollback()
    return {
        "passed": cases,
        "effective": after["effective"],
        "schema": "V278",
        "grant_count": len(result["added"]),
        "unrelated_security_unchanged": before["unrelated_security"] == after["unrelated_security"],
    }
