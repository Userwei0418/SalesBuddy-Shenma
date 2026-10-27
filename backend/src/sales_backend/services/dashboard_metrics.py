"""Dashboard-scoped monitor, sharing Review arithmetic without its authority."""

from datetime import date, datetime
from decimal import Decimal, localcontext

from sales_backend.domain.dashboard_actuals import (
    ACTUAL_METRICS,
    COVERAGE_NOTE,
    actual_card,
    actual_summary,
    distinct_actuals,
)
from sales_backend.domain.review_aggregation import TARGET_KINDS as REVIEW_TARGET_KINDS
from sales_backend.domain.review_aggregation import aggregate_targets
from sales_backend.domain.work_report import SHANGHAI
from sales_backend.repositories import dashboard_metrics as repository
from sales_backend.repositories.dashboard_scope import resolve_selection
from sales_backend.services.review_overview import METRICS, amount, contribution, metric_cards

TARGET_KINDS = (*REVIEW_TARGET_KINDS, "recognized")

NO_BASELINE = dict(
    value=None, status="no_baseline", reason_code="dashboard_scope_no_comparable_baseline",
    reason="当前看板范围暂无可比的昨日快照",
)


class InvalidDashboardSelection(ValueError):
    """Only invalid public selection, never arbitrary data or SQL failures."""


def quarter_window(year, quarter, now):
    start = datetime(year, (quarter - 1) * 3 + 1, 1, tzinfo=SHANGHAI)
    end = datetime(year + 1, 1, 1, tzinfo=SHANGHAI) if quarter == 4 else datetime(
        year, quarter * 3 + 1, 1, tzinfo=SHANGHAI)
    with localcontext() as ctx:
        ctx.prec = 40
        length = Decimal((end.date() - start.date()).days)
        elapsed = Decimal((now.astimezone(SHANGHAI).date() - start.date()).days + 1)
        progress = format(max(Decimal(0), min(Decimal(1), elapsed / length)), ".8f")
    return dict(year=year, quarter=quarter, start=start.isoformat(), end_exclusive=end.isoformat(),
                time_progress=progress, basis="current_calendar_day_inclusive", timezone="Asia/Shanghai")


async def context(connection, actor, *, year, quarter, personal=False, member_id=None, team_groups=()):
    try:
        selected = await resolve_selection(connection, actor, personal=personal, member_id=member_id,
                                           team_groups=team_groups, permission="dashboard.read")
    except ValueError as exc:
        raise InvalidDashboardSelection from exc
    now = await connection.fetchval("SELECT transaction_timestamp()")
    kind = ("self" if selected.member_id == actor.user_id else "member") if selected.personal else (
        "team" if selected.team_ids is not None else "authorized")
    envelope = dict(
        data_as_of=now.isoformat(),
        selection=dict(personal=selected.personal, member_id=selected.member_id if selected.personal else None,
                       team_groups=list(selected.team_groups)),
        scope=dict(kind=kind, label={"self": "本人", "member": "所选成员", "team": "所选团队",
                                    "authorized": "当前授权范围"}[kind]),
        quarter=quarter_window(year, quarter, now),
    )
    return selected, envelope


def direct_targets(rows, allowed):
    result = {}
    for kind in TARGET_KINDS:
        target = rows.get(kind) if allowed else None
        result[kind] = dict(
            kind=kind, id=target["id"] if target else None,
            version_no=target["version_no"] if target else None,
            value=target["value"] if target else None, known_subtotal=target["value"] if target else None,
            note=target.get("note") if target else None,
            status="effective" if target else "no_target" if allowed else "permission_denied",
            coverage="complete" if target else "unavailable",
            aggregation="direct_effective_target", components=[],
        )
    return result


async def target_material(connection, selection, *, start):
    if selection.personal:
        rows, allowed = await repository.targets_for_subject(connection, "person", selection.member_id, start=start)
        return direct_targets(rows, allowed), allowed
    if selection.team_ids:
        subjects = []
        for ident in sorted(set(selection.team_ids)):
            rows, allowed = await repository.targets_for_subject(connection, "team", ident, start=start)
            subjects.append(dict(scope_type="team", subject_id=ident, targets=rows, allowed=allowed))
        return aggregate_targets(subjects, kinds=TARGET_KINDS), all(subject["allowed"] for subject in subjects)
    # An unfiltered restricted dashboard is not a company total. Do not replace
    # it with the sum of people/team targets or disclose the company target.
    company = await repository.subject_allowed(connection, "dashboard.read", "department")
    if not company:
        return direct_targets({}, False), False
    rows, allowed = await repository.targets_for_subject(connection, "department", None, start=start)
    return direct_targets(rows, allowed), allowed


async def material(connection, actor, **selection):
    selected, envelope = await context(connection, actor, **selection)
    rows = await repository.opportunity_rows(
        connection, actor, selected, year=envelope["quarter"]["year"], quarter=envelope["quarter"]["quarter"])
    # SQL is one row per opportunity; refuse conflicting facts rather than
    # arbitrarily choosing money, and keep details identical to card sources.
    distinct = {}
    for row in rows:
        if row["id"] in distinct and distinct[row["id"]] != row:
            raise RuntimeError("Conflicting dashboard opportunity facts")
        distinct[row["id"]] = row
    complete = await repository.source_scope_complete(connection, selected)
    return selected, envelope, list(distinct.values()), complete


async def overview(connection, actor, **selection):
    selected, envelope, rows, complete = await material(connection, actor, **selection)
    quarter = envelope["quarter"]
    targets, allowed = await target_material(connection, selected, start=date.fromisoformat(quarter["start"][:10]))
    cards = metric_cards(rows, targets, allowed, year=quarter["year"], quarter=quarter["quarter"],
                         time_progress=quarter["time_progress"], scope_complete=complete)
    for card in cards:
        card["target"] = targets[METRICS[card["metric_code"]][1]]
        card.update(day_change=None, baseline=dict(NO_BASELINE), basis="latest_sales_forecast",
                    target_basis=METRICS[card["metric_code"]][1])
    actual_rows, actual_complete = await actual_material(connection, actor, selected, quarter=quarter)
    cards.extend(actual_card(actual_rows, code, targets[kind], scope_complete=actual_complete,
                             time_progress=quarter["time_progress"], baseline=NO_BASELINE)
                 for code, (kind, _) in ACTUAL_METRICS.items())
    return dict(**envelope, metrics=cards)


def detail_item(row):
    fields = ("id", "name", "customer_id", "customer_name", "owner_id", "owner_name", "team_id", "status",
              "probability", "amount", "currency", "forecast_id", "collection_confidence",
              "collection_amount", "recognized_amount")
    return {**{key: row.get(key) for key in fields}, "metric_contributions": {
        code: dict(eligible=result[0], value=amount(result[1]), reason=result[2])
        for code in METRICS for result in [contribution(row, code)]
    }}


async def opportunities(connection, actor, *, metric_code, q=None, limit=20, offset=0, **selection):
    _, envelope, rows, complete = await material(connection, actor, **selection)
    rows = [row for row in rows if contribution(row, metric_code)[0] is not False]
    if q and q.strip():
        needle = q.strip().casefold()
        rows = [row for row in rows if any(needle in str(row.get(key) or "").casefold()
                for key in ("name", "customer_name", "owner_name"))]
    rows.sort(key=lambda row: (row["name"].casefold(), row["id"]))
    return dict(
        **envelope, metric_code=metric_code,
        items=[detail_item(row) for row in rows[offset:offset + limit]],
        total=len(rows) if complete else None, known_total=len(rows),
        coverage="complete" if complete else "partial" if rows else "unavailable",
        missing_reasons=[] if complete else ["source_scope_not_fully_authorized"],
        limit=limit, offset=offset, has_more=offset + limit < len(rows),
    )


async def actual_material(connection, actor, selected, *, quarter):
    rows = await repository.actual_rows(connection, actor, selected, year=quarter["year"], quarter=quarter["quarter"])
    complete = await repository.source_scope_complete(connection, selected, actual=True)
    return distinct_actuals(rows), complete


async def actuals(connection, actor, *, metric_code, q=None, limit=20, offset=0, **selection):
    selected, envelope = await context(connection, actor, **selection)
    rows, complete = await actual_material(connection, actor, selected, quarter=envelope["quarter"])
    kind, _ = ACTUAL_METRICS[metric_code]
    rows = [row for row in rows if row["kind"] == kind]
    if q and q.strip():
        needle = q.strip().casefold()
        rows = [row for row in rows if any(needle in str(row.get(key) or "").casefold()
                for key in ("opportunity_name", "customer_name", "owner_name"))]
    rows.sort(key=lambda row: (row["customer_name"].casefold(), (row["opportunity_name"] or "").casefold(), row["id"]))
    summary = actual_summary(rows, metric_code, scope_complete=complete)
    rows = [row for row in rows if not row.get("_attribution_excluded")]
    return dict(**envelope, metric_code=metric_code, items=rows[offset:offset + limit],
                attribution_unknown_period_count=summary["attribution_unknown_period_count"],
                total=len(rows) if complete else None, known_total=len(rows), coverage=summary["coverage"],
                missing_reasons=summary["missing_reasons"], coverage_note=COVERAGE_NOTE,
                limit=limit, offset=offset, has_more=offset + limit < len(rows))
