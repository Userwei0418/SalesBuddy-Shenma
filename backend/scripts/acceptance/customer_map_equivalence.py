"""Read-only map projection comparison with the eleven existing demo accounts.

Run in the application environment, using its non-privileged PostgreSQL role.
Does not log in, create fixtures, write business records or emit record contents.
This measures repository reads, not HTTP latency or concurrent-user capacity.
"""

import asyncio
import json
import time

from sales_backend.config import get_settings
from sales_backend.db import Database
from sales_backend.repositories.collaboration import (
    members_by_opportunity,
    scope_members,
    scoped_opportunity_ids,
)
from sales_backend.repositories.customer_map import CustomerMapRepository
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.repositories.identity import IdentityRepository

ACCOUNTS = (
    "XS001", "XS002", "XS003", "ZJ001", "ZJ002", "ZJL001",
    "FDE001", "FDE002", "FDE003", "FDEL001", "OPSADMIN",
)
DISPLAY_KEYS = (
    "name", "owner_name", "owner_user_ref_id", "owner_team_id", "team_name",
    "potential_score", "relationship_score", "quadrant_code", "quadrant_policy",
    "latest_visit_at", "weekly_follow_up_count", "risk_title", "risk_severity",
)


class QueryTrace:
    def __init__(self, connection):
        self.connection = connection
        self.query = None

    async def fetch(self, sql, *args):
        self.query = (sql, args)
        return await self.connection.fetch(sql, *args)


def encoded_size(value):
    return len(json.dumps(value, default=str, ensure_ascii=False).encode())


async def main():
    database = Database(get_settings())
    await database.connect()
    results = []
    try:
        async with database.connection() as connection:
            privileged = await connection.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
            if privileged:
                raise RuntimeError("Run under the restricted application database role")
        for code in ACCOUNTS:
            async with database.connection() as connection:
                row = await connection.fetchrow(
                    "SELECT * FROM security.resolve_account_actor($1,$2,NULL)",
                    "demo-sales-workspace", code,
                )
                actor = IdentityRepository._actor(row).context
            async with database.transaction(actor, readonly=True) as connection:
                await connection.execute("SET LOCAL statement_timeout='12s'")
                scoped = None
                started = time.perf_counter()
                old = await CustomerRepository().list(
                    connection, query=None, level=None, unassigned=None, limit=None,
                )
                if actor.role.value.startswith("fde"):
                    _, _, _, scoped = await scoped_opportunity_ids(connection, actor)
                    allowed = {row["customer_id"] for row in scoped}
                    old = [row for row in old if row["id"] in allowed]
                    members = await members_by_opportunity(connection, [row["id"] for row in scoped])
                    for item in old:
                        people = {
                            person["id"]: person
                            for row in scoped if row["customer_id"] == item["id"]
                            for person in members.get(row["id"], [])
                        }
                        item["fde_members"] = list(people.values())
                old_ms = 1000 * (time.perf_counter() - started)
                started = time.perf_counter()
                user_ids = None
                if scoped is not None:
                    _, user_ids, _ = await scope_members(connection, actor)
                trace = QueryTrace(connection)
                new = await CustomerMapRepository().read(trace, fde_user_ids=user_ids)
                new_ms = 1000 * (time.perf_counter() - started)
                old_by_id = {row["id"]: row for row in old}
                assert set(old_by_id) == {row["id"] for row in new}, (code, "customer_set")
                for row in new:
                    previous = old_by_id[row["id"]]
                    assert all(row[key] == previous[key] for key in DISPLAY_KEYS), (code, "display_fact")
                    assert sorted(row["plan_close_dates"], key=str) == sorted(
                        previous["plan_close_dates"], key=str
                    ), (code, "plans")
                    if scoped is not None:
                        assert {p["id"] for p in row["fde_members"]} == {
                            p["id"] for p in previous["fde_members"]
                        }, (code, "fde_members")
                    expected = await connection.fetchrow(
                        """SELECT count(*) FILTER(WHERE amount IS NULL OR amount<0) AS unknown,
                            coalesce(sum(amount),0) AS amount FROM crm.opportunity
                            WHERE customer_id=$1::uuid AND deleted_at IS NULL AND status='open'""",
                        row["id"],
                    )
                    assert row["acv_amount"] == (
                        None if expected["unknown"] else expected["amount"]
                    ), (code, "acv")
                raw = await connection.fetchval(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + trace.query[0], *trace.query[1],
                )
                plan = (json.loads(raw) if isinstance(raw, str) else raw)[0]
                result = {
                    "account": code, "customers": len(new), "sets_equal": True,
                    "display_facts_equal": True, "acv_db_equal": True,
                    "old_ms": round(old_ms, 2), "new_ms": round(new_ms, 2),
                    "old_bytes": encoded_size(old), "new_bytes": encoded_size(new),
                    "explain_ms": plan["Execution Time"],
                    "shared_hit_blocks": plan["Plan"].get("Shared Hit Blocks"),
                    "temp_written_blocks": plan["Plan"].get("Temp Written Blocks"),
                }
                results.append(result)
                print(json.dumps(result), flush=True)
        print(json.dumps({"passed": len(results), "items": results}), flush=True)
    finally:
        await database.close()


async def bounded_main():
    async with asyncio.timeout(150):
        await main()


if __name__ == "__main__":
    asyncio.run(bounded_main())
