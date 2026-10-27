"""Narrow database projections; runtime roles never read internal snapshots or audit bodies."""
from sales_backend.db import json_value


async def day_updates(connection, scope, *, period):
    result = await connection.fetchval(
        "SELECT security.review_day_update_status($1,$2::uuid[],$3,$4)",
        "team" if scope.kind == "team" else "person",
        [str(scope.team_id)] if scope.kind == "team" else [str(v) for v in scope.member_ids],
        period.start, period.cutoff,
    )
    return json_value(result)


async def update_activity(connection, scope, *, period):
    result = await connection.fetchval(
        "SELECT security.review_update_activity($1,$2::uuid[],$3,$4)",
        "team" if scope.kind == "team" else "person",
        [str(scope.team_id)] if scope.kind == "team" else [str(v) for v in scope.member_ids],
        period.start, period.cutoff,
    )
    return json_value(result)


async def metric_baseline(connection, scopes, *, snapshot_date, year, quarter):
    kinds = {"team" if scope.kind == "team" else "person" for scope in scopes}
    if len(kinds) != 1:
        raise ValueError("历史基线只支持同类主体")
    kind = kinds.pop()
    subjects = sorted({str(scope.team_id) if kind == "team" else str(scope.member_ids[0]) for scope in scopes})
    result = await connection.fetchval(
        "SELECT security.review_metric_baseline($1,$2::uuid[],$3,$4,$5,$6)",
        kind, subjects, snapshot_date, year, quarter, 1,
    )
    return json_value(result)
