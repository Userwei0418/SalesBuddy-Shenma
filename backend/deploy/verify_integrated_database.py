"""Read-only production readiness checks; never emit business rows or secrets."""

import asyncio
import json

import asyncpg

from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.repositories.company_rules import CompanyRulesRepository
from sales_backend.repositories.identity import IdentityRepository


async def main():
    c = await asyncpg.connect(host="/tmp", database="sales_saas", user="postgres", command_timeout=15)
    await _initialize_connection(c)
    try:
        async with c.transaction(readonly=True):
            assert await c.fetchval("SELECT max(version) FROM ops.schema_migration") == "V067"
            await c.execute("SET LOCAL ROLE sales_runtime")
            assert not await c.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
            for code in ("OPSADMIN", "XS001"):
                row = await c.fetchrow("SELECT * FROM security.resolve_account_actor('demo-sales-workspace',$1,NULL)", code)
                actor = IdentityRepository._actor(row).context
                await set_request_context(c, actor)
                policies = {p["code"] for p in await CompanyRulesRepository().catalog(c)}
                assert {"score.maturity", "score.efficiency", "score.competency"} <= policies, policies
                technical = {p["code"] for p in await CompanyRulesRepository().catalog(c, technical=True)}
                assert {"agent_execution.customer_advice", "agent_execution.opportunity_advice", "agent_execution.visit_advice"} <= technical, technical
                for table in ("insight.business_advice", "insight.business_suggestion", "workflow.task_candidate"):
                    assert await c.fetchval("SELECT has_table_privilege(current_user,$1,'SELECT')", table), table
            print(json.dumps({"schema": "V067", "runtime_role": "sales_runtime", "rls_bypass": False,
                              "policy_catalog_verified": True, "new_table_access_verified": True}))
    finally:
        await c.close()


if __name__ == "__main__":
    asyncio.run(main())
