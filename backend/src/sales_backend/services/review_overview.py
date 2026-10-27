"""Deterministic Review cards/details from one authorized, unpaged row set."""

from datetime import datetime
from decimal import Decimal, localcontext

from sales_backend.contracts.work_report_base import PeriodSelection
from sales_backend.contracts.work_report_overview import OverviewScopeSelection, OverviewSelection
from sales_backend.domain.work_report import SHANGHAI, WorkReportError, resolve_period
from sales_backend.domain.work_report_task_activity import STATUS_LABELS
from sales_backend.repositories import review_history as history_repository
from sales_backend.repositories import review_overview as repository
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.repositories.work_report_task_activity import read_task_activities
from sales_backend.services import work_report_access as access
from sales_backend.services.review_history import with_baseline
from sales_backend.services.work_report_overview import overview_material

METRICS = {
    "forecast_collection": ("collection_amount", "collection", "cash_collection_not_applicable"),
    "collection_ppl": ("collection_amount", "collection_ppl", "cash_collection_not_applicable"),
    "forecast_recognized": ("recognized_amount", "forecast_recognized", "tax_inclusive"),
}
ACTIVITIES = (
    "new_claimed_customers",
    "formal_visits",
    "new_opportunities",
    "lost_opportunities",
    "updated_opportunities",
    "completed_tasks",
    "pending_tasks",
)
UNAVAILABLE_FILTERS = {}


def amount(value):
    return None if value is None else format(value, ".2f")


def contribution(row, code):
    """None eligibility is unknown; excluded rows must not become missing money."""
    if row["status"] not in {"open", "won"}:
        return False, None, None
    field, _, _ = METRICS[code]
    if code == "forecast_collection":
        flag = row.get("collection_confidence")
        eligible = flag == "high" if flag in {"high", "low"} else None
    else:
        probability = row.get("probability")
        eligible = (
            Decimal(str(probability)) >= (30 if code == "collection_ppl" else 70) if probability is not None else None
        )
    if eligible is not True:
        return eligible, None, "unknown_eligibility" if eligible is None else None
    if row.get("currency") != "CNY":
        return True, None, "currency_unavailable"
    raw = row.get(field)
    if raw is None or not row.get("forecast_id"):
        return True, None, "missing_forecast_amount"
    return True, Decimal(str(raw)), None


def metric_cards(rows, targets, target_allowed, *, year, quarter, time_progress="0", scope_complete=True):
    cards = []
    # Deduplicate defensively, but conflicting source rows are an error rather
    # than an arbitrary choice. SQL should already have exactly one per ID.
    by_id = {}
    for row in rows:
        if row["id"] in by_id and by_id[row["id"]] != row:
            raise ValueError("同一商机的季度事实不一致")
        by_id[row["id"]] = row
    with localcontext() as ctx:
        ctx.prec = 80
        for code, (_, kind, tax) in METRICS.items():
            known, missing, unknown, refs, reasons = [], 0, 0, [], set()
            for row in by_id.values():
                eligible, value, reason = contribution(row, code)
                if eligible is False:
                    continue
                unknown += int(eligible is None)
                missing += int(eligible is True and value is None)
                if value is not None:
                    known.append(value)
                if reason:
                    reasons.add(reason)
                refs.append(
                    dict(
                        opportunity_id=row["id"],
                        opportunity_version=row["version_no"],
                        forecast_id=row.get("forecast_id"),
                        forecast_updated_at=row.get("forecast_updated_at"),
                        year=year,
                        quarter=quarter,
                        metric_code=code,
                        definition_version=1,
                    )
                )
            if not scope_complete:
                reasons.add("source_scope_not_fully_authorized")
            complete = not (missing or unknown) and scope_complete
            subtotal = sum(known, Decimal(0)) if known or complete else None
            value = subtotal if complete else None
            source_target = targets.get(kind)
            target = {
                "kind": kind,
                "id": None,
                "version_no": None,
                "value": None,
                "status": "no_target" if target_allowed else "permission_denied",
            }
            if source_target:
                target.update(source_target)
                # Single-scope target rows predate the multi-scope projection
                # and do not carry a status; an explicit status from a
                # multi-scope aggregate must remain intact.
                if "status" not in source_target:
                    target["status"] = "effective"
            target_coverage = target.get("coverage")
            denominator_text = target.get("value")
            target_is_partial = (
                target.get("status") != "permission_denied"
                and denominator_text is None
                and target_coverage == "partial"
                and target.get("known_subtotal") is not None
            )
            if target_is_partial:
                denominator_text = target.get("known_subtotal")
            numerator_text = value if value is not None else subtotal
            value_is_partial = value is None and subtotal is not None
            rate_basis = (
                "known_subtotals_and_targets" if target_is_partial and value_is_partial
                else "known_effective_targets" if target_is_partial
                else "known_subtotals" if value_is_partial
                else "effective_target"
            )
            denominator = Decimal(denominator_text) if denominator_text is not None else None
            # Positive targets only. No undefined or legacy-zero denominator.
            numerator = Decimal(numerator_text) if numerator_text is not None else None
            comparable = numerator is not None and denominator is not None and denominator > 0
            ratio = numerator / denominator if comparable else None
            cards.append(
                dict(
                    metric_code=code,
                    definition_version=1,
                    unit="元",
                    currency="CNY",
                    tax_basis=tax,
                    value=amount(value),
                    known_subtotal=amount(subtotal),
                    coverage="complete" if complete else "partial" if known else "unavailable",
                    missing_amount_count=missing,
                    unknown_eligibility_count=unknown,
                    missing_reasons=sorted(reasons),
                    target=target,
                    gap=amount(denominator - numerator) if comparable else None,
                    rate=format(ratio, ".8f") if comparable else None,
                    rate_basis=rate_basis if comparable else None,
                    status="incomplete"
                    if not complete
                    else "target_unavailable"
                    if not target_allowed
                    else "no_target"
                    if not comparable
                    else "expected_achieved"
                    if ratio >= 1
                    else "on_track"
                    if ratio >= Decimal(time_progress)
                    else "behind",
                    day_change=None,
                    baseline={
                        "value": None,
                        "status": "no_baseline",
                        "reason": "无覆盖相同指标定义、授权范围和资格字段的昨日可信基线",
                    },
                    source_refs=refs,
                )
            )
    return cards


def quarter_info(anchor, now):
    period = resolve_period(PeriodSelection(kind="quarter", anchor_date=anchor), now=now)
    with localcontext() as ctx:
        ctx.prec = 40
        elapsed = Decimal((anchor - period.start.date()).days + 1)
        length = Decimal((period.end_exclusive.date() - period.start.date()).days)
        progress = format(max(Decimal(0), min(Decimal(1), elapsed / length)), ".8f")
    return dict(
        year=period.start.year,
        quarter=(period.start.month - 1) // 3 + 1,
        start=period.start.isoformat(),
        end_exclusive=period.end_exclusive.isoformat(),
        time_progress=progress,
        basis="selected_calendar_day_inclusive",
        timezone="Asia/Shanghai",
    )


async def context(connection, actor, selection):
    await repository.assert_read_context(connection, actor)
    now = await connection.fetchval("SELECT transaction_timestamp()")
    selection = selection.model_copy(update={"anchor_date": selection.anchor_date or now.astimezone(SHANGHAI).date()})
    scope = await access.resolve_read_scope(
        connection,
        actor,
        OverviewScopeSelection(kind=selection.scope, member_id=selection.member_id, team_id=selection.team_id),
    )
    period = resolve_period(PeriodSelection(kind=selection.period_type, anchor_date=selection.anchor_date), now=now)
    return {"selection": selection, "scope": scope, "period": period, "data_as_of": now.isoformat()}, now


async def source_scope_allowed(connection, scope, permission):
    typ = "team" if scope.kind == "team" else "person"
    return bool(
        await connection.fetchval(
            "SELECT security.authorization_subject($1,$2,$3::uuid,$4::uuid)",
            permission,
            typ,
            str(scope.member_ids[0]) if typ == "person" else None,
            str(scope.team_id) if typ == "team" else None,
        )
    )


async def metric_scope_complete(connection, scope):
    # Record-level RLS still filters every row. Subject checks only establish
    # whether the visible subtotal can be compared to the full subject target.
    return await source_scope_allowed(connection, scope, "opportunity.read") and await source_scope_allowed(
        connection, scope, "customer.read"
    )


def availability(*, risk_allowed=True):
    return {
        key: {
            "available": key not in UNAVAILABLE_FILTERS and (key != "high_risk" or risk_allowed),
            "reason": UNAVAILABLE_FILTERS.get(key)
            or ("当前无风险读取权限" if key == "high_risk" and not risk_allowed else None),
        }
        for key in ("all", "not_updated", "stale", "high_risk", "key_collection")
    }


def business_updated_at(row):
    return max(t for t in (row.get("updated_at"), row.get("forecast_updated_at")) if t is not None)


def pool_rows(rows, selection, which, *, metric_code=None):
    if which in UNAVAILABLE_FILTERS:
        return []
    if metric_code:
        # Metric drilldown includes won and incomplete rows, exactly matching
        # the card population. Active pool alone uses status=open.
        return [r for r in rows if contribution(r, metric_code)[0] is not False]
    if which == "key_collection":
        return [
            r
            for r in rows
            if r["status"] in {"open", "won"}
            and r.get("collection_confidence") == "high"
            and r.get("collection_amount") is not None
            and Decimal(str(r["collection_amount"])) > 0
            and r.get("forecast_id")
        ]
    active = [r for r in rows if r["status"] == "open"]
    if which == "not_updated":
        return [r for r in active if r.get("updated_in_selected_day") is False]
    if which == "high_risk":
        return [r for r in active if r.get("high_risk")]
    if which == "stale":

        def stale(row):
            last = row.get("last_follow_up_at")
            last_day = last.astimezone(SHANGHAI).date() if last is not None else row.get("business_created_on")
            return last_day is not None and (selection.anchor_date - last_day).days >= 7

        return [r for r in active if stale(r)]
    return active


def opportunity_item(row):
    return {
        **{
            key: row.get(key)
            for key in (
                "id",
                "version_no",
                "name",
                "customer_id",
                "customer_name",
                "owner_id",
                "owner_name",
                "team_id",
                "status",
                "stage_code",
                "probability",
                "amount",
                "currency",
                "updated_at",
                "forecast_id",
                "forecast_updated_at",
                "collection_amount",
                "recognized_amount",
                "collection_confidence",
                "last_follow_up_at",
                "high_risk",
                "risk_id",
                "risk_title",
                "risk_description",
                "risk_severity",
                "business_created_on",
            )
        },
        "business_updated_at": business_updated_at(row),
        "updated_in_selected_day": row.get("updated_in_selected_day"),
        "stale_days": row.get("stale_days"),
        "stale_basis": row.get("stale_basis"),
        "actions": {
            "can_view": True,
            "can_follow_up": bool(row.get("can_follow_up")),
            "can_create_task": bool(row.get("can_create_task")),
        },
        "navigation": {"resource_type": "opportunity", "resource_id": row["id"], "customer_id": row["customer_id"]},
        "metric_contributions": {
            code: {"eligible": result[0], "value": amount(result[1]), "reason": result[2]}
            for code in METRICS
            for result in [contribution(row, code)]
        },
    }


def count_items(items, *, complete=True, reasons=()):
    return {
        "value": len(items) if complete else None,
        "known_count": len(items),
        "coverage": "complete" if complete else "partial" if items else "unavailable",
        "missing_reasons": list(reasons),
    }


async def activity_material(connection, actor, envelope, now):
    selection, scope, period = (envelope[k] for k in ("selection", "scope", "period"))
    old_selection = OverviewSelection(
        scope=OverviewScopeSelection(kind=selection.scope, member_id=selection.member_id, team_id=selection.team_id),
        period=PeriodSelection(kind=selection.period_type, anchor_date=selection.anchor_date),
    )
    facts, _ = await overview_material(
        connection,
        actor,
        old_selection,
        now=now,
        period=period,
        allow_partial_sources=True,
    )
    _, task_activity = await read_task_activities(connection, actor, period=period, scope=scope, data_as_of=now)
    updated, history = await repository.updated_opportunities(connection, scope, period=period)
    pending = [item.model_dump(mode="json") for item in task_activity.progressing_tasks]
    groups = {code: [item.model_dump(mode="json") for item in getattr(facts, code)] for code in ACTIVITIES[:4]}
    groups["formal_visits"] = await repository.formal_visits(connection, scope, period=period)
    groups.update(
        updated_opportunities=updated,
        completed_tasks=[item.model_dump(mode="json") for item in task_activity.completed_tasks],
        pending_tasks=pending,
    )
    counts = {code: getattr(facts.counts, code).model_dump() for code in ACTIVITIES[:4]}
    counts["formal_visits"] = count_items(groups["formal_visits"])
    counts["completed_tasks"] = task_activity.counts.completed_tasks.model_dump()
    counts["updated_opportunities"] = count_items(
        updated,
        complete=history.get("count_status", history["status"]) == "complete",
        reasons=[] if history.get("count_status", history["status"]) == "complete" else history["reason_codes"],
    )
    counts["updated_opportunities"]["history_coverage"] = history
    # Pending is explicitly current inventory, not a fabricated historical state.
    counts["pending_tasks"] = count_items(pending)
    for item in groups["pending_tasks"]:
        item["source_refs"] = [{"kind": "task", "id": item["id"], "version": item.get("version_no")}]
        item["basis"] = "current_pending_inventory"
        item["status_label"] = STATUS_LABELS[item["status"]]
        due_at = item.get("due_at")
        item["overdue"] = bool(due_at and (datetime.fromisoformat(due_at.replace("Z", "+00:00")) if isinstance(due_at, str) else due_at) < now)
    await repository.activity_extras(connection, groups, period=period)
    for key in ("new_opportunities", "lost_opportunities"):
        counts[key]["known_count"] = len(groups[key])
        if counts[key]["coverage"] == "complete":
            counts[key]["value"] = len(groups[key])
    # A narrower source grant is not evidence that the selected subject did no
    # work. Keep visible detail/count while marking the aggregate incomplete.
    # Opportunity actions INNER JOIN customers. Formal visits additionally
    # require linked opportunity access in authorization_visit. Task activity
    # LEFT JOINs both only for redacted metadata, so those grants do not gate
    # the task population or its completion-event evidence.
    for permission, codes in (
        (
            "customer.read",
            (
                "new_claimed_customers",
                "formal_visits",
                "new_opportunities",
                "lost_opportunities",
                "updated_opportunities",
            ),
        ),
        ("visit.read", ("formal_visits",)),
        ("opportunity.read", ("formal_visits", "new_opportunities", "lost_opportunities", "updated_opportunities")),
        ("task.read", ("completed_tasks", "pending_tasks")),
    ):
        if not await source_scope_allowed(connection, scope, permission):
            for code in codes:
                counts[code].update(value=None, coverage="partial" if groups[code] else "unavailable")
                counts[code]["missing_reasons"] = list(
                    dict.fromkeys([*counts[code]["missing_reasons"], "来源读取权限不覆盖所选统计范围"])
                )
    return (
        groups,
        counts,
        [dict(id=str(m.member_id), name=m.name, membership_state=m.membership_state) for m in facts.members],
    )


async def read_metric_projection(connection, actor, selection):
    """Reusable quarterly card projection; never reconstruct or sum KR targets.

    Requires an actor-scoped read-only repeatable-read connection. Department
    scope is intentionally absent from the existing report read selector.
    """
    envelope, now = await context(connection, actor, selection)
    complete = await metric_scope_complete(connection, envelope["scope"])
    quarter = quarter_info(envelope["selection"].anchor_date, now)
    rows = await repository.opportunity_rows(
        connection, actor, envelope["scope"], year=quarter["year"], quarter=quarter["quarter"]
    )
    target_rows, allowed = await repository.targets(
        connection, envelope["scope"], start=datetime.fromisoformat(quarter["start"]).date()
    )
    return {
        **envelope,
        "quarter": quarter,
        "metrics": metric_cards(
            rows,
            target_rows,
            allowed,
            year=quarter["year"],
            quarter=quarter["quarter"],
            time_progress=quarter["time_progress"],
            scope_complete=complete,
        ),
    }


async def read_overview(connection, actor, selection):
    envelope, now = await context(connection, actor, selection)
    complete = await metric_scope_complete(connection, envelope["scope"])
    quarter = quarter_info(envelope["selection"].anchor_date, now)
    rows = await repository.opportunity_rows(
        connection, actor, envelope["scope"], year=quarter["year"], quarter=quarter["quarter"]
    )
    target_rows, allowed = await repository.targets(
        connection, envelope["scope"], start=datetime.fromisoformat(quarter["start"]).date()
    )
    _, counts, members = await activity_material(connection, actor, envelope, now)
    effective = await AuthorizationRepository().effective(connection)
    filters = availability(risk_allowed=effective.allows("risk.read"))
    if not await source_scope_allowed(connection, envelope["scope"], "visit.read"):
        filters["stale"] = {"available": False, "reason": "跟进读取权限不覆盖所选统计范围"}
    await mark_day_updates(connection, envelope, rows, filters, now)
    for key, item in filters.items():
        if not item["available"]:
            item.update(coverage="unavailable", known_count=None)
            continue
        item.update(
            coverage="complete" if complete else "partial" if rows else "unavailable",
            known_count=len(pool_rows(rows, envelope["selection"], key)),
        )
        if not complete:
            item["reason"] = "来源读取权限不覆盖所选统计范围，仅返回可见明细"
    return {
        **envelope,
        "quarter": quarter,
        "metrics": await with_baseline(connection, metric_cards(
            rows, target_rows, allowed, year=quarter["year"], quarter=quarter["quarter"],
            time_progress=quarter["time_progress"], scope_complete=complete,
        ), [envelope["scope"]], quarter=quarter, selection=envelope["selection"], now=now),
        "activities": [
            {
                "code": code,
                "count": counts[code]["value"],
                **{k: counts[code][k] for k in ("known_count", "coverage", "missing_reasons")},
                "history_coverage": counts[code].get("history_coverage"),
            }
            for code in ACTIVITIES
        ],
        "pool_counts": {
            key: len(pool_rows(rows, envelope["selection"], key)) if value["available"] and complete else None
            for key, value in filters.items()
        },
        "filter_availability": filters,
        "members": members,
        "generation": {"existing_overview_endpoint": "/api/v1/work-reports/overview", "unchanged": True},
    }


async def read_activities(connection, actor, selection, *, activity, limit=50, offset=0):
    envelope, now = await context(connection, actor, selection)
    groups, counts, _ = await activity_material(connection, actor, envelope, now)
    items, count = groups[activity], counts[activity]
    return {
        **envelope,
        "activity": activity,
        "history_coverage": count.get("history_coverage"),
        "values_basis": "current_pending_inventory" if activity == "pending_tasks" else "selected_period_events",
        "items": items[offset : offset + limit],
        "total": count["value"],
        "known_total": count["known_count"],
        "coverage": count["coverage"],
        "missing_reasons": count["missing_reasons"],
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < len(items),
    }


async def mark_day_updates(connection, envelope, rows, filters, now):
    day = resolve_period(PeriodSelection(kind="day", anchor_date=envelope["selection"].anchor_date), now=now)
    captured = await history_repository.day_updates(connection, envelope["scope"], period=day)
    history = captured["history_coverage"]
    identifiers = set(captured["updated_ids"])
    for row in rows:
        # An observed field event proves change. Missing events do not prove
        # no change: current timestamps and V235 installation are insufficient.
        row["updated_in_selected_day"] = (True if row["id"] in identifiers
                                          else False if history["status"] == "complete" else None)
        last = row.get("last_follow_up_at")
        last_day = last.astimezone(SHANGHAI).date() if last is not None else row.get("business_created_on")
        row["stale_days"] = (envelope["selection"].anchor_date - last_day).days if last_day is not None else None
        row["stale_basis"] = "formal_interaction_at" if last else "business_created_on" if last_day else "unknown"
        if not filters["high_risk"]["available"]:
            row["high_risk"] = None
    filters["stale"].update(
        threshold_days=7,
        basis="Asia/Shanghai calendar dates",
        unknown_count=sum(r["status"] == "open" and r["stale_days"] is None for r in rows),
    )
    filters["not_updated"]["history_coverage"] = history
    if history["status"] != "complete":
        filters["not_updated"] = {
            "available": False, "reason": "当日变更记录覆盖不完整，不能证明没有更新",
            "history_coverage": history,
        }


async def read_opportunities(
    connection,
    actor,
    selection,
    *,
    filter="all",
    q=None,
    sort="last_follow_up_desc",
    metric_code=None,
    limit=50,
    offset=0,
):
    envelope, now = await context(connection, actor, selection)
    if metric_code is not None and (metric_code not in METRICS or filter != "all"):
        raise WorkReportError("WORK_REPORT_INVALID_INPUT", "指标下钻使用有效指标与all筛选", status=422)
    effective = await AuthorizationRepository().effective(connection)
    complete = await metric_scope_complete(connection, envelope["scope"])
    filters = availability(risk_allowed=effective.allows("risk.read"))
    if not await source_scope_allowed(connection, envelope["scope"], "visit.read"):
        filters["stale"] = {"available": False, "reason": "跟进读取权限不覆盖所选统计范围"}
    available = filters[filter]["available"]
    rows = []
    if available:
        quarter = quarter_info(envelope["selection"].anchor_date, now)
        rows = await repository.opportunity_rows(
            connection, actor, envelope["scope"], year=quarter["year"], quarter=quarter["quarter"]
        )
        await mark_day_updates(connection, envelope, rows, filters, now)
        available = filters[filter]["available"]
        if not available:
            rows = []
        rows = pool_rows(rows, envelope["selection"], filter, metric_code=metric_code)
    if q:
        needle = q.casefold()
        rows = [
            row
            for row in rows
            if any(needle in str(row.get(k) or "").casefold() for k in ("name", "customer_name", "owner_name"))
        ]
    # NULL last_follow_up stays last in both directions and IDs break ties.
    key = "updated_at" if sort == "updated_desc" else "last_follow_up_at"
    present = sorted(
        (r for r in rows if r.get(key) is not None),
        key=lambda r: (r[key], r["id"]),
        reverse=sort != "last_follow_up_asc",
    )
    missing = sorted((r for r in rows if r.get(key) is None), key=lambda r: r["id"])
    rows = present + missing
    return {
        **envelope,
        "filter": filter,
        "sort": sort,
        "metric_code": metric_code,
        "values_basis": "current_authorized_opportunity_snapshot",
        "filter_availability": filters,
        "items": [opportunity_item(r) for r in rows[offset : offset + limit]],
        "total": len(rows) if available and complete else None,
        "known_total": len(rows),
        "coverage": "complete" if available and complete else "partial" if available and rows else "unavailable",
        "missing_reasons": (["source_scope_not_fully_authorized"] if not complete else [])
        + ([] if available else [filters[filter]["reason"]]),
        "available": available,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < len(rows),
    }
