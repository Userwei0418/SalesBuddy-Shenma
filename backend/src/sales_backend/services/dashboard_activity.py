"""Seven Shanghai calendar days over the selected authorized opportunity set."""

from datetime import datetime, time, timedelta

from sales_backend.domain.work_report import SHANGHAI
from sales_backend.repositories import dashboard_activity as repository
from sales_backend.repositories.dashboard_metrics import source_scope_complete
from sales_backend.services.dashboard_metrics import context

BASIS = "quarter_positive_sales_forecast"


def activity_window(now):
    local = now.astimezone(SHANGHAI)
    start = datetime.combine(local.date() - timedelta(days=6), time.min, SHANGHAI)
    end = datetime.combine(local.date() + timedelta(days=1), time.min, SHANGHAI)
    return dict(start=start.isoformat(), end_exclusive=end.isoformat(), as_of=now.isoformat(),
                days=7, timezone="Asia/Shanghai")


def follow_up_state(value, *, now, readable=True):
    """Future/invalid values cannot masquerade as a current follow-up."""
    if value is None:
        return ("never" if readable else "unknown"), None
    try:
        value = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(value, datetime) or value.tzinfo is None or value > now:
            return "unknown", None
        days = (now.astimezone(SHANGHAI).date() - value.astimezone(SHANGHAI).date()).days
    except (ValueError, TypeError, OverflowError):
        return "unknown", None
    if days < 7:
        return "recent", days
    return ("overdue" if readable else "unknown"), days


def classify(rows, *, now):
    result = []
    for row in rows:
        state, days = follow_up_state(row.get("last_follow_up_at"), now=now,
                                     readable=row.get("follow_up_complete") is True)
        result.append(dict(row, follow_up_status=state, days_since_follow_up=days))
    return result


def coverage(rows, scope_complete):
    reasons = [] if scope_complete else ["source_scope_not_fully_authorized"]
    if any(row["follow_up_status"] == "unknown" for row in rows):
        reasons.append("follow_up_visibility_incomplete")
    return dict(coverage="complete" if not reasons else "partial" if rows else "unavailable",
                missing_reasons=reasons)


async def material(connection, actor, **selection):
    selected, envelope = await context(connection, actor, **selection)
    now = datetime.fromisoformat(envelope["data_as_of"])
    rows = await repository.activity_rows(connection, actor, selected,
        year=envelope["quarter"]["year"], quarter=envelope["quarter"]["quarter"], now=now)
    distinct = {}
    for row in rows:
        if row["id"] in distinct and distinct[row["id"]] != row:
            raise RuntimeError("Conflicting dashboard activity facts")
        distinct[row["id"]] = row
    complete = await source_scope_complete(connection, selected)
    return envelope, classify(list(distinct.values()), now=now), complete, activity_window(now)


async def overview(connection, actor, **selection):
    envelope, rows, complete, window = await material(connection, actor, **selection)
    counts = {state: sum(row["follow_up_status"] == state for row in rows)
              for state in ("recent", "overdue", "never", "unknown")}
    return dict(**envelope, basis=BASIS, window=window, **coverage(rows, complete),
                active_count=len(rows), recent_count=counts["recent"],
                overdue_count=counts["overdue"] + counts["never"], never_count=counts["never"],
                unknown_count=counts["unknown"])


def detail(row):
    fields = ("id", "name", "customer_id", "customer_name", "owner_id", "owner_name", "team_id",
              "stage_code", "probability", "status", "days_since_follow_up", "follow_up_status", "currency")
    last = row.get("last_follow_up_at")
    is_cny = row.get("currency") == "CNY"
    return dict(**{key: row.get(key) for key in fields}, can_read=True,
                last_follow_up_at=last.isoformat() if isinstance(last, datetime) else last,
                collection_amount=row.get("collection_amount") if is_cny else None,
                recognized_amount=row.get("recognized_amount") if is_cny else None,
                amount_reason=None if is_cny else "currency_unavailable")


async def opportunities(connection, actor, *, q=None, limit=20, offset=0, **selection):
    envelope, all_rows, complete, window = await material(connection, actor, **selection)
    scope = coverage(all_rows, complete)
    rows = [row for row in all_rows if row["follow_up_status"] in {"never", "overdue"}]
    if q and q.strip():
        needle = q.strip().casefold()
        rows = [row for row in rows if any(needle in str(row.get(key) or "").casefold()
                for key in ("name", "customer_name", "owner_name"))]
    # Never first, then oldest known date; UUID breaks every date/name tie.
    rows.sort(key=lambda row: (row.get("last_follow_up_at") is not None,
                              str(row.get("last_follow_up_at") or ""), row["id"]))
    return dict(**envelope, basis=BASIS, window=window, **scope,
                items=[detail(row) for row in rows[offset:offset + limit]],
                total=len(rows) if scope["coverage"] == "complete" else None, known_total=len(rows),
                limit=limit, offset=offset, has_more=offset + limit < len(rows))
