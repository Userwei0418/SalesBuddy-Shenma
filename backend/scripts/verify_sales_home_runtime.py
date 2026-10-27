"""Read deployed home actions and claim eligibility without sessions or writes.

Uses existing business identities, the normal capability resolver and claim-pool
handler inside one read-only snapshot. Prints no business rows, names or amounts.
"""

import asyncio
import json
from collections import Counter
from types import SimpleNamespace

from fastapi import HTTPException
from verify_profile_runtime import Checks, ExistingReadonlyTransaction, load_actors

from sales_backend.api.assistant import home_quick_actions
from sales_backend.api.customers import customer_claim_pool
from sales_backend.config import get_settings
from sales_backend.db import Database, set_request_context
from sales_backend.domain.agent import AgentMode
from sales_backend.domain.policy import assert_mode_allowed
from sales_backend.services.capabilities import capability_snapshot


async def main():
    database = Database(get_settings())
    await database.connect()
    checks, roles = Checks(), Counter()
    try:
        async with database.connection() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                checks.require("readonly_transaction", await connection.fetchval("SHOW transaction_read_only") == "on")
                actors = await load_actors(connection, checks)
                for (_, role), subject in actors.items():
                    roles[role] += 1
                    await set_request_context(connection, subject)
                    snapshot = await capability_snapshot(connection, subject)
                    caps = snapshot["capabilities"]
                    checks.require("capability_version", bool(snapshot["permission_version"]))
                    checks.require("personal_visit", caps["visit.create"] and caps["visit.supplement"])
                    checks.require("task_create", caps["task.create"])
                    checks.require("no_direct_customer_create", caps["customer.create"] is False)
                    assert_mode_allowed(subject.role, AgentMode.VISIT_ENTRY)
                    sales = role in {"sales", "supervisor", "manager"}
                    checks.require("claim_role_boundary", caps["customer.claim"] is sales)
                    expected = (["customer_claim"] if sales else []) + ["visit_entry", "management_task"]
                    checks.require(
                        "home_action_order", [x["code"] for x in home_quick_actions(subject.role, caps)] == expected
                    )
                    args = dict(
                        q=None,
                        page_size=50,
                        offset=0,
                        identity=SimpleNamespace(actor=subject),
                        database=ExistingReadonlyTransaction(connection),
                    )
                    if not sales:
                        try:
                            await customer_claim_pool(**args)
                        except HTTPException as error:
                            checks.require("fde_claim_directory_denied", error.status_code == 403)
                        else:
                            checks.require("fde_claim_directory_denied", False)
                        continue
                    page = await customer_claim_pool(**args)
                    checks.require(
                        "claim_directory_page", len(page["items"]) <= 50 and page["total"] >= len(page["items"])
                    )
                    for item in page["items"]:
                        checks.require(
                            "claim_eligibility", item["can_claim"] is (item["ownership_state"] == "unclaimed")
                        )
                        checks.require(
                            "claim_sql_agrees",
                            item["can_claim"]
                            == await connection.fetchval(
                                "SELECT security.can_claim_customer($1::uuid)",
                                item["id"],
                            ),
                        )
        checks.require("five_roles", set(roles) == {"sales", "supervisor", "manager", "fde", "fde_lead"})
        print(
            json.dumps(
                {
                    "ok": True,
                    "roles": dict(roles),
                    "checks": dict(checks.counts),
                    "total_checks": sum(checks.counts.values()),
                    "readonly": True,
                    "business_writes": False,
                    "model_calls": False,
                    "sessions_created": False,
                }
            )
        )
    finally:
        await database.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(json.dumps({"ok": False, "readonly": True, "error_type": type(error).__name__}))
        raise SystemExit(1) from None
