"""Release checks via peer maintenance connection and the real non-bypass runtime role.

No credentials or business content are read into the report. The receipt write
probe is restricted to XS001 and always rolled back, including on failure.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg

from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.repositories.agent_audit import AgentAuditRepository
from sales_backend.repositories.company_rules import CompanyRulesRepository
from sales_backend.repositories.identity import IdentityRepository


async def main():
    connection = await asyncpg.connect(  # Local PostgreSQL Unix socket, not a temporary file.
        host="/tmp", database="sales_saas", user="postgres", command_timeout=15  # noqa: S108
    )
    await _initialize_connection(connection)
    result = {"role": "sales_runtime", "checks": []}
    try:
        async with connection.transaction(readonly=True):
            await connection.execute("SET LOCAL ROLE sales_runtime")
            assert not await connection.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
            for privilege in ("SELECT", "INSERT", "UPDATE"):
                assert await connection.fetchval(
                    "SELECT has_table_privilege(current_user,'agent.inference_operation',$1)", privilege
                )
            assert not await connection.fetchval(
                "SELECT has_table_privilege(current_user,'agent.inference_operation','DELETE')"
            )
            for code in ("OPSADMIN", "XS001"):
                row = await connection.fetchrow(
                    "SELECT * FROM security.resolve_account_actor('demo-sales-workspace',$1,NULL)", code
                )
                assert row, f"Approved account missing: {code}"
                actor = IdentityRepository._actor(row).context
                await set_request_context(connection, actor)
                repo = CompanyRulesRepository()
                policies = {p["code"]: p["current"] for p in await repo.catalog(connection)}
                assert len(policies) == 4
                assert policies["home_display"]["definition"]["message_order"] == "desc"
                assert policies["customer_quadrant"]["definition"]["potential_threshold"] == 70
                assert policies["customer_quadrant"]["definition"]["relationship_threshold"] == 70
                assert policies["visit_admission"]["definition"]["score_threshold"] == 60
                assert policies["visit_admission"]["definition"]["inclusive"] is False
                technical = await repo.catalog(connection, technical=True)
                assert len(technical) == 7
                assert all(p["current"]["definition"]["strategy"] == "inherit" for p in technical)
                assert all(not p["current"]["definition"]["override_budget"] for p in technical)
                now = datetime.now(UTC)
                audit = await AgentAuditRepository().cases(
                    connection, start=now-timedelta(days=7), end=now+timedelta(seconds=1), limit=1
                )
                if code == "XS001":
                    assert audit["total"] == 0
                result["checks"].append({"account": code, "business_policies": 4,
                                         "technical_policies": 7, "audit_cases": audit["total"]})
        transaction = connection.transaction()
        await transaction.start()
        try:
            await connection.execute("SET LOCAL ROLE sales_runtime")
            await set_request_context(connection, actor)
            identifier = uuid4()
            await connection.execute(
                "INSERT INTO agent.inference_operation(id,workspace_id,actor_user_ref_id,actor_role_code,capability) "
                "VALUES($1,$2::uuid,$3::uuid,$4,'release_rollback_probe')",
                identifier, actor.workspace_id, actor.user_id, actor.role.value,
            )
            assert await connection.execute(
                "UPDATE agent.inference_operation SET status='accepted',completed_at=clock_timestamp() WHERE id=$1",
                identifier,
            ) == "UPDATE 1"
            assert await connection.execute(
                "UPDATE agent.inference_operation SET status='failed' WHERE id=$1", identifier
            ) == "UPDATE 0"
        finally:
            await transaction.rollback()
        result["receipt_write_rollback"] = True
        result["completed_receipt_immutable"] = True
        print(json.dumps(result, ensure_ascii=False))
    finally:
        await connection.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        raise SystemExit(f"Admin database verification failed: {type(exc).__name__}: {exc}") from None
