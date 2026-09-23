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
        "potential_score", "relationship_score", "quadrant_code", "quadrant_policy",
        "latest_visit_at", "weekly_follow_up_count", "risk_title", "risk_severity",
    )
    for row in new:
        assert {k: row[k] for k in display_fields} == {k: before[row["id"]][k] for k in display_fields}
        assert sorted(row["plan_close_dates"], key=str) == sorted(before[row["id"]]["plan_close_dates"], key=str)
        assert not {"attributes", "import_meta", "next_action", "demand_summary", "visits"} & row.keys()


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
    claimed, old, boundary, future = [await customer(connection, sales) for _ in range(4)]
    await formal_visit(connection, sales, old["id"], "2026-03-21")
    await formal_visit(connection, sales, boundary["id"], "2026-03-22")
    await formal_visit(connection, sales, future["id"], "2026-09-23")
    points = await CustomerMapRepository().read(connection, as_of=date(2026, 9, 22))
    ids = {r["id"] for r in points}
    assert boundary["id"] in ids
    assert not {claimed["id"], old["id"], future["id"]} & ids
    # Inactive archives remain searchable and retain the same raw historical date.
    customers = await CustomerRepository().list(connection, query=None, level=None, unassigned=None, limit=None)
    assert {claimed["id"], old["id"]} <= {r["id"] for r in customers}
    assets = CustomerAssetRepository()
    summary = (await assets.read(connection, customer_id=claimed["id"]))["summary"]
    assert summary["portfolio_customer_count"] == 1
    assert summary["customer_count"] == 0 and summary["entry_count"] == 0
    assert summary["acv_amount"] == 0


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
