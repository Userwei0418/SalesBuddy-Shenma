"""Marketing counts and ranks share the database's complete permission-scoped cohort."""

from datetime import date, timedelta

from sales_backend.repositories.customer_assets import today
from sales_backend.repositories.rankings import ranking


async def efficiency_rankings(connection, actor):
    as_of = today()
    starts = {
        "week": as_of - timedelta(days=as_of.weekday()),
        "quarter": date(as_of.year, (as_of.month - 1) // 3 * 3 + 1, 1),
        "year": date(as_of.year, 1, 1),
        "all": date(1900, 1, 1),
    }
    result = {}
    for metric, periods in {
        "followup": ("week", "quarter", "year"),
        "customers": ("year", "all"),
        "opportunities": ("year", "all"),
    }.items():
        result[metric] = {}
        for period in periods:
            payload = await ranking(connection, metric, starts[period], as_of)
            result[metric][period] = [
                {**row, "value": int(row["value"]), "cohort": payload["cohort"]} for row in payload["rows"]
            ]
    return result
