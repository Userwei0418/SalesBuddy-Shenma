"""Deterministic meeting facts. No DB, model, clock, permission or guessed history."""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, localcontext
from uuid import UUID, uuid5

from sales_backend.contracts.work_report_meeting import MeetingFacts, MeetingMoneyChange, MeetingScheme
from sales_backend.domain.opportunity_history import canonical
from sales_backend.domain.work_report import canonical_hash

NAMESPACE = UUID("a309b497-20c5-43b4-84f7-dd90a06e1187")
METRICS = ("acv", "quarter_revenue", "quarter_collection")
PERIOD_NAMES = {"day": "日会", "week": "周会", "two_weeks": "双周会", "month": "月度会议", "quarter": "季度会议"}
SECTIONS = ("overview", "changes", "customers", "opportunities", "followups", "next_steps")
ABSENT = {"absent": True, "owner_id": None, "team_id": None, "status": "absent", "amount": "0", "currency": "CNY"}


def schemes():
    return [
        MeetingScheme(
            builtin_id=f"meeting.{kind}.v1", period_kind=kind, name=f"{name}工作看板", section_ids=list(SECTIONS)
        )
        for kind, name in PERIOD_NAMES.items()
    ]


def amount_text(value):
    return None if value is None else format(value, "f") if value.as_tuple().exponent < -2 else format(value, ".2f")


def money(opening, closing):
    """Each endpoint is an independently deduplicated list; None is a missing fact."""
    with localcontext() as ctx:
        ctx.prec = 80

        def total(values):
            known = [Decimal(str(v)) for v in values if v is not None]
            subtotal = sum(known, Decimal(0)) if known or not values else None
            complete = len(known) == len(values)
            return (
                subtotal if complete else None,
                subtotal,
                "complete" if complete else "partial" if known else "unavailable",
            )

        left, subtotal_left, coverage_left = total(opening)
        right, subtotal_right, coverage_right = total(closing)
        delta = right - left if left is not None and right is not None else None
        # Net known subtotal needs the SAME population at both endpoints. A pair
        # with one missing endpoint cannot contribute a fabricated partial net.
        pairs = [(a, b) for a, b in zip(opening, closing, strict=True) if a is not None and b is not None]
        partial_delta = sum((Decimal(str(b)) - Decimal(str(a)) for a, b in pairs), Decimal(0)) if pairs else None
        if delta is not None:
            partial_delta = delta
        return MeetingMoneyChange(
            opening=amount_text(left),
            closing=amount_text(right),
            change=amount_text(delta),
            known_subtotals=dict(
                opening=amount_text(subtotal_left),
                closing=amount_text(subtotal_right),
                change=amount_text(partial_delta),
            ),
            coverage=dict(
                opening=coverage_left,
                closing=coverage_right,
                change="complete" if delta is not None else "partial" if pairs else "unavailable",
            ),
            missing_reasons=[] if delta is not None else ["部分边界归属、金额或历史不可得；未知不作为零计入"],
        )


def current_state(opportunity):
    return {key: opportunity.get(key) for key in ("amount", "currency", "status", "owner_id", "team_id")}


def same_state(left, right):
    if left is None or right is None:
        return left is right

    def normalized(value):
        result = canonical(value)
        for key in ("amount", "recognized_amount", "collection_amount"):
            if result.get(key) is not None:
                result[key] = Decimal(str(result[key]))
        return result

    return normalized(left) == normalized(right)


def state_at(opportunity, boundary, since):
    """Reverse only complete typed images after a known collection boundary."""
    events = sorted(opportunity["events"], key=lambda e: e["after_version"], reverse=True)
    native = next(
        (
            e
            for e in events
            if e["history_origin"] == "native_creation"
            and e["mutation_kind"] == "insert"
            and e["complete_from_origin"]
            and e["source_kind"] == "native_application"
        ),
        None,
    )
    if native and boundary <= native["occurred_at"]:
        return deepcopy(ABSENT)
    if since is None or boundary < since:
        return None
    state, version = current_state(opportunity), opportunity["version_no"]
    for event in events:
        if event["occurred_at"] < boundary:
            break
        if (
            not event.get("meeting_state_captured")
            or event["after_version"] != version
            or not same_state(state, event.get("meeting_after"))
        ):
            return None
        if event["mutation_kind"] == "insert":
            # An imported insert does not prove historical non-existence.
            return deepcopy(ABSENT) if event is native else None
        state, version = deepcopy(event["meeting_before"]), event["before_version"]
    return state


def forecast_at(opportunity, boundary, since, quarter):
    if since is None or boundary < since:
        return None
    state = deepcopy(next((row for row in opportunity["forecasts"] if (row["year"], row["quarter"]) == quarter), None))
    if state is not None:
        state = {key: state[key] for key in ("recognized_amount", "collection_amount")}
    events = sorted(
        (e for e in opportunity["forecast_events"] if (e["year"], e["quarter"]) == quarter),
        key=lambda e: (e.get("event_no", 0), e["recorded_at"], str(e["id"])),
        reverse=True,
    )
    for event in events:
        if event["recorded_at"] < boundary:
            break
        if not same_state(state, event["after_state"]):
            return None
        state = deepcopy(event["before_state"])
    # No forecast row means unfilled, not a confirmed zero.
    return state


def in_scope(state, scope, member_id=None):
    if state is None:
        return None
    if state.get("absent"):
        return False
    if member_id is not None:
        return str(state.get("owner_id")) == str(member_id) and (
            scope.kind == "self" or str(state.get("team_id")) == str(scope.team_id)
        )
    if scope.kind == "self":
        return str(state.get("owner_id")) == str(scope.member_ids[0])
    return str(state.get("team_id")) == str(scope.team_id)


def contribution(state, forecast, scope, metric, member_id=None):
    included = in_scope(state, scope, member_id)
    if included is False or (state is not None and state.get("status") in {"lost", "cancelled"}):
        return Decimal(0)
    if included is None or state.get("currency") != "CNY":
        return None
    value = (
        state.get("amount")
        if metric == "acv"
        else (forecast or {}).get("recognized_amount" if metric == "quarter_revenue" else "collection_amount")
    )
    return Decimal(str(value)) if value is not None else None


def count(rows, *, complete=True):
    return dict(
        value=len(rows) if complete else None,
        known_count=len(rows),
        coverage="complete" if complete else "partial" if rows else "unavailable",
        missing_reasons=[] if complete else ["缺少完整历史归属或日期，已知明细仅为部分记录"],
    )


def assemble(bundle, *, period, scope, data_as_of):
    """bundle is server-collected current-authority material, never client JSON."""
    stamp = period.cutoff - timedelta(microseconds=1) if period.cutoff == period.end_exclusive else period.cutoff
    quarter = (stamp.year, (stamp.month - 1) // 3 + 1)
    since = bundle["history_since"]
    members = {str(row["id"]): row for row in bundle["members"]}
    sources, dependencies = {}, {}

    def source(kind, row, *, at=None, dependency=None):
        ident = str(row["id"])
        key = f"{kind}:{ident}"
        sources[key] = dict(
            source_id=key,
            kind=kind,
            record_id=ident,
            version=str(row["version_no"]) if row.get("version_no") is not None else None,
            observed_at=data_as_of,
            business_at=at,
            source_hash=canonical_hash(canonical(row)),
        )
        if dependency is not None:
            dependencies[canonical_hash(dependency)] = dependency
        return key

    def people(ids):
        return sorted({str(i) for i in ids if i is not None and str(i) in members})

    def linked_dependencies(row):
        # Labels and navigation targets can remain in a report even when the
        # linked opportunity is outside its monetary population. Recheck each
        # referenced object independently of the visit/task owner's permission.
        for key, kind in (("customer_id", "meeting_customer"), ("opportunity_id", "opportunity")):
            if row.get(key) is not None:
                dependency = dict(source_type=kind, record_id=str(row[key]))
                dependencies[canonical_hash(dependency)] = dependency

    def item(kind, row, refs, who, at):
        return dict(
            id=uuid5(NAMESPACE, f"{kind}:{row['id']}"),
            name=row["name"],
            customer_id=row.get("customer_id"),
            customer_name=row.get("customer_name"),
            opportunity_id=row.get("opportunity_id"),
            opportunity_name=row.get("opportunity_name"),
            occurred_at=at,
            member_ids=people(who),
            summary=row.get("summary") or "",
            source_refs=refs,
        )

    groups = {key: [] for key in ("new_claimed_customers", "formal_visits", "new_opportunities", "lost_opportunities")}
    next_steps = []
    for row in bundle["claims"]:
        linked_dependencies(row)
        ref = source(
            "customer_claim",
            row,
            at=row["occurred_at"],
            dependency=dict(source_type="meeting_claim", record_id=str(row["id"]), customer_id=str(row["customer_id"])),
        )
        existing = next(
            (r for r in groups["new_claimed_customers"] if str(r["customer_id"]) == str(row["customer_id"])), None
        )
        if existing:
            existing["source_refs"].append(ref)
            existing["member_ids"] = people([*existing["member_ids"], row["owner_id"]])
        else:
            groups["new_claimed_customers"].append(item("claim", row, [ref], [row["owner_id"]], row["occurred_at"]))
    for row in bundle["visits"]:
        linked_dependencies(row)
        ref = source(
            "visit", row, at=row["interaction_at"], dependency=dict(source_type="visit", record_id=str(row["id"]))
        )
        groups["formal_visits"].append(item("visit", row, [ref], row["member_ids"], row["interaction_at"]))
        if row.get("next_action"):
            next_steps.append(
                dict(
                    id=f"visit:{row['id']}:next",
                    text=row["next_action"],
                    opportunity_id=row.get("opportunity_id"),
                    member_ids=people(row["member_ids"]),
                    due_at=None,
                    original_date_text=row["next_action"] if len(row["next_action"]) <= 240 else None,
                    source_refs=[ref],
                )
            )
    for row in bundle["tasks"]:
        linked_dependencies(row)
        ref = source("task", row, dependency=dict(source_type="task", record_id=str(row["id"])))
        next_steps.append(
            dict(
                id=f"task:{row['id']}",
                text=row["title"],
                opportunity_id=row.get("opportunity_id"),
                member_ids=people(row["member_ids"]),
                due_at=row["due_at"],
                original_date_text=None,
                source_refs=[ref],
            )
        )

    population = []
    new_complete = True
    for row in bundle["opportunities"]:
        left = state_at(row, period.start, since)
        right = state_at(row, period.cutoff, since)
        # Known historic transfer-outs remain in the population. Current broad
        # read grants cannot add unrelated departments to the generated facts.
        period_events = [e for e in row["events"] if period.start <= e["occurred_at"] < period.cutoff]
        candidates = [
            left,
            right,
            *(e.get("meeting_before") for e in period_events),
            *(e.get("meeting_after") for e in period_events),
        ]
        # A known post-period creation or an earlier transfer out does not
        # belong on this period's board. For incomplete history retain the
        # currently relevant cohort with explicitly unknown endpoints.
        if left is None or right is None:
            candidates.append(current_state(row))
        if not any(in_scope(s, scope) is True for s in candidates):
            continue
        linked_dependencies(row)
        ref = source("opportunity", row, dependency=dict(source_type="opportunity", record_id=str(row["id"])))
        refs = [ref]
        for event in row["events"]:
            refs.append(
                source(
                    "opportunity_history",
                    event,
                    at=event["occurred_at"],
                    dependency=dict(
                        source_type="meeting_state", record_id=str(event["id"]), opportunity_id=str(row["id"])
                    ),
                )
            )
        for event in row["forecast_events"]:
            if (event["year"], event["quarter"]) != quarter:
                continue
            refs.append(
                source(
                    "forecast_history",
                    event,
                    at=event["recorded_at"],
                    dependency=dict(
                        source_type="meeting_forecast", record_id=str(event["id"]), opportunity_id=str(row["id"])
                    ),
                )
            )
        lf = forecast_at(row, period.start, since, quarter)
        rf = forecast_at(row, period.cutoff, since, quarter)
        population.append((row, left, right, lf, rf, refs))
        creation = row.get("business_created_at")
        if creation is None:
            new_complete = False
        elif period.start <= creation < period.cutoff:
            origin = next(
                (e for e in row["events"] if e["mutation_kind"] == "insert" and e.get("meeting_state_captured")), None
            )
            owner_state = origin["meeting_after"] if origin else None
            if owner_state is None:
                new_complete = False
            elif in_scope(owner_state, scope):
                display = {**row, "opportunity_id": row["id"], "opportunity_name": row["name"]}
                groups["new_opportunities"].append(item("new", display, refs, [owner_state["owner_id"]], creation))
        for event in row["events"]:
            if (
                period.start <= event["occurred_at"] < period.cutoff
                and event["after_status"] == "lost"
                and event["before_status"] != "lost"
                and in_scope(event.get("meeting_after"), scope)
                and event.get("source_kind") != "historical_import"
            ):
                if not any(str(r["opportunity_id"]) == str(row["id"]) for r in groups["lost_opportunities"]):
                    display = {**row, "opportunity_id": row["id"], "opportunity_name": row["name"]}
                    groups["lost_opportunities"].append(
                        item("lost", display, refs, [event["meeting_after"]["owner_id"]], event["occurred_at"])
                    )

    def amounts_and_changes(member_id=None):
        totals = {m: ([], []) for m in METRICS}
        changes = []
        for row, left, right, lf, rf, refs in population:
            relevant = (
                member_id is None
                or left is None
                or right is None
                or any(str(s.get("owner_id")) == member_id for s in (left, right) if s)
            )
            if not relevant:
                continue
            amounts = {}
            for metric in METRICS:
                a, b = (
                    contribution(left, lf, scope, metric, member_id),
                    contribution(right, rf, scope, metric, member_id),
                )
                totals[metric][0].append(a)
                totals[metric][1].append(b)
                amounts[metric] = money([a], [b])
            reasons = []
            if left is None or right is None:
                reasons.append(dict(code="history_gap", text="边界历史不可得，净变化未计算", source_refs=refs))
            elif in_scope(left, scope, member_id) != in_scope(right, scope, member_id):
                direction = in_scope(right, scope, member_id)
                reasons.append(
                    dict(
                        code="scope_transfer_in" if direction else "scope_transfer_out",
                        text="归属转入本范围" if direction else "归属转出本范围",
                        source_refs=refs,
                    )
                )
            if left and right:
                if left.get("owner_id") != right.get("owner_id") and not left.get("absent"):
                    reasons.append(dict(code="ownership_change", text="已登记商机负责人变化", source_refs=refs))
                if left.get("absent"):
                    reasons.append(dict(code="new_opportunity", text="本期新建商机", source_refs=refs))
                elif right["status"] == "lost" and left["status"] != "lost":
                    reasons.append(dict(code="lost", text="本期丢单，移出当前预计值", source_refs=refs))
                elif left["status"] in {"lost", "cancelled"} and right["status"] == "open":
                    reasons.append(dict(code="reopened", text="本期重新打开", source_refs=refs))
                if left.get("amount") != right.get("amount"):
                    reasons.append(dict(code="amount_change", text="已登记ACV变更", source_refs=refs))
                if lf is not None and rf is not None and not same_state(lf, rf):
                    reasons.append(dict(code="forecast_change", text="已登记季度预测变更", source_refs=refs))
            if any(m.change is None for m in amounts.values()) and not any(r["code"] == "history_gap" for r in reasons):
                reasons.append(
                    dict(code="history_gap", text="部分金额或季度预测边界不可得，未推断具体变化", source_refs=refs)
                )
            changes.append(
                dict(
                    opportunity_id=row["id"],
                    name=row["name"],
                    customer_id=row["customer_id"],
                    customer_name=row.get("customer_name"),
                    amounts=amounts,
                    opening_member_ids=people([left["owner_id"]]) if left else [],
                    closing_member_ids=people([right["owner_id"]]) if right else [],
                    reasons=reasons,
                    follow_up_ids=[
                        r["id"] for r in groups["formal_visits"] if str(r.get("opportunity_id")) == str(row["id"])
                    ],
                    next_step_ids=[r["id"] for r in next_steps if str(r.get("opportunity_id")) == str(row["id"])],
                    source_refs=refs,
                )
            )
        # Prior to this migration, even an empty current cohort cannot prove no
        # transfers out. Keep the total coverage honest without inventing rows.
        if since is None or period.start < since:
            for left_values, right_values in totals.values():
                left_values.append(None)
                right_values.append(Decimal(0) if since and period.cutoff >= since else None)
        return {m: money(*totals[m]) for m in METRICS}, changes

    amounts, changes = amounts_and_changes()
    flags = dict(
        new_claimed_customers=True,
        formal_visits=True,
        new_opportunities=new_complete,
        lost_opportunities=since is not None
        and period.start >= since
        and all(left is not None and right is not None for _, left, right, *_ in population),
    )
    member_facts = []
    for ident, member in members.items():
        own_groups = {k: [r for r in rows if ident in r["member_ids"]] for k, rows in groups.items()}
        own_amounts, own_changes = amounts_and_changes(ident)
        member_facts.append(
            dict(
                member_id=ident,
                name=member["name"],
                membership_state=member["membership_state"],
                missing_reasons=[] if member["name"] else ["历史成员姓名当前不可得"],
                counts={k: count(v, complete=flags[k]) for k, v in own_groups.items()},
                amounts=own_amounts,
                opportunity_ids=[c["opportunity_id"] for c in own_changes],
                opportunity_changes=own_changes,
                **{
                    target: [r["id"] for r in own_groups[key]]
                    for key, target in (
                        ("new_claimed_customers", "new_claimed_customer_ids"),
                        ("formal_visits", "formal_visit_ids"),
                        ("new_opportunities", "new_opportunity_ids"),
                        ("lost_opportunities", "lost_opportunity_ids"),
                    )
                },
                next_step_ids=[r["id"] for r in next_steps if ident in r["member_ids"]],
            )
        )
        dep = dict(source_type="meeting_member", record_id=ident)
        dependencies[canonical_hash(dep)] = dep
    missing = [] if since and period.start >= since else ["金额与归属历史启用前的期初不可证，保留已知期末及明细"]
    if any(
        s and in_scope(s, scope) and s.get("owner_id") and str(s["owner_id"]) not in members
        for _, left, right, _, _, _ in population
        for s in (left, right)
    ):
        missing.append("部分边界成员当前不可访问，未展示其身份或成员明细")
    facts = MeetingFacts(
        period=period,
        data_as_of=data_as_of,
        quarter=dict(year=quarter[0], quarter=quarter[1]),
        scope=scope,
        counts={k: count(v, complete=flags[k]) for k, v in groups.items()},
        amounts=amounts,
        opportunity_changes=changes,
        **groups,
        next_steps=next_steps,
        members=member_facts,
        sources=list(sources.values()),
        missing_reasons=missing,
    )
    return facts, sorted(dependencies.values(), key=canonical_hash)
