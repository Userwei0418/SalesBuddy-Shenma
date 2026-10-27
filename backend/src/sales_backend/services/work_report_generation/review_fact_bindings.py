"""Server-owned weekly v2 facts and evidence-bound suggestion choices.

This enforces faithful frozen-fact rendering, not arbitrary prose truth. Model
proposals never supply factual text, and rejected proposals are never repaired.
"""

import json
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from sales_backend.contracts.work_report_review import WeeklyReviewContent

from .review_numbers import METRIC_LABELS, ROLE_LABELS, measure_subjects

REASONS = {
    "unknown_eligibility": "统计纳入条件尚未明确",
    "missing_forecast_amount": "预测金额尚未补齐",
    "no_effective_target": "尚无生效目标",
    "no_baseline": "缺少可信历史比较基线",
    "no_actual_evidence": "缺少实际业绩依据",
    "incomplete_actual_period": "实际业绩期间覆盖不完整",
    "missing_material": "所需材料尚未补齐",
    "tax_basis_unresolved": "税务口径尚未明确",
    "source_confirmation_required": "数据来源尚待确认",
    "period_actual_not_allocated_to_selected_dates": "期间实绩尚未分配到所选日期",
    "source_scope_not_fully_authorized": "来源读取权限未覆盖所选范围",
    "no_confirmed_progress": "缺少已确认进展",
    "source_unavailable": "所需来源暂不可用",
}


def enabled(facts, mode):
    return mode == "weekly" and facts.contract_version == "work_report.review_facts.v2"


def item_key(item):
    return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _source_data(source):
    try:
        value = json.loads(source.text)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _pack(rows):
    """Keep every labeled fact, within the existing public per-item bounds."""
    packed = []
    for row in rows:
        if len(row["text"]) > 2000:
            raise ValueError("Review frozen fact exceeds item bounds")
        if (
            packed
            and packed[-1].get("kind") == row.get("kind")
            and packed[-1]["source_refs"] == row["source_refs"]
            and len(packed[-1]["text"]) + len(row["text"]) + 1 <= 2000
        ):
            last = packed[-1]
            last["text"] += "\n" + row["text"]
            for key in ("source_refs", "measure_refs"):
                last[key] = list(dict.fromkeys([*last[key], *row[key]]))
        else:
            packed.append(deepcopy(row))
    if len(packed) > 30:
        raise ValueError("Review frozen facts exceed public section bounds")
    return packed


def _quote(value, *, limit=240):
    if not isinstance(value, str) or not value.strip():
        return ""
    # Explicit excerpts are source quotations, not newly asserted facts.
    text = value.strip().replace("{{metric:", "［指标标记：")
    return "“" + text[:limit] + ("…（节选）" if len(text) > limit else "") + "”"


def _business_context(source):
    """Known fields only; preserve plans/declarations and quoted-source status."""
    data = _source_data(source)
    kind = {"occurred": "fact", "current": "current", "planned": "plan", "declaration": "declaration"}[
        source.state_kind
    ]
    title = _quote(data.get("name") or data.get("title") or data.get("opportunity_name"))
    customer = _quote(data.get("customer_name"))
    label = {"task": "关联任务", "activity": "关联业务活动", "opportunity": "关联商机"}.get(source.kind)
    if not label:
        return None, []
    context = ("客户" + customer + "的" if customer else "") + label + title
    state = {"occurred": "已发生记录", "current": "当前记录", "planned": "已登记计划", "declaration": "贡献声明"}[
        source.state_kind
    ]
    details = []
    stopped = source.kind == "task" and data.get("completion_action") == "close"
    for key, caption in (
        ("activity_label", "活动"),
        ("status_label", "状态"),
        ("summary_excerpt", "材料原文"),
        ("next_action", "已登记下一步原文"),
    ):
        if quote := _quote(data.get(key), limit=160 if source.kind == "task" else 240):
            details.append(caption + quote)
    if source.kind == "task":
        result = data.get("completion_result") or data.get("completion_note")
        if quote := _quote(result, limit=160):
            caption = "停止说明" if stopped else "提交说明" if source.state_kind == "current" else "完成结果"
            details.append(caption + quote)
        if stopped:
            details.append("下发人决定不再继续，并非验收通过或目标达成")
        elif source.state_kind == "current":
            for key, caption in (("latest_feedback", "最新反馈"), ("next_step", "已登记下一步"),
                                 ("processing_detail", "处理说明")):
                if quote := _quote(data.get(key), limit=160):
                    details.append(caption + quote)
            reason = data.get("processing_reason")
            if label := {"adjustment_request": "申请调整", "paused": "暂停", "awaiting_review": "待验收"}.get(reason):
                details.append("处理原因：" + label)
            if reason == "awaiting_review":
                details.append("仍待下发人验收，不计入完成数量或目标达成")
            details.append("取数时点的未完成任务现状，不代表本期发生推进")
    elif quote := _quote(data.get("completion_note")):
        details.append("完成说明" + quote)
    for key, caption in (("expected_contribution", "预计贡献"), ("actual_contribution", "贡献声明")):
        contribution = data.get(key)
        if isinstance(contribution, dict):
            details.append(
                caption + "说明" + (_quote(contribution.get("note")) or "尚无文字说明") + "；贡献声明不等于已实现业绩"
            )
            if quote := _quote(contribution.get("value")):
                details.append("申报数值" + quote + "，仅为任务声明，不计入正式指标")
    if not data and (quote := _quote(source.text)):
        details.append("来源原话" + quote + "，未独立核实其业务结论")
    if source.kind == "opportunity" and "forecast_id" in data:
        details.append("已关联预测登记" if data["forecast_id"] is not None else "尚未关联预测登记")
    if data.get("overdue") is True:
        details.append("记录标注逾期，需核实当前安排")
    text = f"{context}：{state}。" + "；".join(details) + ("。" if details else "")
    row = {"kind": kind, "text": text, "source_refs": [source.source_ref], "measure_refs": []}
    actions = (
        ["核实执行进展与阻碍", "确认后续安排"] if source.kind == "task" else ["核实后续跟进安排", "核实相关风险与依据"]
    )
    if stopped:
        actions = ["核实停止原因与后续影响", "确认相关收尾安排"]
    recorded = "" if stopped else _quote(data.get("next_step") or data.get("next_action"))
    background = _quote(data.get("completion_result") or data.get("completion_note")
                        or data.get("processing_detail") or data.get("latest_feedback") or data.get("summary_excerpt"))
    if recorded:
        actions = [f"确认记录中的下一步{recorded}及落实安排", *actions]
    elif background:
        actions = [f"结合记录原话{background}确认后续安排", *actions]
    choices = [
        {
            "kind": "suggestion",
            "text": f"建议围绕{context}{action}，待人工确认。",
            "source_refs": [source.source_ref],
            "metric_refs": [],
        }
        for action in actions
    ]
    return row, choices


def _kr_scope(group, sources):
    if not group.key_result_id:
        return None
    metadata = [
        _source_data(sources[ref])
        for ref in group.source_refs
        if sources[ref].kind == "key_result"
        and sources[ref].record_id == str(group.key_result_id)
        and "scope" in _source_data(sources[ref])
    ]
    if not metadata or any(row.get("metric_code") != group.metric_code for row in metadata):
        raise ValueError("Review KR lacks frozen metadata")
    scopes = [row["scope"] for row in metadata]
    if any(scope != scopes[0] for scope in scopes):
        raise ValueError("Review KR own scope is inconsistent")
    scope = scopes[0]
    kind = scope.get("scope_type")
    ident = scope.get("team_id" if kind == "team" else "user_id")
    if kind not in {"team", "person"} or not ident:
        raise ValueError("Review KR own scope is missing")
    return {"scope_type": kind, "record_id": str(ident)}


def catalog(facts, *, include_business_context=True):
    """All identities come from the frozen producer, never a model sentence."""
    subjects = measure_subjects(facts)
    sources = {row.source_ref: row for row in facts.evidence}
    measures = {row.measure_id: row for row in facts.measures}
    result = []
    for group in facts.groups:
        progress, risks, candidates = [], [], []
        kr_scope = _kr_scope(group, sources)
        for ref in group.measure_refs:
            measure = measures[ref]
            subject = subjects.get(ref)
            if subject is None:
                raise ValueError("Review measure lacks frozen subject identity")
            # A linked KR can belong to a different authorized subject. Its
            # own frozen group is its identity; never relabel it as the selected team.
            label = (
                f"KR“{group.title}”"
                if group.key_result_id
                else f"{subject.label}（仅此{'团队' if subject.scope_type == 'team' else '成员'}）"
            )
            metric = METRIC_LABELS.get(measure.metric_code, "所选指标")
            role = "金额" if measure.role == "forecast" and metric.endswith("预测") else ROLE_LABELS[measure.role]
            refs = list(measure.source_refs)
            if not set(refs) <= set(group.source_refs):
                raise ValueError("Review fact provenance exceeds frozen group")
            queries = []
            for source_ref in refs:
                source = sources[source_ref]
                if source.kind != "metric_query":
                    continue
                query = _source_data(source)
                if query.get("basis") != "shared_review_projection":
                    # Individual actual provenance uses the same evidence kind.
                    # It is not a scope-level projection or a new metric total.
                    if isinstance(query.get("actual"), dict) and isinstance(query.get("source"), dict):
                        continue
                    raise ValueError("Review metric query lacks typed projection")
                if query.get("metric_code") != measure.metric_code:
                    raise ValueError("Review query metric identity mismatch")
                if group.key_result_id and source.record_id != "kr_" + str(group.key_result_id):
                    raise ValueError("Review query KR identity mismatch")
                scope = query.get("scope")
                if scope != (kr_scope or {"scope_type": subject.scope_type, "record_id": str(subject.id)}):
                    raise ValueError("Review query subject identity mismatch")
                queries.append((source_ref, query))
            if measure.role in {"forecast", "current"} and len(queries) != 1:
                raise ValueError("Review metric requires its exact scope projection")
            if measure.role == "kr_progress" and not queries:
                confirmations = [
                    _source_data(sources[key]).get("confirmed_progress")
                    for key in refs
                    if sources[key].kind == "key_result" and sources[key].record_id == str(group.key_result_id)
                ]
                if not confirmations or any(
                    not isinstance(row, dict) or row.get("value") != measure.value or row.get("status") != "confirmed"
                    for row in confirmations
                ):
                    raise ValueError("Review manual KR lacks frozen confirmation")
            if measure.role in {"forecast", "current", "kr_progress"} and queries:
                if any(q.get("coverage") != measure.coverage or q.get("value") != measure.value for _, q in queries):
                    raise ValueError("Review query value or coverage mismatch")
            prefix = f"{label}：{metric}{role}"
            basis = {"tax_inclusive": "含税口径", "tax_exclusive": "不含税口径", "not_applicable": "不适用税务区分"}[
                measure.tax_basis
            ]
            period = queries[0][1].get("period") if len(queries) == 1 else None
            if (
                isinstance(period, dict)
                and isinstance(period.get("year"), int)
                and period.get("quarter") in (1, 2, 3, 4)
            ):
                basis += f"；统计周期为{period['year']}年第{period['quarter']}季度"
            if measure.comparison_boundary is not None:
                basis += "；比较边界为" + measure.comparison_boundary.isoformat()
            if measure.coverage == "complete":
                progress.append(
                    {
                        "kind": "current",
                        "text": prefix + "为 {{metric:" + ref + "}} " + measure.unit + f"；该统计覆盖完整。{basis}。",
                        "source_refs": refs,
                        "measure_refs": [ref],
                    }
                )
            else:
                reasons = "；".join(
                    dict.fromkeys(REASONS.get(code, "所需数据尚待核实") for code in measure.missing_reasons)
                )
                coverage = "部分覆盖" if measure.coverage == "partial" else "暂不可用"
                text = f"{prefix}尚无法确定；该统计{coverage}。{reasons}。{basis}。"
                if measure.role in {"forecast", "current", "kr_progress"}:
                    subtotal = queries[0][1].get("known_subtotal") if len(queries) == 1 else None
                    if subtotal is not None:
                        try:
                            valid = isinstance(subtotal, str) and Decimal(subtotal).is_finite()
                        except InvalidOperation:
                            valid = False
                        if not valid:
                            raise ValueError("Review known subtotal must remain exact decimal text")
                        text += f"已知部分为 {subtotal} {measure.unit}，不是全量总额。"
                risks.append({"text": text, "source_refs": refs, "measure_refs": []})
                # Recommendations name an action, not an invented fact or date.
                action = f"建议核实{label}的{metric}{role}所需依据，待人工确认。"
                anchor = [queries[0][0]] if len(queries) == 1 else refs
                candidates.append({"kind": "suggestion", "text": action, "source_refs": anchor, "metric_refs": []})
        for ref in (group.source_refs if include_business_context else ()):
            source = sources[ref]
            business, actions = _business_context(source)
            if business:
                candidates.extend(actions)
                # Keep linked task/activity progress. Bare forecast-index rows
                # are shown with selected recommendations, not dumped en masse.
                if source.kind != "opportunity":
                    progress.append(business)
                if _source_data(source).get("overdue") is True:
                    risks.append(
                        {
                            "text": business["text"] + "该记录的逾期标记需结合当前执行情况核实。",
                            "source_refs": [ref],
                            "measure_refs": [],
                        }
                    )
        result.append(
            {
                "group_id": group.group_id,
                "progress": _pack(progress),
                "risks": _pack(risks),
                "candidates": list({item_key(row): row for row in candidates}.values()),
            }
        )
    return result


def _selected_context(group, tasks, facts):
    refs = {ref for row in tasks for ref in row["source_refs"]}
    rows = []
    for source in facts.evidence:
        if source.source_ref in refs and source.kind == "opportunity":
            row, _ = _business_context(source)
            rows.append(row)
    return _pack([*group["progress"], *rows])


def candidate_sections(facts, *, allowed_refs, accepted=None):
    """Combine may retain only exact choices accepted in the preceding drafts."""
    return {
        f"review_{index}_next": [
            deepcopy(row)
            for row in group["candidates"]
            if set(row["source_refs"]) <= allowed_refs
            and (accepted is None or item_key(row) in accepted.get(f"review_{index}_next", set()))
        ]
        for index, group in enumerate(catalog(facts))
    }


def accepted_choices(summaries):
    result = {}
    for draft in summaries:
        for section in draft["sections"]:
            if section["id"].startswith("review_") and section["id"].endswith("_next"):
                result.setdefault(section["id"], set()).update(item_key(row) for row in section["items"])
    return result


def validate_choices(wire, facts, *, allowed_refs, expected_sections, accepted=None):
    if [(s.id, s.title) for s in wire.sections] != [(s["id"], s["title"]) for s in expected_sections]:
        raise ValueError("Review proposal section drift")
    if wire.missing_data != facts.missing_data:
        raise ValueError("Review proposal coverage drift")
    candidates = candidate_sections(facts, allowed_refs=allowed_refs, accepted=accepted)
    for section in wire.sections:
        allowed = {item_key(row) for row in candidates.get(section.id, [])}
        keys = [item_key(row.model_dump(mode="json", by_alias=True)) for row in section.items]
        if len(keys) > 20 or len(keys) != len(set(keys)) or not set(keys) <= allowed:
            raise ValueError("Review proposal is not an exact evidence-bound choice")


def assemble(wire, facts):
    """Called once for final generation, never on a stored historical report."""
    selected = {section.id: section.items for section in wire.sections}
    groups = []
    for index, group in enumerate(catalog(facts)):
        tasks = [
            {
                "kind": "suggestion",
                "requires_confirmation": True,
                "text": row.text,
                "source_refs": list(row.source_refs),
                "measure_refs": [],
            }
            for row in selected[f"review_{index}_next"]
        ]
        groups.append(
            {key: deepcopy(group[key]) for key in ("group_id", "progress", "risks")} | {"next_week_tasks": tasks}
        )
        groups[-1]["progress"] = _selected_context(group, tasks, facts)
    return WeeklyReviewContent(groups=groups, missing_data=list(facts.missing_data))


def validate_generated(content, facts):
    """Persistence fence: exact frozen facts, exact candidates, no repair."""
    expected = catalog(facts)
    if content.missing_data != facts.missing_data or len(content.groups) != len(expected):
        raise ValueError("Review generated facts shape mismatch")
    for actual, group in zip(content.groups, expected, strict=True):
        group = {
            **group,
            "progress": _selected_context(
                group, [row.model_dump(mode="json") for row in actual.next_week_tasks], facts
            ),
        }
        for key in ("group_id", "progress", "risks"):
            if actual.model_dump(mode="json")[key] != group[key]:
                raise ValueError("Review generated facts differ from frozen catalog")
        allowed = {item_key(row) for row in group["candidates"]}
        seen = set()
        for row in actual.next_week_tasks:
            key = item_key(
                {"kind": row.kind, "text": row.text, "source_refs": row.source_refs, "metric_refs": row.measure_refs}
            )
            if not row.requires_confirmation or key not in allowed or key in seen:
                raise ValueError("Review generated suggestion differs from frozen catalog")
            seen.add(key)
