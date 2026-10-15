"""Console opportunity totals, pagination and scale under native tenant RLS."""

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal
from time import perf_counter
from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.operations_opportunities import OperationsOpportunityRepository
from tests.integration.feishu_fixtures import assert_restricted, fixture_owner
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def seed_customer(connection, workspace, user, name, owner=None):
    return await connection.fetchval(
        """INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id,
                                  owner_user_ref_id,owner_team_id)
           VALUES($1::uuid,$2,$2,$3::uuid,$4::uuid,$5::uuid) RETURNING id""", workspace, name, user,
        owner.user_id if owner else None, owner.team_ids[0] if owner else None,
    )


def assert_totals(result, expected):
    amounts = [row["amount"] for row in expected if row["amount"] is not None]
    amount = sum(amounts, Decimal(0))
    assert result["total"] == len(expected)
    assert result["total_amount"] == amount
    for item in result["items"]:
        assert item["total_count"] == len(expected)
        # The existing row contract distinguishes SQL SUM(NULL) from zero.
        assert item["total_amount"] == (amount if amounts else None)
    groups = defaultdict(list)
    for row in expected:
        groups[(row["stage_code"], row["status"], row["probability"])].append(row["amount"])
    actual = {(row["stage_code"], row["status"], row["probability"]): (row["count"], row["amount"])
              for row in result["stages"]}
    assert actual == {key: (len(values), sum((v for v in values if v is not None), Decimal(0))
                           if any(v is not None for v in values) else None)
                      for key, values in groups.items()}


@pytest.mark.parametrize("plan_mode", ["force_custom_plan", "force_generic_plan"])
async def test_console_opportunity_filters_empty_pages_null_amounts_and_scope(connection, plan_mode):
    await assert_restricted(connection)
    await connection.execute(f"SET LOCAL plan_cache_mode='{plan_mode}'")
    first, second = await actor(connection, "XS001"), await actor(connection, "XS002")
    admin = await actor(connection, "ADMIN001")
    marker = "console-opportunity-" + uuid4().hex
    rows = []
    async with fixture_owner(connection):
        customers = [await seed_customer(connection, admin.workspace_id, admin.user_id, marker + suffix, owner)
                     for suffix, owner in (("-customer-a", first), ("-customer-b", second))]
        samples = [(first, 0, Decimal('100.01'), 10, "open", date(2026, 9, 1)),
                   (first, 0, None, 30, "open", date(2026, 9, 2)),
                   (second, 1, Decimal('200.02'), 100, "won", date(2026, 10, 1)),
                   (second, 1, None, 30, "open", date(2026, 10, 2)),
                   (first, 0, 0, 10, "lost", date(2026, 9, 3))]
        for ordinal, (owner, index, amount, probability, status, close) in enumerate(samples):
            row = await connection.fetchrow(
                """INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,
                     owner_team_id,created_by_user_ref_id,amount,probability,status,expected_close_date,updated_at)
                   VALUES($1::uuid,$2,$3,$4::uuid,$5::uuid,$6::uuid,$7,$8,$9,$10,
                          '2026-09-01'::timestamptz + $11::integer * interval '1 minute')
                   RETURNING id::text,customer_id,owner_user_ref_id::text,amount,probability,status,
                             expected_close_date,stage_code""",
                admin.workspace_id, customers[index], marker + f"-{ordinal}", owner.user_id,
                owner.team_ids[0], admin.user_id, amount, probability, status, close, ordinal,
            )
            rows.append(dict(row))
        await connection.execute(
            """INSERT INTO crm.opportunity(workspace_id,customer_id,name,amount,deleted_at)
               VALUES($1::uuid,$2,$3,999999,clock_timestamp())""", admin.workspace_id, customers[0], marker,
        )
        foreign = uuid4()
        await connection.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'隔离外公司')",
            foreign, str(foreign),
        )
        foreign_user = await connection.fetchval(
            """INSERT INTO platform.user_ref(workspace_id,external_user_id,display_name)
               VALUES($1,$2,'隔离外公司人员') RETURNING id""", foreign, str(uuid4()),
        )
        foreign_customer = await seed_customer(connection, foreign, foreign_user, marker + "-foreign")
        await connection.execute(
            "INSERT INTO crm.opportunity(workspace_id,customer_id,name,amount) VALUES($1,$2,$3,888888)",
            foreign, foreign_customer, marker,
        )
    repo = OperationsOpportunityRepository()
    cases = [({}, rows), ({"q": None}, rows), ({"owner": first.user_id}, [rows[i] for i in (0, 1, 4)]),
             ({"customer": str(customers[1])}, [rows[i] for i in (2, 3)]),
             ({"status": "won"}, [rows[2]]), ({"probability": 30}, [rows[1], rows[3]]),
             ({"close_from": date(2026, 9, 2), "close_to": date(2026, 9, 3)}, [rows[1], rows[4]]),
             ({"owner": first.user_id, "probability": 10, "status": "lost"}, [rows[4]]),
             ({"q": marker + "-customer-b"}, [rows[2], rows[3]]), ({"q": marker + "-missing"}, [])]
    for filters, expected in cases:
        for offset in (0, 2, 20):
            result = await repo.list(connection, **{"q": marker, **filters}, limit=2, offset=offset)
            assert_totals(result, expected)
            assert [item["id"] for item in result["items"]] == [row["id"] for row in reversed(expected)][offset:offset + 2]
    await set_request_context(connection, first)
    first_result = await repo.list(connection, q=marker)
    assert_totals(first_result, [rows[i] for i in (0, 1, 4)])
    assert not ({rows[2]["id"], rows[3]["id"]} & {r["id"] for r in first_result["items"]})
    await grant(connection, admin, admin.user_id, [dict(permission="opportunity.read", effect="deny")])
    await set_request_context(connection, admin)
    assert_totals(await repo.list(connection, q=marker), [])


class TimedQueries:
    def __init__(self, connection):
        self.connection = connection
        self.calls = []

    async def fetch(self, query, *args):
        started = perf_counter()
        rows = await self.connection.fetch(query, *args)
        self.calls.append({"ms": round((perf_counter() - started) * 1000, 3),
                           "rows": len(rows), "query": query, "args": args})
        return rows


async def test_console_opportunity_scale_reports_query_budget(connection):
    await assert_restricted(connection)
    admin = await actor(connection, "ADMIN001")
    marker = "console-opportunity-scale-" + uuid4().hex
    async with fixture_owner(connection):
        customer = await seed_customer(connection, admin.workspace_id, admin.user_id, marker)
        await connection.execute(
            """INSERT INTO crm.opportunity(workspace_id,customer_id,name,created_by_user_ref_id,amount,probability)
               SELECT $1::uuid,$2,$3||n,$4::uuid,CASE WHEN n%5=0 THEN NULL ELSE 100 END,10
               FROM generate_series(1,5000)n""", admin.workspace_id, customer, marker, admin.user_id,
        )
        await connection.execute("ANALYZE crm.customer; ANALYZE crm.opportunity")
    await connection.execute("SET LOCAL statement_timeout='10s'")
    repo = OperationsOpportunityRepository()
    for offset in (0, 2500, 6000):
        measured = TimedQueries(connection)
        started = perf_counter()
        result = await repo.list(measured, q=marker, limit=50, offset=offset)
        elapsed = (perf_counter() - started) * 1000
        assert result["total"] == 5000 and result["total_amount"] == 400000
        assert len(result["items"]) == (50 if offset < 5000 else 0)
        assert len(measured.calls) == 1
        assert all(row["total_count"] == 5000 and row["total_amount"] == 400000 for row in result["items"])
        plans = []
        for call in measured.calls:
            plan = await connection.fetchval("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + call["query"], *call["args"])
            root = plan[0]
            def nodes(node):
                return [node["Node Type"], *(kind for child in node.get("Plans", []) for kind in nodes(child))]
            plans.append({"ms": root["Execution Time"], "nodes": nodes(root["Plan"]),
                          "shared_hits": root["Plan"].get("Shared Hit Blocks", 0)})
        print("CONSOLE_OPPORTUNITY_SCALE " + json.dumps({"rows": 5000, "offset": offset,
              "elapsed_ms": round(elapsed, 3), "sql_count": len(measured.calls),
              "queries": [{key: call[key] for key in ("ms", "rows")} for call in measured.calls], "plans": plans}))
