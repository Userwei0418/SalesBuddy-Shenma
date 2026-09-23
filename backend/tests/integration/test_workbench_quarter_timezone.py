from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from sales_backend.domain.reporting import quarter_window
from sales_backend.repositories.workbench import WorkbenchRepository
from sales_backend.services.opportunities import save_opportunity
from tests.integration.test_customer_assets import customer
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("instant", ["2026-09-30T16:00:00+00:00", "2026-12-31T16:00:00+00:00"])
async def test_workbench_quarter_is_independent_of_connection_timezone(connection, monkeypatch, instant):
    start, end = quarter_window(datetime.fromisoformat(instant))
    monkeypatch.setattr("sales_backend.repositories.workbench.quarter_window", lambda: (start, end))
    sales = await actor(connection, "XS001")
    c = await customer(connection, sales)
    before = await WorkbenchRepository().load(connection, sales)
    plans = [{"year": start.year, "quarter": (start.month - 1) // 3 + 1,
              "recognized_amount": 0, "collection_amount": 0}]
    await save_opportunity(connection, sales, customer_id=c["id"], data={
        "name": "上海季度首日项目", "amount": Decimal(120000), "probability": 50,
        "expected_close_date": start.date(),
        "quarterly_forecasts": plans,
    })
    for label, closed_at, amount in (
        ("季度前一瞬", start - timedelta(microseconds=1), 1000),
        ("季度首瞬", start, 2000),
    ):
        won = await save_opportunity(connection, sales, customer_id=c["id"], data={
            "name": label, "amount": Decimal(amount), "probability": 100, "status": "won",
            "expected_close_date": start.date(), "quarterly_forecasts": plans, "closure_confirmed": True,
        })
        await connection.execute(
            "UPDATE crm.opportunity SET closed_at=$2::timestamptz WHERE id=$1::uuid", won["id"], closed_at,
        )
    # Uses the production repository SQL/owner scope; no rewritten test-only SQL.
    await connection.execute("SET LOCAL TIME ZONE 'Asia/Shanghai'")
    shanghai = await WorkbenchRepository().load(connection, sales)
    await connection.execute("SET LOCAL TIME ZONE 'UTC'")
    utc = await WorkbenchRepository().load(connection, sales)
    keys = ("quarter_won_amount", "quarter_largest_won", "quarter_weighted_forecast")
    assert {k: shanghai["summary"][k] for k in keys} == {k: utc["summary"][k] for k in keys}
    assert utc["summary"]["quarter_weighted_forecast"] == before["summary"]["quarter_weighted_forecast"] + 60000
    assert utc["summary"]["quarter_won_amount"] == before["summary"]["quarter_won_amount"] + 2000
