"""Real SQL checks for single historical events and quarter-only plans."""
from datetime import date

import pytest
from tests.integration.feishu_fixtures import seed_execute

from sales_backend.repositories.customer_map import CustomerMapRepository
from sales_backend.repositories.detail_history import DetailHistoryRepository
from sales_backend.repositories.detail_overviews import related_summary
from sales_backend.repositories.opportunity_browse import browse_opportunities
from sales_backend.repositories.opportunity_overview import opportunity_overview
from sales_backend.repositories.visits import VisitRepository
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_customer_map_projection import formal_visit
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def test_one_visit_is_visible_through_both_opportunities_without_duplicate_counts(connection):
    first = await opportunity(connection, "XS001", 100000)
    second = await opportunity(connection, "XS001", 200000)
    person = await actor(connection, "XS001")
    record = await formal_visit(connection, person, first["customer_id"], "2026-09-21")
    await actor(connection, "OPS001")
    await seed_execute(
        connection, """INSERT INTO activity.visit_opportunity(workspace_id,visit_id,opportunity_id)
        SELECT workspace_id,$1::uuid,id FROM crm.opportunity WHERE id=ANY($2::uuid[])""",
        record["id"], [first["id"], second["id"]],
    )
    await actor(connection, "XS001")
    detail = await VisitRepository().detail(connection, record["id"])
    assert detail["opportunity_id"] is None
    assert {r["id"] for r in detail["linked_opportunities"]} == {first["id"], second["id"]}
    for project in (first, second):
        summary = await related_summary(connection, project["customer_id"], project["id"])
        assert summary["confirmed_visit_count"] == 1
        history = await DetailHistoryRepository().visits(
            connection, project["customer_id"], opportunity_id=project["id"], limit=20,
        )
        assert [v["id"] for v in history["items"]] == [record["id"]]
        assert history["items"][0]["customer_id"] == first["customer_id"]
        timeline = await DetailHistoryRepository().timeline(connection, project["id"])
        assert sum(e["object_type"] == "visit" for e in timeline["items"]) == 1
    points = await CustomerMapRepository().read(connection, as_of=date(2026, 9, 22))
    assert {first["customer_id"], second["customer_id"]} <= {p["id"] for p in points}


async def test_quarter_precision_list_overview_and_map_agree_without_making_a_date(connection):
    project = await opportunity(connection, "XS001", 100000)
    person = await actor(connection, "XS001")
    await connection.execute(
        """UPDATE crm.opportunity SET expected_close_date=NULL,expected_close_year=2026,
        expected_close_quarter=3 WHERE id=$1::uuid""", project["id"],
    )
    await formal_visit(connection, person, project["customer_id"], "2026-09-21", project["id"])
    page = await browse_opportunities(connection, person, limit=20, year=2026, quarters=[3])
    row = next(r for r in page["items"] if r["id"] == project["id"])
    assert row["expected_close_date"] is None
    assert (row["expected_close_year"], row["expected_close_quarter"]) == (2026, 3)
    overview = await opportunity_overview(connection, person, year=2026, quarters=[3])
    assert overview["metrics"]["total"] == page["summary"]["total"]
    assert overview["metrics"]["missingCloseDates"] == 0
    points = await CustomerMapRepository().read(connection, as_of=date(2026, 9, 22))
    point = next(p for p in points if p["id"] == project["customer_id"])
    assert point["plan_close_dates"] == [None]
    assert point["plan_close_periods"] == [{"date": None, "year": 2026, "quarter": 3}]
