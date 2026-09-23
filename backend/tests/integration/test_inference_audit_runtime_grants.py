"""Reproduce production's inherited app role without the fixture's blanket grants."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio


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
