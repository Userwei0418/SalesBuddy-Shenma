"""Real RLS tests: complete customer scope, light fields and explicit unknown ACV."""

from decimal import Decimal

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.collaboration import scope_members, scoped_opportunity_ids
from sales_backend.repositories.customer_assets import CustomerAssetRepository, today
from sales_backend.repositories.customer_map import CustomerMapRepository
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.repositories.visits import VisitRepository
from sales_backend.services.opportunities import save_opportunity
from tests.integration.test_customer_assets import customer
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("code", ["XS001", "XS002", "ZJ001", "ZJL001", "OPS001"])
async def test_light_map_preserves_authorized_customers_and_display_facts(connection, code):
    await actor(connection, code)
    old = await CustomerRepository().list(connection, query=None, level=None, unassigned=None, limit=None)
    new = await CustomerMapRepository().read(connection)
    assert {r["id"] for r in new} <= {r["id"] for r in old}
    assert len(new) == len({r["id"] for r in new})
    before = {r["id"]: r for r in old}
    display_fields = (
        "name", "owner_name", "owner_user_ref_id", "owner_team_id", "sales_members", "team_name",
        "potential_score", "relationship_score", "quadrant_code",
        "latest_visit_at", "weekly_follow_up_count", "risk_title", "risk_severity",
    )
    for row in new:
        assert {k: row[k] for k in display_fields} == {k: before[row["id"]][k] for k in display_fields}
        assert sorted(row["plan_close_dates"], key=str) == sorted(before[row["id"]]["plan_close_dates"], key=str)
        assert not {"attributes", "import_meta", "next_action", "demand_summary", "visits"} & row.keys()


async def test_map_assessment_identity_and_policy_come_from_the_same_visible_score(connection):
    from tests.integration.test_fde_battle_map_reviews import answer, handler_for

    owner = await actor(connection, "XS001")
    item = await customer(connection, owner)
    await formal_visit(connection, owner, item["id"], today().isoformat())
    handler = handler_for(connection)
    for code, potential in [("XS001", 90), ("ZJ001", 30)]:
        person = await actor(connection, code)
        facts = await handler._load_facts(item["id"], person)
        await handler._persist(item["id"], person, facts, answer(handler, facts, potential), {})
        await connection.execute(
            "UPDATE insight.quadrant_score SET input_snapshot=jsonb_set(input_snapshot,"
            "'{company_policy}',jsonb_build_object('test_owner',$2::text)) "
            "WHERE customer_id=$1::uuid AND subject_user_ref_id=common.current_user_ref_id() "
            "AND valid_to='infinity'", item["id"], code,
        )
    await actor(connection, "ZJL001")
    expected = await connection.fetchrow(
        "SELECT id::text AS assessment_id,subject_user_ref_id::text,potential_score,"
        "input_snapshot->'company_policy' AS quadrant_policy FROM insight.quadrant_score "
        "WHERE customer_id=$1::uuid AND valid_to='infinity' "
        "ORDER BY calculated_at DESC,id DESC LIMIT 1", item["id"],
    )
    point = next(row for row in await CustomerMapRepository().read(connection) if row["id"] == item["id"])
    assert {key: point[key] for key in expected.keys()} == dict(expected)
    assert point["quadrant_policy"] == {"test_owner": "ZJ001"}
    assert point["potential_score"] == 30


async def test_map_no_opportunities_and_unknown_are_distinct(connection):
    sales = await actor(connection, "XS001")
    c = await customer(connection, sales)
    await formal_visit(connection, sales, c["id"], today().isoformat())
    repo = CustomerMapRepository()

    async def point():
        return next(r for r in await repo.read(connection) if r["id"] == c["id"])

    empty = await point()
    assert empty["open_count"] == 0 and empty["acv_amount"] == 0 and empty["plan_close_dates"] == []
    from datetime import date

    first = await save_opportunity(connection, sales, customer_id=c["id"], data={
        "name": "地图已知金额", "probability": 30, "amount": Decimal(120000),
        "expected_close_date": date(2027, 1, 5),
        "quarterly_forecasts": [{"year": 2027, "quarter": 1, "recognized_amount": 0, "collection_amount": 0}],
    })
    second = await save_opportunity(connection, sales, customer_id=c["id"], data={
        "name": "地图待补金额", "probability": 10, "amount": Decimal(30000),
        "expected_close_date": date(2026, 12, 1),
    })
    assert (await point())["acv_amount"] == 150000
    await connection.execute("UPDATE crm.opportunity SET amount=NULL WHERE id=$1::uuid", second["id"])
    partial = await point()
    assert partial["open_count"] == 2 and partial["unknown_amount_count"] == 1
    assert partial["acv_amount"] is None and partial["opportunity_amount"] is None
    await connection.execute("UPDATE crm.opportunity SET amount=NULL WHERE id=$1::uuid", first["id"])
    assert (await point())["unknown_amount_count"] == 2
    await connection.execute("UPDATE crm.opportunity SET amount=0 WHERE customer_id=$1::uuid", c["id"])
    zero = await point()
    assert zero["acv_amount"] == 0 and zero["unknown_amount_count"] == 0 and zero["open_count"] == 2


@pytest.mark.parametrize("label,scope", [("first", "self"), ("second", "self"), ("lead", "team")])
async def test_fde_scope_precedes_map_projection_and_keeps_customer_context(connection, label, scope):
    sales, opportunity, people, _ = await fde_fixture(connection)
    await set_request_context(connection, sales)
    await formal_visit(connection, sales, opportunity["customer_id"], today().isoformat(), opportunity["id"])
    from datetime import date

    await save_opportunity(connection, sales, customer_id=opportunity["customer_id"], data={
        "name": "客户另一条未协助项目", "probability": 10, "amount": Decimal(900),
        "expected_close_date": date(2027, 3, 1),
    })
    fde = await actor(connection, people[label]["code"])
    _, ids, _ = await scope_members(connection, fde, scope)
    _, _, _, old_scope = await scoped_opportunity_ids(connection, fde, scope)
    rows = await CustomerMapRepository().read(connection, fde_user_ids=ids)
    assert {r["id"] for r in rows} == {r["customer_id"] for r in old_scope}
    row = next(r for r in rows if r["id"] == opportunity["customer_id"])
    assert row["acv_amount"] == Decimal(100900) and row["open_count"] == 2
    assert {p["id"] for p in row["fde_members"]} == {people["first"]["id"], people["second"]["id"]}
    assert await CustomerMapRepository().read(connection, fde_user_ids=[]) == []
    with pytest.raises(PermissionError):
        await scope_members(connection, fde, scope, "00000000-0000-0000-0000-000000000001")


async def formal_visit(connection, who, customer_id, day, opportunity_id=None):
    return await VisitRepository().create(connection, who, customer_id=customer_id, fields={
        "interaction_at": day, "created_date": day, "contact_name": "真实历史联系人",
        "follow_up_record": "核对本次已知事实", "next_action": "后续由负责人核实资料",
        "opportunity_id": opportunity_id, "_follow_up_quality_score": 80,
    })


async def test_active_window_uses_original_date_not_claim_or_import_date(connection):
    from datetime import date
    sales = await actor(connection, "XS001")
    claimed, old, boundary, first_half, current, future = [
        await customer(connection, sales) for _ in range(6)
    ]
    await formal_visit(connection, sales, old["id"], "2025-12-31")
    await formal_visit(connection, sales, boundary["id"], "2026-01-01")
    await formal_visit(connection, sales, first_half["id"], "2026-02-01")
    await formal_visit(connection, sales, current["id"], "2026-09-22")
    await formal_visit(connection, sales, future["id"], "2026-09-23")
    points = await CustomerMapRepository().read(connection, as_of=date(2026, 9, 22))
    ids = {r["id"] for r in points}
    assert {boundary["id"], first_half["id"], current["id"]} <= ids
    assert not {claimed["id"], old["id"], future["id"]} & ids
    # A calendar year restarts on January 1; it is not a rolling full year.
    next_year = await CustomerMapRepository().read(connection, as_of=date(2027, 1, 1))
    assert not {r["id"] for r in (claimed, old, boundary, first_half, current, future)} & {
        r["id"] for r in next_year
    }
    # Inactive archives remain searchable and retain the same raw historical date.
    customers = await CustomerRepository().list(connection, query=None, level=None, unassigned=None, limit=None)
    assert {claimed["id"], old["id"]} <= {r["id"] for r in customers}
    assets = CustomerAssetRepository()
    summary = (await assets.read(connection, customer_id=claimed["id"]))["summary"]
    assert summary["portfolio_customer_count"] == 1
    assert summary["customer_count"] == 0 and summary["entry_count"] == 0
    assert summary["acv_amount"] == 0


async def test_active_customer_count_requires_formal_undeleted_visits_and_deduplicates(connection):
    from datetime import date

    sales = await actor(connection, "XS001")
    active, pending, deleted = [await customer(connection, sales) for _ in range(3)]
    confirmed = await formal_visit(connection, sales, active["id"], "2026-01-01")
    await connection.execute(
        "UPDATE activity.visit SET status='confirmed' WHERE id=$1::uuid", confirmed["id"]
    )
    await formal_visit(connection, sales, active["id"], "2026-06-30")
    await formal_visit(connection, sales, active["id"], "2026-09-22")
    unfinished = await formal_visit(connection, sales, pending["id"], "2026-09-22")
    await connection.execute(
        "UPDATE activity.visit SET status='pending_confirm' WHERE id=$1::uuid", unfinished["id"]
    )
    removed = await formal_visit(connection, sales, deleted["id"], "2026-09-22")
    await connection.execute(
        "UPDATE activity.visit SET deleted_at=clock_timestamp() WHERE id=$1::uuid", removed["id"]
    )

    rows = await CustomerMapRepository().read(connection, as_of=date(2026, 9, 22))
    assert len([row for row in rows if row["id"] == active["id"]]) == 1
    assert not {pending["id"], deleted["id"]} & {row["id"] for row in rows}
    first_day = await CustomerMapRepository().read(connection, as_of=date(2026, 1, 1))
    assert active["id"] in {row["id"] for row in first_day}
    point = next(row for row in rows if row["id"] == active["id"])
    # A customer-only follow-up activates an unassessed customer without an opportunity.
    assert point["open_count"] == 0
    assert point["potential_score"] is None and point["relationship_score"] is None


async def test_active_window_honours_shanghai_midnight_boundaries(connection):
    from datetime import date, datetime

    sales = await actor(connection, "XS001")
    cases = [
        ("2025-12-31T15:59:59+00:00", False),
        ("2025-12-31T16:00:00+00:00", True),
        ("2026-09-22T15:59:59+00:00", True),
        ("2026-09-22T16:00:00+00:00", False),
    ]
    expected = {}
    for timestamp, included in cases:
        item = await customer(connection, sales)
        visit = await formal_visit(connection, sales, item["id"], "2026-09-22")
        await connection.execute(
            "UPDATE activity.visit SET interaction_at=$2 WHERE id=$1::uuid",
            visit["id"], datetime.fromisoformat(timestamp),
        )
        expected[item["id"]] = included
    ids = {row["id"] for row in await CustomerMapRepository().read(connection, as_of=date(2026, 9, 22))}
    assert {customer_id: customer_id in ids for customer_id in expected} == expected


async def test_portfolio_keeps_inactive_opportunities_and_unknown_amounts(connection):
    from datetime import date
    sales = await actor(connection, "XS001")
    c = await customer(connection, sales)
    op = await save_opportunity(connection, sales, customer_id=c["id"], data={
        "name": "未激活的已认领客户商机", "probability": 10, "amount": Decimal(60000),
        "expected_close_date": date(2027, 1, 5),
    })
    assert c["id"] not in {row["id"] for row in await CustomerMapRepository().read(connection)}
    repo = CustomerAssetRepository()
    for period in ("year", "all"):
        summary = (await repo.read(connection, customer_id=c["id"], period=period))["summary"]
        assert summary["portfolio_customer_count"] == 1 and summary["acv_amount"] == 60000
        assert summary["recognized_amount"] is None and summary["collection_amount"] is None
    await connection.execute("UPDATE crm.opportunity SET amount=NULL WHERE id=$1::uuid", op["id"])
    summary = (await repo.read(connection, customer_id=c["id"]))["summary"]
    assert summary["acv_amount"] == 0 and summary["unknown_acv_count"] == 1
    assert await connection.fetchval("SELECT amount FROM crm.opportunity WHERE id=$1::uuid", op["id"]) is None
