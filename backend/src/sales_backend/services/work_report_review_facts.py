"""Server-owned Review material and live access checks, on the caller's connection.

The display evidence is deliberately separate from the authorization manifest.
Neither evidence text nor a remembered permission version grants access. Financial
values come only from the shared projections; task declarations never enter them.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sales_backend.contracts.review_overview import ReviewSelection
from sales_backend.contracts.review_scope import ReviewResolvedScopeV2
from sales_backend.contracts.work_report_review import ReviewFacts, ReviewFactsV2, validate_review_facts
from sales_backend.db import json_value
from sales_backend.domain.work_report import SHANGHAI, WorkReportError, canonical_hash
from sales_backend.services import review_okr, review_overview

RULES_VERSION = "work_report.v1"
PERMISSIONS = {"work_report.read", "work_report.write", "work_report.share", "agent.work_report", "report.export"}
ACTIVITY_LABELS = {
    "new_claimed_customers": "新认领客户",
    "formal_visits": "正式跟进",
    "new_opportunities": "新增商机",
    "lost_opportunities": "丢单商机",
    "updated_opportunities": "更新商机",
    "completed_tasks": "完成任务",
    "pending_tasks": "待办任务",
}


def _json(value):
    def encode(item):
        if isinstance(item, (datetime, date)):
            return item.isoformat()
        if isinstance(item, (UUID, Decimal)):
            return str(item)
        raise TypeError(type(item).__name__)

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=encode)


def _plain(value):
    return json.loads(_json(value))


def _denied(message="Review报告部分来源当前不可访问"):
    return WorkReportError("WORK_REPORT_PERMISSION_CHANGED", message, status=403)


def _subject(scope):
    if scope.kind == "team":
        return {"scope_type": "team", "record_id": str(scope.team_id)}
    if scope.kind not in {"self", "member"} or len(scope.member_ids) != 1:
        raise WorkReportError("WORK_REPORT_SCOPE_CHANGED", "Review报告仅支持已解析的个人或团队范围", status=403)
    return {"scope_type": "person", "record_id": str(scope.member_ids[0])}


def _subjects(scope):
    if isinstance(scope, ReviewResolvedScopeV2):
        return [dict(scope_type=row.scope_type, record_id=str(row.id)) for row in scope.subjects]
    return [_subject(scope)]


class _Material:
    def __init__(self, *, scope, period, now, permission_version, namespace=None):
        self.scope, self.period, self.now, self.permission_version = scope, period, now, permission_version
        self.namespace = namespace
        self.evidence, self.measures, self.groups, self.dependencies = {}, {}, {}, {}
        self.forecast_customers = {}
        self.missing = set()

    def dependency(self, source_type, record_id, refs, **fields):
        value = _plain(dict(source_type=source_type, record_id=str(record_id), **fields))
        key = canonical_hash(value)
        current = self.dependencies.setdefault(key, {**value, "source_refs": []})
        current["source_refs"] = sorted(set(current["source_refs"]) | set(refs))

    def subject(self, refs, *, required=(), subject=None):
        value = subject or _subject(self.scope)
        self.dependency(
            "review_subject",
            value["record_id"],
            refs,
            scope_type=value["scope_type"],
            required_permissions=sorted({"work_report.read", *required}),
        )

    def source(self, kind, record_id, payload, *, version=None, state="current", discriminator="", target=False):
        payload = _plain(payload)
        ref = "review:" + canonical_hash([kind, str(record_id), discriminator, self.namespace])
        value = dict(
            source_ref=ref,
            kind=kind,
            record_id=str(record_id),
            record_version=str(version) if version is not None else canonical_hash(payload),
            observed_at=self.now,
            state_kind=state,
            text=_json(payload),
            target_status="effective" if target else None,
        )
        if ref in self.evidence and self.evidence[ref] != value:
            raise WorkReportError("WORK_REPORT_SOURCE_FAILED", "同一Review来源出现冲突快照", status=503)
        self.evidence[ref] = value
        return ref

    def group(self, code, *, kr=None, title=None):
        key = "kr:" + str(kr["id"]) if kr else "metric:" + code
        return self.groups.setdefault(
            key,
            dict(
                group_id=key,
                metric_code=code,
                key_result_id=str(kr["id"]) if kr else None,
                objective_id=str(kr["objective_id"]) if kr else None,
                title=title or code,
                source_refs=[],
                measure_refs=[],
            ),
        )

    def attach(self, group, *, refs=(), measures=()):
        group["source_refs"] = sorted(set(group["source_refs"]) | set(refs))
        group["measure_refs"] = sorted(set(group["measure_refs"]) | set(measures))

    def measure(self, ident, card, role, refs, *, value=None, reasons=(), coverage=None, boundary=None):
        coverage = coverage or card.get("coverage", "unavailable")
        reasons = sorted(set(reasons if reasons else card.get("missing_reasons", [])))
        if coverage != "complete":
            value = None
            reasons = reasons or ["source_unavailable"]
        else:
            reasons = []
        self.missing.update(reasons)
        unit = "比例" if role == "rate" else card.get("unit", "元")
        self.measures[ident] = dict(
            measure_id=ident,
            metric_code=card["metric_code"],
            definition_version=int(card.get("definition_version", 1)),
            role=role,
            value=str(value) if value is not None else None,
            unit=unit,
            currency=card.get("currency") if unit != "比例" else None,
            tax_basis=card.get("tax_basis")
            if card.get("tax_basis") in {"tax_inclusive", "tax_exclusive"}
            else "not_applicable",
            coverage=coverage,
            missing_reasons=reasons,
            source_refs=sorted(set(refs)),
            comparison_boundary=boundary,
        )
        return ident

    def finish(self):
        return ReviewFacts(
            period=self.period,
            scope=self.scope,
            data_as_of=self.now,
            permission_version=self.permission_version,
            rules_version=RULES_VERSION,
            evidence=list(self.evidence.values()),
            measures=list(self.measures.values()),
            groups=list(self.groups.values()),
            missing_data=sorted(self.missing),
        ), list(self.dependencies.values())


async def _projection_sources(connection, material, card, *, query_ref):
    refs = [query_ref]
    forecast_ids = {str(s["opportunity_id"]) for s in card.get("source_refs", []) if "forecast_id" in s}
    missing = forecast_ids - material.forecast_customers.keys()
    if missing:
        rows = await connection.fetch(
            """/* review_report_forecast_customer_dependencies */
            SELECT o.id::text,o.customer_id::text FROM crm.opportunity o
            JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
            WHERE o.workspace_id=common.current_workspace_id() AND o.id=ANY($1::uuid[])
              AND o.deleted_at IS NULL AND c.deleted_at IS NULL
              AND security.authorization_opportunity('opportunity.read',o.id)
              AND security.authorization_opportunity('work_report.read',o.id)
              AND security.authorization_customer('customer.read',c.id)
              AND security.authorization_customer('work_report.read',c.id)""",
            sorted(missing),
        )
        if {row["id"] for row in rows} != missing:
            raise _denied()
        material.forecast_customers.update({row["id"]: row["customer_id"] for row in rows})
    for source in card.get("source_refs", []):
        source = _plain(source)
        kind = source.get("kind", "opportunity")
        ident = str(source.get("opportunity_id") if "forecast_id" in source else source.get("id"))
        if kind == "effective_actual":
            rows = await connection.fetch(
                """/* review_report_actual_dependency */ SELECT a.* FROM crm.effective_actual a
              WHERE a.workspace_id=common.current_workspace_id() AND a.id=$1 AND a.kind=$2
                AND security.authorization_customer('actual.read',a.customer_id)
                AND security.authorization_customer('work_report.read',a.customer_id)
                AND (a.opportunity_id IS NULL OR (security.authorization_opportunity('actual.read',a.opportunity_id)
                  AND security.authorization_opportunity('work_report.read',a.opportunity_id)))""",
                str(source["id"]),
                card["metric_code"],
            )
            if len(rows) != 1:
                raise _denied()
            row = dict(rows[0])
            ref = material.source(
                "metric_query",
                source["id"],
                {"source": source, "actual": row},
                version=row["version_no"],
                discriminator="actual",
            )
            material.dependency(
                "review_actual",
                source["id"],
                [ref, query_ref],
                kind=row["kind"],
                record_version=row["version_no"],
                customer_id=row["customer_id"],
                opportunity_id=row["opportunity_id"],
            )
        elif kind in {"opportunity", "visit"}:
            ref = material.source(
                "opportunity" if kind == "opportunity" else "activity",
                ident,
                source,
                version=source.get("opportunity_version", source.get("version_no")),
                discriminator="projection:" + card["metric_code"] + ":" + canonical_hash(source),
            )
            material.dependency(kind, ident, [ref, query_ref])
            if "forecast_id" in source:
                material.dependency("meeting_customer", material.forecast_customers[ident], [ref, query_ref])
        else:
            raise WorkReportError("WORK_REPORT_SOURCE_FAILED", "未知自动指标来源，不能生成报告", status=503)
        refs.append(ref)
    return refs


async def _add_card(connection, material, card, *, mode, group, prefix, subject=None):
    code = card["metric_code"]
    query_ref = material.source(
        "metric_query",
        prefix,
        {
            "metric_code": code,
            "definition_version": card.get("definition_version", 1),
            "value": card.get("value"),
            "known_subtotal": card.get("known_subtotal"),
            "coverage": card.get("coverage", "unavailable"),
            "missing_reasons": card.get("missing_reasons", []),
            "source_kind": card.get("source_kind", "forecast"),
            "basis": "shared_review_projection",
            "period": card.get("period"),
            "scope": subject or _subject(material.scope),
        },
        version=card.get("definition_version", 1),
    )
    required = []
    if card.get("coverage") == "complete":
        required = (
            ("opportunity.read", "customer.read")
            if code in review_okr.FORECAST_METRICS
            else (
                ("actual.read", "customer.read", "opportunity.read")
                if code in review_okr.ACTUAL_METRICS
                else ("opportunity.read",)
                if code == "opportunity_count"
                else ("visit.read",)
                if code == "visit_count"
                else ()
            )
        )
    material.subject([query_ref], required=required, subject=subject)
    refs = await _projection_sources(connection, material, card, query_ref=query_ref)
    role = "kr_progress" if prefix.startswith("kr_") else "forecast"
    measure_refs = [material.measure(prefix + "." + role, card, role, refs, value=card.get("value"))]
    target = card.get("target") or {}
    if target.get("status") == "effective" and target.get("id"):
        target_ref = material.source("target", target["id"], target, version=target["version_no"], target=True)
        material.dependency("review_target", target["id"], [target_ref], record_version=str(target["version_no"]))
        refs.append(target_ref)
        measure_refs.append(
            material.measure(
                prefix + ".effective_target",
                card,
                "effective_target",
                [target_ref],
                value=target["value"],
                coverage="complete",
            )
        )
    if role == "forecast":
        for target_role in ("gap", "rate"):
            valid = card.get("coverage") == "complete" and card.get(target_role) is not None
            reasons = card.get("missing_reasons", []) or ["no_effective_target"]
            measure_refs.append(
                material.measure(
                    prefix + "." + target_role,
                    card,
                    target_role,
                    refs,
                    value=card.get(target_role),
                    coverage="complete" if valid else "unavailable",
                    reasons=reasons,
                )
            )
    # Current-state forecasts and period totals cannot establish a past boundary.
    change = "day_change" if mode == "daily" else "week_change"
    measure_refs.append(
        material.measure(
            prefix + "." + change,
            card,
            change,
            [query_ref],
            coverage="unavailable",
            reasons=["no_baseline"],
            boundary=material.period.start,
        )
    )
    material.attach(group, refs=refs, measures=measure_refs)


def _related_dependencies(material, item, refs):
    for field, kind in (("customer_id", "meeting_customer"), ("opportunity_id", "opportunity")):
        if item.get(field):
            material.dependency(kind, item[field], refs)
    for row in item.get("opportunities", []):
        material.dependency("opportunity", row["id"], refs)
    for person in item.get("member_ids", []):
        material.dependency("meeting_member", person, refs)


def _activity_dependencies(material, code, item, refs):
    _related_dependencies(material, item, refs)
    if code in {"completed_tasks", "pending_tasks"}:
        material.dependency("task", item["task_id"], refs)
    elif code == "formal_visits":
        material.dependency("visit", item["id"], refs)
    elif code == "updated_opportunities":
        material.dependency("opportunity", item["opportunity_id"], refs)
    else:
        for source in item.get("source_refs", []):
            if not isinstance(source, str) or ":" not in source:
                raise WorkReportError("WORK_REPORT_SOURCE_FAILED", "活动来源标识不完整", status=503)
            kind, ident = source.split(":", 1)
            if kind == "customer_claim":
                material.dependency("meeting_claim", ident, refs, customer_id=item["customer_id"])
            elif kind == "opportunity":
                material.dependency("opportunity", ident, refs)
            elif kind in {"opportunity_history", "forecast_history"}:
                material.dependency(
                    "meeting_state" if kind == "opportunity_history" else "meeting_forecast",
                    ident,
                    refs,
                    opportunity_id=item["opportunity_id"],
                )
            else:
                raise WorkReportError("WORK_REPORT_SOURCE_FAILED", "未知活动来源", status=503)


async def _prepare_review_facts_single(connection, actor, *, selection, scope, period, data_as_of,
                                       permission_version, mode, namespace=None):
    """Return (ReviewFacts, private dependency list) in the existing read snapshot."""
    if not connection.is_in_transaction():
        raise _denied("Review取材必须在调用方已有事务中执行")
    await review_overview.repository.assert_read_context(connection, actor)
    now = await connection.fetchval("SELECT transaction_timestamp()")
    if now != data_as_of:
        raise WorkReportError("WORK_REPORT_SNAPSHOT_CHANGED", "Review取数时间必须来自同一事务", status=409)
    if mode not in {"daily", "weekly"} or period.kind != ("day" if mode == "daily" else "week"):
        raise WorkReportError("WORK_REPORT_INVALID_INPUT", "Review模式与周期不一致", status=422)
    _subject(scope)
    chosen = ReviewSelection(
        scope=scope.kind,
        member_id=scope.member_id,
        team_id=scope.team_id,
        # The queued request may cross midnight before this Worker reads it.
        # Its implicit anchor belongs to the API-frozen period, not Worker time.
        anchor_date=selection.period.anchor_date or period.cutoff.astimezone(SHANGHAI).date(),
        period_type=period.kind,
    )
    projection = await review_overview.read_metric_projection(connection, actor, chosen)
    if (
        projection["period"].model_copy(update={"cutoff": period.cutoff}) != period
        or period.cutoff > projection["period"].cutoff
        or set(projection["scope"].member_ids) != set(scope.member_ids)
        or projection["scope"].team_id != scope.team_id
        or datetime.fromisoformat(projection["data_as_of"]) != now
    ):
        raise WorkReportError("WORK_REPORT_SCOPE_CHANGED", "Review周期或授权范围与冻结取材不一致", status=403)
    # Quarterly/current inventory values are observed at this transaction's
    # data_as_of. Period activity must use the request's immutable cutoff.
    projection = {**projection, "period": period}
    material = _Material(scope=scope, period=period, now=now, permission_version=permission_version,
                         namespace=namespace)
    for card in projection["metrics"]:
        card = {**card, "period": projection["quarter"]}
        group = material.group(card["metric_code"])
        await _add_card(connection, material, card, mode=mode, group=group, prefix=card["metric_code"])

    activities, counts, _ = await review_overview.activity_material(connection, actor, projection, now)
    task_items, task_refs = {}, {}
    for code, items in activities.items():
        material.missing.update(counts[code].get("missing_reasons", []))
        for item in items:
            item = _plain(item)
            is_task = code in {"completed_tasks", "pending_tasks"}
            ident = (
                item["task_id"]
                if is_task
                else item["id"]
                if code == "formal_visits"
                else item.get("opportunity_id") or item.get("customer_id") or item["id"]
            )
            # Large histories remain individually identifiable; narratives need
            # the action and its source IDs, not unbounded copies of all changes.
            payload = {k: v for k, v in item.items() if k not in {"events", "summary"}}
            payload.update(
                activity_code=code,
                activity_label=ACTIVITY_LABELS[code],
                summary_excerpt=item.get("summary", "")[:8000],
                summary_truncated=len(item.get("summary", "")) > 8000,
            )
            if item.get("events"):
                payload["events"] = [
                    {k: row.get(k) for k in ("kind", "id", "occurred_at", "version_no")} for row in item["events"]
                ]
            ref = material.source(
                "task" if is_task else "activity",
                ident,
                payload,
                version=item.get("version_no"),
                state="current" if code == "pending_tasks" else "occurred",
                discriminator=code,
            )
            _activity_dependencies(material, code, item, [ref])
            if is_task:
                task_items[str(ident)] = item
                task_refs.setdefault(str(ident), []).append(ref)

    task_links = {}
    if task_items:
        rows = await connection.fetch(
            """/* review_report_task_links */
          SELECT t.id::text,t.version_no,security.task_alignment_read(t.id) AS alignment
          FROM workflow.task t WHERE t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL
            AND t.id=ANY($1::uuid[]) AND security.authorization_task('task.read',t.id)
            AND security.authorization_task('work_report.read',t.id) ORDER BY t.id""",
            sorted(task_items),
        )
        if {row["id"] for row in rows} != set(task_items):
            raise _denied()
        for row in rows:
            for task_ref in task_refs[row["id"]]:
                material.evidence[task_ref]["record_version"] = str(row["version_no"])
            alignment = json_value(row["alignment"])
            if alignment is None:
                material.missing.add("task_alignment_missing")
                continue
            refs = task_refs[row["id"]]
            for key, state in (("expected_contribution", "planned"), ("actual_contribution", "declaration")):
                if alignment.get(key) is not None:
                    ref = material.source(
                        "task",
                        row["id"],
                        {key: alignment[key], "counts_toward_financial_actuals": False},
                        version=row["version_no"],
                        state=state,
                        discriminator=key,
                    )
                    refs.append(ref)
                    material.dependency("task", row["id"], [ref])
            for code in alignment.get("metric_codes", []):
                material.attach(material.group(code), refs=refs)
            for kr_id in alignment.get("key_result_ids", []):
                task_links.setdefault(str(kr_id), []).extend(refs)
            if alignment.get("restricted_key_result_count"):
                material.missing.add("task_key_result_restricted")

    # Exhaust pagination. There is no first-page-only OKR report population.
    offset = 0
    seen = set()
    while True:
        page = await review_okr.read_key_results(connection, actor, limit=200, offset=offset)
        for kr in page["items"]:
            kr = _plain(kr)
            if kr["id"] in seen:
                raise WorkReportError("WORK_REPORT_SOURCE_FAILED", "KR分页存在重复记录", status=503)
            seen.add(kr["id"])
            ks = kr["scope"]
            if ks["scope_type"] not in {"person", "team"}:
                continue
            subject = dict(
                scope_type=ks["scope_type"], record_id=ks["team_id"] if ks["scope_type"] == "team" else ks["user_id"]
            )
            relevant = subject == _subject(scope) or kr["id"] in task_links
            overlaps = str(kr["period"]["start"]) <= str(period.cutoff.astimezone(SHANGHAI).date()) and str(
                kr["period"]["end"]
            ) >= str(period.start.date())
            if (
                not relevant
                or not overlaps
                or kr.get("status") != "active"
                or kr.get("objective_status", "active") != "active"
            ):
                continue
            ref = material.source(
                "key_result",
                kr["id"],
                {k: kr[k] for k in ("title", "metric_code", "objective_id", "scope", "period", "progress_mode")},
                version=kr["version_no"],
            )
            material.dependency("review_key_result", kr["id"], [ref], record_version=str(kr["version_no"]))
            material.dependency("review_objective", kr["objective_id"], [ref])
            material.subject([ref], subject=subject)
            group = material.group(kr["metric_code"], kr=kr, title=kr["title"])
            material.attach(group, refs=[ref, *task_links.get(kr["id"], [])])
            # Any task contribution cites its KR metadata only as an alignment;
            # the KR numeric progress is recomputed by the shared KR service.
            for task_ref in task_links.get(kr["id"], []):
                material.dependency("review_key_result", kr["id"], [task_ref], record_version=str(kr["version_no"]))
            progress = kr["progress"]
            if progress.get("data_as_of") and datetime.fromisoformat(progress["data_as_of"]) != now:
                raise WorkReportError("WORK_REPORT_SNAPSHOT_CHANGED", "KR投影不属于同一事务快照", status=409)
            manual = kr["progress_mode"] == "manual" and progress.get("status") == "confirmed"
            metric = kr["metric"]
            card = {
                **progress,
                "metric_code": kr["metric_code"],
                "unit": metric["unit"],
                "definition_version": metric["definition_version"],
                "period": kr["period"],
                "target": kr.get("effective_target"),
            }
            # An unavailable projection still retains its defined tax basis;
            # lack of source permission must produce a typed missing value.
            if kr["metric_code"] in review_okr.FORECAST_METRICS | review_okr.ACTUAL_METRICS:
                card["currency"] = "CNY"
                card["tax_basis"] = (
                    "tax_exclusive"
                    if kr["metric_code"] == "recognized"
                    else "tax_inclusive"
                    if kr["metric_code"] == "forecast_recognized"
                    else "not_applicable"
                )
            card["source_kind"] = metric.get("source_kind", "manual" if kr["progress_mode"] == "manual" else "actual")
            if manual:
                manual_ref = material.source(
                    "key_result",
                    kr["id"],
                    {"confirmed_progress": progress},
                    version=progress["version_no"],
                    discriminator="confirmed_progress",
                )
                material.dependency("review_key_result", kr["id"], [manual_ref], record_version=str(kr["version_no"]))
                measure = material.measure(
                    "kr_" + kr["id"] + ".kr_progress",
                    card,
                    "kr_progress",
                    [manual_ref],
                    value=progress["value"],
                    coverage="complete",
                )
                material.attach(group, refs=[manual_ref], measures=[measure])
            else:
                if card.get("coverage") is None:
                    card.update(
                        coverage="unavailable", missing_reasons=[progress.get("reason") or "no_confirmed_progress"]
                    )
                await _add_card(
                    connection, material, card, mode=mode, group=group, prefix="kr_" + kr["id"], subject=subject
                )
        offset += len(page["items"])
        if offset >= page["total"]:
            break
        if not page["items"]:
            raise WorkReportError("WORK_REPORT_SOURCE_FAILED", "KR分页取材未覆盖全部记录", status=503)
    if namespace:
        # The v2 union keeps each subject's measure independently traceable;
        # the group identity remains the shared metric/KR identity.
        mapping = {
            ident: f"scope_{namespace}_{ident}"
            for ident in material.measures
        }
        material.measures = {
            mapping[ident]: {**measure, "measure_id": mapping[ident]}
            for ident, measure in material.measures.items()
        }
        for group in material.groups.values():
            group["measure_refs"] = [mapping[ident] for ident in group["measure_refs"]]
    facts, dependencies = material.finish()
    await assert_review_access(connection, actor, facts, dependencies=dependencies)
    return facts, dependencies


async def prepare_review_facts(connection, actor, *, selection, scope, period, data_as_of, permission_version, mode):
    """Prepare one frozen facts object; v2 unions each authorized subject.

    The single-scope producer remains the source of every metric and activity
    query.  Multi-scope snapshots run it once per resolver-approved legacy
    scope in the same repeatable-read transaction, then expose only the v2
    scope and the unioned evidence to generation.
    """
    if not isinstance(scope, ReviewResolvedScopeV2):
        return await _prepare_review_facts_single(
            connection, actor, selection=selection, scope=scope, period=period,
            data_as_of=data_as_of, permission_version=permission_version, mode=mode,
        )
    from sales_backend.services.review_scope import resolve_review_scope

    resolution = await resolve_review_scope(connection, actor, selection.scope)
    if resolution.scope != scope:
        raise WorkReportError("WORK_REPORT_SCOPE_CHANGED", "Review范围解析已变化，请重新取材", status=409)
    facts_rows, dependency_rows = [], []
    for index, legacy in enumerate(resolution.legacy_scopes):
        subject = resolution.scope.subjects[index]
        namespace = canonical_hash({"scope_type": subject.scope_type, "record_id": str(subject.id)})[:16]
        facts, dependencies = await _prepare_review_facts_single(
            connection, actor, selection=selection, scope=legacy, period=period,
            data_as_of=data_as_of, permission_version=permission_version, mode=mode,
            namespace=namespace,
        )
        facts_rows.append(facts)
        dependency_rows.extend(dependencies)
    evidence, measures, groups, missing = {}, [], {}, set()
    for facts in facts_rows:
        missing.update(facts.missing_data)
        for item in facts.evidence:
            evidence[item.source_ref] = item
        for item in facts.measures:
            measures.append(item)
        for group in facts.groups:
            target = groups.setdefault(group.group_id, group.model_copy(deep=True))
            if target is not group:
                target.source_refs = sorted(set(target.source_refs) | set(group.source_refs))
                target.measure_refs = sorted(set(target.measure_refs) | set(group.measure_refs))
    merged = ReviewFactsV2.model_validate(dict(
        period=period,
        scope=scope,
        data_as_of=data_as_of,
        permission_version=permission_version,
        rules_version=RULES_VERSION,
        evidence=list(evidence.values()),
        measures=measures,
        groups=list(groups.values()),
        missing_data=sorted(missing),
    ))
    await assert_review_access(connection, actor, merged, dependencies=dependency_rows)
    return merged, dependency_rows


async def assert_review_access(connection, actor, facts, *, dependencies, permission="work_report.read"):
    """Recheck the complete private manifest and each operation on actual objects.

    May run in a caller's write transaction before persistence. Current source
    authorization is required even when an old immutable report is being read.
    """
    facts = validate_review_facts(facts)
    refs = {e.source_ref for e in facts.evidence}
    if not dependencies or any(not isinstance(d, dict) for d in dependencies):
        raise _denied("Review报告缺少服务器来源依赖")
    bound = {ref for d in dependencies for ref in d.get("source_refs", [])}
    if refs != bound:
        raise _denied("Review报告来源依赖与冻结证据不匹配")
    subjects = _subjects(facts.scope)
    if any(not any(
        d.get("source_type") == "review_subject"
        and d.get("scope_type") == subject["scope_type"]
        and d.get("record_id") == subject["record_id"]
        for d in dependencies
    ) for subject in subjects):
        raise _denied("Review报告范围依赖缺失")
    await assert_review_dependencies(connection, actor, dependencies, permission=permission)


async def assert_review_dependencies(connection, actor, dependencies, *, permission="work_report.read"):
    """Accept raw or database-normalized private manifests, never evidence text.

    This also serves existing saved-report action checks whose database manifest
    intentionally omits display source_refs. No historical permission-version or
    author identity equality is imposed on a newly authorized reader.
    """
    if permission not in PERMISSIONS:
        raise ValueError("unsupported report action")
    identity = await connection.fetchrow(
        "SELECT common.current_workspace_id()::text AS workspace_id,"
        "common.current_user_ref_id()::text AS user_id,current_setting('row_security') AS rls"
    )
    if not identity or dict(identity) != {"workspace_id": actor.workspace_id, "user_id": actor.user_id, "rls": "on"}:
        raise _denied("Review报告必须使用当前用户的真实数据库授权")
    allowed_types = {
        "review_subject",
        "review_target",
        "review_objective",
        "review_key_result",
        "review_actual",
        "task",
        "visit",
        "opportunity",
        "meeting_customer",
        "meeting_claim",
        "meeting_state",
        "meeting_forecast",
        "meeting_member",
    }
    try:
        if not isinstance(dependencies, list) or not dependencies:
            raise ValueError("empty dependency list")
        for dep in dependencies:
            if not isinstance(dep, dict) or dep.get("source_type") not in allowed_types:
                raise ValueError("unknown source")
            if dep["source_type"] != "review_actual":
                UUID(dep["record_id"])
            elif (
                not isinstance(dep.get("record_id"), str)
                or not dep["record_id"]
                or dep.get("kind") not in {"collection", "recognized"}
                or not str(dep.get("record_version", "")).isdigit()
            ):
                raise ValueError("invalid actual dependency")
            if dep["source_type"] == "review_subject":
                required = dep.get("required_permissions")
                source_reads = {
                    "work_report.read",
                    *(k + ".read" for k in ("customer", "opportunity", "visit", "task", "risk", "actual", "target")),
                }
                if (
                    dep.get("scope_type") not in {"person", "team"}
                    or not isinstance(required, list)
                    or "work_report.read" not in required
                    or len(required) != len(set(required))
                    or not set(required) <= source_reads
                ):
                    raise ValueError("invalid subject dependency")
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise _denied("Review报告包含无效服务器来源依赖") from error
    if not await connection.fetchval("SELECT security.authorization_has($1)", permission):
        raise _denied()
    if not await connection.fetchval("SELECT security.work_report_source_access($1::jsonb,'[]'::jsonb)", dependencies):
        raise _denied()
    checked = set()
    for dep in dependencies:
        kind, ident = dep["source_type"], dep["record_id"]
        key = (kind, ident)
        if key in checked:
            continue
        checked.add(key)
        if kind == "review_subject":
            allowed = await connection.fetchval(
                "SELECT security.authorization_subject($1,$2,$3::uuid,$4::uuid)",
                permission,
                dep["scope_type"],
                ident if dep["scope_type"] == "person" else None,
                ident if dep["scope_type"] == "team" else None,
            )
        elif kind in {"review_target", "review_objective", "review_key_result"}:
            queries = {
                "review_target": "FROM crm.sales_target o WHERE o.id=$2::uuid",
                "review_objective": "FROM crm.review_objective o WHERE o.id=$2::uuid",
                "review_key_result": "FROM crm.review_key_result k JOIN crm.review_objective o "
                "ON o.id=k.objective_id AND o.workspace_id=k.workspace_id WHERE k.id=$2::uuid",
            }
            # Closed server-owned SQL fragments; permissions and IDs stay bound.
            allowed = await connection.fetchval(
                "SELECT security.authorization_subject($1,o.scope_type,o.user_ref_id,o.team_id) "
                + queries[kind]
                + " AND o.workspace_id=common.current_workspace_id()",
                permission,
                ident,
            )
        elif kind == "review_actual":
            allowed = await connection.fetchval(
                """SELECT bool_and(security.authorization_customer($1,e.customer_id)
              AND (e.opportunity_id IS NULL OR security.authorization_opportunity($1,e.opportunity_id)))
              FROM crm.effective_actual e WHERE e.workspace_id=common.current_workspace_id()
                AND e.id=$2 AND e.kind=$3""",
                permission,
                ident,
                dep["kind"],
            )
        else:
            resource, target = {
                "task": ("task", ident),
                "visit": ("visit", ident),
                "opportunity": ("opportunity", ident),
                "meeting_customer": ("customer", ident),
                "meeting_claim": ("customer", dep.get("customer_id")),
                "meeting_state": ("opportunity", dep.get("opportunity_id")),
                "meeting_forecast": ("opportunity", dep.get("opportunity_id")),
                "meeting_member": ("member", ident),
            }.get(kind, (None, None))
            predicates = {
                "task": "security.authorization_task($1,$2::uuid)",
                "visit": "security.authorization_visit($1,$2::uuid)",
                "opportunity": "security.authorization_opportunity($1,$2::uuid)",
                "customer": "security.authorization_customer($1,$2::uuid)",
                "member": "security.authorization_subject($1,'person',$2::uuid,NULL)",
            }
            if resource is None or target is None:
                raise _denied("Review报告包含未知来源依赖")
            allowed = await connection.fetchval("SELECT " + predicates[resource], permission, target)
        if not allowed:
            raise _denied("当前操作授权不覆盖Review报告的全部来源")
