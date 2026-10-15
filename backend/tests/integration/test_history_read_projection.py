"""Real SQL checks for single historical events and quarter-only plans."""
from datetime import date

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.customer_map import CustomerMapRepository
from sales_backend.repositories.detail_history import DetailHistoryRepository
from sales_backend.repositories.detail_overviews import related_summary
from sales_backend.repositories.opportunity_browse import browse_opportunities
from sales_backend.repositories.opportunity_overview import opportunity_overview
from sales_backend.repositories.visits import VisitRepository
from tests.integration.feishu_fixtures import seed_execute
from tests.integration.tenant_fixtures import active_foreign_workspace
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_customer_map_projection import formal_visit
from tests.integration.test_fde_identity_tasks import fde_fixture
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


async def test_mixed_principal_and_bridge_history_keeps_global_pages_and_rls(connection):
    sales, project, people, _ = await fde_fixture(connection)
    other = await opportunity(connection, "XS001", 200000)
    expected_ids = []
    for index in range(7):
        record = await formal_visit(
            connection, sales, project["customer_id"], f"2026-09-{20 + index % 3}",
            project["id"] if index % 2 == 0 else None,
        )
        expected_ids.append(record["id"])
        if index % 2:
            await seed_execute(
                connection, """INSERT INTO activity.visit_opportunity(workspace_id,visit_id,opportunity_id)
                SELECT workspace_id,$1::uuid,id FROM crm.opportunity WHERE id=ANY($2::uuid[])""",
                record["id"], [project["id"], other["id"]],
            )
    # The other customer's direct history must not enter this target's pages.
    await formal_visit(connection, sales, other["customer_id"], "2026-09-25", other["id"])
    await connection.execute(
        "UPDATE activity.visit SET interaction_at=NULL WHERE id=ANY($1::uuid[])",
        [expected_ids[0], expected_ids[1]],
    )
    legacy_subject = """(v.customer_id=$1::uuid OR (v.opportunity_id IS NULL AND EXISTS (
      SELECT 1 FROM activity.visit_opportunity vo JOIN crm.opportunity linked ON linked.id=vo.opportunity_id
      WHERE vo.visit_id=v.id AND linked.customer_id=$1::uuid AND linked.deleted_at IS NULL))
      OR (v.customer_id IS NULL AND v.opportunity_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM crm.opportunity linked WHERE linked.id=v.opportunity_id
          AND linked.customer_id=$1::uuid AND linked.deleted_at IS NULL))) AND v.deleted_at IS NULL
      AND (v.opportunity_id=$2::uuid OR (v.opportunity_id IS NULL AND EXISTS (
        SELECT 1 FROM activity.visit_opportunity vo WHERE vo.visit_id=v.id AND vo.opportunity_id=$2::uuid)))"""
    repository = DetailHistoryRepository()
    for code in ("XS001", people["lead"]["code"]):
        await actor(connection, code)
        summary = await related_summary(connection, project["customer_id"], project["id"])
        assert summary["visit_count"] == 7
        for sort in (None, "created_desc"):
            order = ("created_at DESC,id DESC" if sort
                     else "interaction_at DESC NULLS LAST,history_sort_date DESC,id DESC")
            expected = [row["id"] for row in await connection.fetch(
                "SELECT v.id::text FROM activity.visit v WHERE " + legacy_subject + " ORDER BY " + order,  # noqa: S608
                project["customer_id"], project["id"],
            )]
            assert len(expected) == 7 and set(expected) == set(expected_ids)
            cursor, seen = None, []
            for offset in (0, 2, 4, 6):
                page = await repository.visits(
                    connection, project["customer_id"], opportunity_id=project["id"],
                    limit=2, cursor=cursor, sort=sort,
                )
                legacy = await repository.visits(
                    connection, project["customer_id"], opportunity_id=project["id"],
                    limit=2, offset=offset, sort=sort,
                )
                ids = [row["id"] for row in page["items"]]
                assert ids == [row["id"] for row in legacy["items"]] == expected[offset:offset + 2]
                seen.extend(ids)
                cursor = page["next_cursor"]
            assert seen == expected and cursor is None
    await actor(connection, "XS002")
    assert not (await repository.visits(
        connection, project["customer_id"], opportunity_id=project["id"],
    ))["items"]
    assert (await related_summary(connection, project["customer_id"], project["id"]))["visit_count"] == 0
    foreign = await active_foreign_workspace(connection)
    await set_request_context(connection, sales.model_copy(update={"workspace_id": foreign}))
    assert not (await repository.visits(
        connection, project["customer_id"], opportunity_id=project["id"],
    ))["items"]
    assert (await related_summary(connection, project["customer_id"], project["id"]))["visit_count"] == 0


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
