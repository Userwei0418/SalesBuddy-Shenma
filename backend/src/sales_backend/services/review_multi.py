"""Union full authorized subject facts before counts, money, filters or paging."""

from copy import deepcopy
from datetime import datetime

from sales_backend.contracts.review_multi import ReviewSelectionV2
from sales_backend.contracts.review_overview import ReviewSelection
from sales_backend.contracts.review_scope import ReviewScopeSelectionV2
from sales_backend.contracts.work_report_base import PeriodSelection
from sales_backend.domain.review_aggregation import aggregate_targets
from sales_backend.domain.work_report import SHANGHAI, WorkReportError, resolve_period
from sales_backend.repositories import review_overview as repository
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.services import review_overview as core
from sales_backend.services.review_history import with_baseline
from sales_backend.services.review_scope import resolve_review_scope

GENERATION = {
    "available": True,
    "reason_code": "work_report_multi_scope_v1",
    "reason": "所选范围可通过工作报告入口生成并保存多主体报告",
}
BUSINESS_IDS = {
    "new_claimed_customers": "customer_id", "formal_visits": "id",
    "new_opportunities": "opportunity_id", "lost_opportunities": "opportunity_id",
    "updated_opportunities": "opportunity_id", "completed_tasks": "task_id", "pending_tasks": "task_id",
}


def _time(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))


def _union_list(left, right, field):
    if field == "member_ids":
        return sorted({str(value) for value in [*left, *right]})
    if field == "source_refs":
        result = []
        for value in [*left, *right]:
            if value not in result:
                result.append(deepcopy(value))
        return result
    identity = "member_id" if field == "owners" else "event_id" if field == "events" else "id"
    result = {}
    for value in [*left, *right]:
        key = str(value[identity])
        if key in result and result[key] != value:
            raise ValueError("同一事实标识的授权快照不一致")
        result[key] = deepcopy(value)
    values = [result[key] for key in sorted(result)]
    if field in {"events", "business_events"}:
        values.sort(key=lambda item: (_time(item["occurred_at"]), str(item[identity])))
    return values


def union_items(batches, *, identity="id"):
    """Keep each business record once while retaining every stable event/owner."""
    records = {}
    list_fields = {"member_ids", "owners", "source_refs", "events", "business_events", "opportunities"}
    for batch in batches:
        for raw in batch:
            item = deepcopy(raw)
            key = str(item[identity])
            item["id"] = key
            if key not in records:
                records[key] = item
                continue
            current = records[key]
            for field, value in item.items():
                if field in list_fields:
                    current[field] = _union_list(current.get(field, []), value, field)
                elif field == "event_count":
                    continue
                elif field == "occurred_at" and current.get(field) is not None and value is not None:
                    current[field] = max((current[field], value), key=_time)
                elif field not in current or current[field] is None:
                    current[field] = value
                elif value is not None and current[field] != value:
                    raise ValueError("同一业务记录的授权快照不一致: " + field)
            if "events" in current:
                current["event_count"] = len(current["events"])
    return [records[key] for key in sorted(records)]


def history_union(histories, *, has_records):
    histories = [value for value in histories if value is not None]
    if not histories:
        return None
    complete = all(value["status"] == "complete" for value in histories)
    starts = [value.get("complete_from") for value in histories]
    return dict(
        status="complete" if complete else "partial" if has_records else "unavailable",
        complete_from=max(starts, key=_time) if all(starts) else None,
        reason_codes=sorted({code for value in histories for code in value["reason_codes"]}),
        message="；".join(dict.fromkeys(value["message"] for value in histories)),
    )


def union_activities(materials):
    groups, counts = {}, {}
    for code in core.ACTIVITIES:
        rows = union_items([material[0][code] for material in materials], identity=BUSINESS_IDS[code])
        key = "due_at" if code == "pending_tasks" else "completed_at" if code == "completed_tasks" else "occurred_at"
        present = sorted((row for row in rows if row.get(key) is not None),
                         key=lambda row: (_time(row[key]), row["id"]), reverse=code != "pending_tasks")
        groups[code] = present + [row for row in rows if row.get(key) is None]
        values = [material[1][code] for material in materials]
        complete = all(value["coverage"] == "complete" for value in values)
        counts[code] = core.count_items(rows, complete=complete,
                                       reasons=sorted({r for value in values for r in value["missing_reasons"]}))
        counts[code]["history_coverage"] = history_union(
            [value.get("history_coverage") for value in values], has_records=bool(rows))
    members = {}
    for _, _, entries in materials:
        for member in entries:
            key = str(member["id"])
            old = members.get(key)
            if old and old.get("name") != member.get("name"):
                raise ValueError("同一成员的授权名称不一致")
            members[key] = dict(member)
            if old and old.get("membership_state") == "current":
                members[key]["membership_state"] = "current"
    return groups, counts, [members[key] for key in sorted(members)]


async def context(connection, actor, selection):
    selection = ReviewSelectionV2.model_validate(selection)
    resolver = await resolve_review_scope(connection, actor, selection.model_dump(
        include=set(ReviewScopeSelectionV2.model_fields), exclude_none=True))
    now = resolver.data_as_of
    selection = selection.model_copy(update={"anchor_date": selection.anchor_date or now.astimezone(SHANGHAI).date()})
    period = resolve_period(PeriodSelection(kind=selection.period_type, anchor_date=selection.anchor_date), now=now)
    envelope = dict(selection=selection, scope=resolver.scope, period=period, data_as_of=now.isoformat(),
                    generation=dict(GENERATION))
    singles = []
    for scope in resolver.legacy_scopes:
        old = ReviewSelection(scope=scope.kind, member_id=scope.member_id, team_id=scope.team_id,
                              anchor_date=selection.anchor_date, period_type=selection.period_type)
        singles.append(dict(selection=old, scope=scope, period=period, data_as_of=now.isoformat()))
    return envelope, singles, now


async def _activities(connection, actor, singles, now):
    materials = []
    for single in singles:
        materials.append(await core.activity_material(connection, actor, single, now))
    return union_activities(materials)


async def _opportunities(connection, actor, envelope, singles, now, *, include_targets=False):
    quarter = core.quarter_info(envelope["selection"].anchor_date, now)
    effective = await AuthorizationRepository().effective(connection)
    batches, filters_by_subject, subject_targets, completeness = [], [], [], []
    for single in singles:
        scope = single["scope"]
        completeness.append(await core.metric_scope_complete(connection, scope))
        filters = core.availability(risk_allowed=effective.allows("risk.read") and
                                    await core.source_scope_allowed(connection, scope, "risk.read"))
        if not await core.source_scope_allowed(connection, scope, "visit.read"):
            filters["stale"] = {"available": False, "reason": "跟进读取权限不覆盖所选统计范围"}
        rows = await repository.opportunity_rows(connection, actor, scope,
                                                 year=quarter["year"], quarter=quarter["quarter"])
        await core.mark_day_updates(connection, single, rows, filters, now)
        batches.append(rows)
        filters_by_subject.append(filters)
        if include_targets:
            targets, allowed = await repository.targets(
                connection, scope, start=datetime.fromisoformat(quarter["start"]).date())
            subject_targets.append(dict(scope_type="team" if scope.kind == "team" else "person",
                                        subject_id=str(scope.team_id if scope.kind == "team" else scope.member_ids[0]),
                                        allowed=allowed, targets=targets))
    rows = union_items(batches)
    complete = all(completeness)
    filters = {}
    for key in filters_by_subject[0]:
        components = [value[key] for value in filters_by_subject]
        available = all(value["available"] for value in components)
        filters[key] = dict(available=available, reason="；".join(dict.fromkeys(
            value["reason"] for value in components if value.get("reason"))) or None)
        if key == "stale":
            filters[key].update(threshold_days=7, basis="Asia/Shanghai calendar dates",
                                unknown_count=sum(row["status"] == "open" and row["stale_days"] is None
                                                  for row in rows))
        history = history_union([value.get("history_coverage") for value in components], has_records=bool(rows))
        if history is not None:
            filters[key]["history_coverage"] = history
        filters[key].update(
            coverage=("unavailable" if not available else
                      "complete" if complete else "partial" if rows else "unavailable"),
            known_count=len(core.pool_rows(rows, envelope["selection"], key)) if available else None,
        )
        if available and not complete:
            filters[key]["reason"] = "来源读取权限不覆盖所选统计范围，仅返回可见明细"
    if not filters["high_risk"]["available"]:
        for row in rows:
            row["high_risk"] = None
    if not filters["stale"]["available"]:
        # A visible last visit is evidence; absence under partial visit grants
        # cannot establish never-followed or a stale duration for the union.
        for row in rows:
            row.update(stale_days=None, stale_basis="unknown")
        filters["stale"]["unknown_count"] = sum(row["status"] == "open" for row in rows)
    return rows, filters, complete, quarter, subject_targets


def union_metric_cards(rows, subjects, *, quarter, complete):
    targets = aggregate_targets(subjects)
    cards = core.metric_cards(
        rows, targets,
        all(subject["allowed"] for subject in subjects), year=quarter["year"], quarter=quarter["quarter"],
        time_progress=quarter["time_progress"], scope_complete=complete)
    for card in cards:
        card["target"] = targets[core.METRICS[card["metric_code"]][1]]
    return cards


async def read_overview(connection, actor, selection):
    envelope, singles, now = await context(connection, actor, selection)
    rows, filters, complete, quarter, targets = await _opportunities(
        connection, actor, envelope, singles, now, include_targets=True)
    _, counts, members = await _activities(connection, actor, singles, now)
    return dict(
        **envelope, contract_version="review.overview.v2", quarter=quarter,
        metrics=await with_baseline(connection,
            union_metric_cards(rows, targets, quarter=quarter, complete=complete),
            [single["scope"] for single in singles], quarter=quarter, selection=envelope["selection"], now=now),
        activities=[dict(code=code, count=counts[code]["value"], **{
            key: counts[code][key] for key in ("known_count", "coverage", "missing_reasons", "history_coverage")
        }) for code in core.ACTIVITIES],
        pool_counts={key: value["known_count"] if value["available"] and complete else None
                     for key, value in filters.items()}, filter_availability=filters, members=members,
    )


async def read_activities(connection, actor, selection, *, activity, limit=50, offset=0):
    envelope, singles, now = await context(connection, actor, selection)
    groups, counts, _ = await _activities(connection, actor, singles, now)
    items, count = groups[activity], counts[activity]
    return dict(
        **envelope, contract_version="review.page.v2", activity=activity,
        history_coverage=count["history_coverage"],
        values_basis="current_pending_inventory" if activity == "pending_tasks" else "selected_period_events",
        items=items[offset:offset + limit], total=count["value"], known_total=count["known_count"],
        coverage=count["coverage"], missing_reasons=count["missing_reasons"],
        limit=limit, offset=offset, has_more=offset + limit < len(items),
    )


async def read_opportunities(connection, actor, selection, *, filter="all", q=None,
                             sort="last_follow_up_desc", metric_code=None, limit=50, offset=0):
    envelope, singles, now = await context(connection, actor, selection)
    if metric_code is not None and (metric_code not in core.METRICS or filter != "all"):
        raise WorkReportError("WORK_REPORT_INVALID_INPUT", "指标下钻使用有效指标与all筛选", status=422)
    rows, filters, complete, _, _ = await _opportunities(connection, actor, envelope, singles, now)
    available = filters[filter]["available"]
    rows = core.pool_rows(rows, envelope["selection"], filter, metric_code=metric_code) if available else []
    if q:
        rows = [row for row in rows if any(q.casefold() in str(row.get(key) or "").casefold()
                                         for key in ("name", "customer_name", "owner_name"))]
    key = "updated_at" if sort == "updated_desc" else "last_follow_up_at"
    present = sorted((row for row in rows if row.get(key) is not None),
                     key=lambda row: (row[key], row["id"]), reverse=sort != "last_follow_up_asc")
    rows = present + sorted((row for row in rows if row.get(key) is None), key=lambda row: row["id"])
    return dict(
        **envelope, contract_version="review.page.v2", filter=filter, sort=sort, metric_code=metric_code,
        values_basis="current_authorized_opportunity_snapshot", filter_availability=filters,
        items=[core.opportunity_item(row) for row in rows[offset:offset + limit]],
        total=len(rows) if available and complete else None, known_total=len(rows),
        coverage="complete" if available and complete else "partial" if available and rows else "unavailable",
        missing_reasons=(["source_scope_not_fully_authorized"] if not complete else []) +
                        ([] if available else [filters[filter]["reason"]]),
        available=available, limit=limit, offset=offset, has_more=offset + limit < len(rows),
    )
