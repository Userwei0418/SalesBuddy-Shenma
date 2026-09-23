from datetime import date
from decimal import Decimal

import pytest

from sales_backend.repositories.dashboard import DashboardRepository
from sales_backend.services.opportunities import save_opportunity
from tests.integration.test_opportunity_lifecycle import setup_customer

pytestmark = pytest.mark.asyncio


async def test_dashboard_uses_separate_quarter_plans_weighted_predictions_and_actuals(connection, sales_actor):
    _, customer = await setup_customer(connection, sales_actor)
    ids = {}
    for status, probability in [("open", 10), ("open", 30), ("won", 100), ("lost", 90)]:
        result = await save_opportunity(
            connection,
            sales_actor,
            customer_id=customer["id"],
            data={
                "name": f"{status}-{probability}",
                "status": status,
                "probability": probability,
                "closure_confirmed": True,
                "amount": Decimal(100000),
                "expected_close_date": date(2028, 1, 1),
                "quarterly_forecasts": [
                    {
                        "year": 2026,
                        "quarter": 4,
                        "recognized_amount": Decimal("100.05"),
                        "collection_amount": Decimal("0"),
                    }
                ],
            },
        )
        ids[(status, probability)] = result["id"]
    data = await DashboardRepository().load(connection, sales_actor)
    rows = {r["opportunity_id"]: r for r in data["quarter_forecasts"]}
    assert data["contract_version"] == 3
    for key in [("lost", 90)]:
        assert rows[ids[key]]["weighted_recognized_amount"] is None
        assert not rows[ids[key]]["forecast_eligible"]
    assert rows[ids[("open", 10)]]["weighted_recognized_amount"] == Decimal("10.01")
    assert rows[ids[("open", 10)]]["weighted_collection_amount"] == 0
    assert rows[ids[("open", 10)]]["forecast_eligible"]
    assert rows[ids[("open", 30)]]["weighted_recognized_amount"] == Decimal("30.02")
    assert rows[ids[("open", 30)]]["weighted_collection_amount"] == 0
    assert rows[ids[("won", 100)]]["weighted_recognized_amount"] == Decimal("100.05")
    assert all(r["recognized_amount"] == Decimal("100.05") and r["year"] == 2026 for r in rows.values())
    assert data["quarter_actuals"] == []  # A prediction never creates recognized income or cash receipts.


async def test_ten_percent_missing_plan_stays_unknown_and_filled_zero_stays_zero(connection, sales_actor):
    _, customer = await setup_customer(connection, sales_actor)
    common = dict(status="open", probability=10, amount=Decimal(100000),
                  expected_close_date=date(2028, 1, 1))
    missing = await save_opportunity(
        connection, sales_actor, customer_id=customer["id"],
        data=dict(common, name="early-no-plan", quarterly_forecasts=[]),
    )
    partial = await save_opportunity(
        connection, sales_actor, customer_id=customer["id"],
        data=dict(common, name="early-partial-plan", quarterly_forecasts=[
            dict(year=2028, quarter=1, recognized_amount=None, collection_amount=Decimal(0))]),
    )
    data = await DashboardRepository().load(connection, sales_actor)
    rows = {r["opportunity_id"]: r for r in data["quarter_forecasts"]}
    assert missing["id"] not in rows
    assert rows[partial["id"]]["weighted_recognized_amount"] is None
    assert rows[partial["id"]]["weighted_collection_amount"] == 0
    assert rows[partial["id"]]["forecast_eligible"]
