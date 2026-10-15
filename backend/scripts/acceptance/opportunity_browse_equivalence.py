"""Read-only, real-account equivalence for SQL paging and full filtered totals.

Uses the existing legacy list as a reference. Outputs counts and timings only.
Run under the application's restricted PostgreSQL role; no fixtures or login.
"""

import asyncio
import json
import time

from sales_backend.config import get_settings
from sales_backend.db import Database
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.opportunities import OpportunityRepository

ACCOUNTS = (
    "XS001", "XS002", "XS003", "ZJ001", "ZJ002", "ZJL001",
    "FDE001", "FDE002", "FDE003", "FDEL001", "OPSADMIN",
)


def check_summary(result, rows):
    opened = [row for row in rows if row["status"] == "open"]
    unknown = sum(row["amount"] is None or row["amount"] < 0 for row in opened)
    assert result["summary"] == {
        "total": len(rows), "open_count": len(opened), "unknown_open_amount_count": unknown,
        "open_amount": None if unknown else sum(row["amount"] for row in opened),
    }


async def main():
    database = Database(get_settings())
    await database.connect()
    results = []
    repo = OpportunityRepository()
    try:
        async with database.connection() as connection:
            assert not await connection.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ), "Use the restricted application role"
        for code in ACCOUNTS:
            async with database.connection() as connection:
                actor = IdentityRepository._actor(await connection.fetchrow(
                    "SELECT * FROM security.resolve_account_actor($1,$2,NULL)",
                    "demo-sales-workspace", code,
                )).context
            scopes = ["self", "team"] if actor.role.value == "fde_lead" else [None]
            for scope in scopes:
                async with database.transaction(actor, readonly=True) as connection:
                    await connection.execute("SET LOCAL statement_timeout='12s'")
                    started = time.perf_counter()
                    old = await repo.list(
                        connection, actor, customer_id=None, owner_name=None, probability=None,
                        stage_code=None, close_from=None, close_to=None, limit=None,
                        include_closed=True, scope=scope,
                    )
                    old_ms = round(1000 * (time.perf_counter() - started), 2)
                    started = time.perf_counter()
                    first = await repo.page(connection, actor, limit=20, include_closed=True, scope=scope)
                    first_ms = round(1000 * (time.perf_counter() - started), 2)
                    check_summary(first, old)
                    assert first["items"] == old[:20], (code, scope, "first_page")
                    items = list(first["items"])
                    page = first
                    page_count = 1
                    while page["has_more"]:
                        assert page_count < 100, "Bounded probe: over 2000 opportunities"
                        page = await repo.page(
                            connection, actor, limit=20, include_closed=True,
                            scope=scope, offset=page["next_offset"],
                        )
                        check_summary(page, old)
                        items.extend(page["items"])
                        page_count += 1
                    assert items == old, (code, scope, "all_rows")
                    assert len(items) == len({row["id"] for row in items}), (code, "duplicates")
                    filters_tested = 0
                    for product in first["facets"]["product_lines"][:3]:
                        rows = [row for row in old if row["product_line"] == product]
                        filtered = await repo.page(
                            connection, actor, limit=1, include_closed=True,
                            scope=scope, product_line=product,
                        )
                        check_summary(filtered, rows)
                        assert filtered["items"] == rows[:1], (code, "product_filter")
                        filters_tested += 1
                    result = {
                        "account": code, "scope": scope, "total": len(items),
                        "first_page_rows": len(first["items"]), "pages": page_count,
                        "rows_equal": True, "full_summary_equal": True,
                        "product_filters_tested": filters_tested,
                        "old_all_rows_ms": old_ms, "new_first_page_ms": first_ms,
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
