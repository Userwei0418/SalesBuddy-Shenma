"""Only recorded field differences are Review events; absence is not history."""

import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

LABEL_FIELDS = {
    "商机名称": "name", "ACV（万元）": "amount", "预计关单日期": "expected_close_date",
    "所属伙伴": "partner_name", "销售渠道": "sales_channel", "产品线": "product_line",
    "跟进计划": "follow_up_plan", "商机阶段": "stage_label",
}
FIELD_LABELS = {
    "amount": "ACV", "currency": "币种", "status": "商机状态", "probability": "赢单概率",
    "stage_code": "商机阶段", "owner_id": "负责人", "team_id": "所属团队",
    "recognized_amount": "预测含税确收", "collection_amount": "预测回款",
    "name": "商机名称", "collection_confidence": "回款信心度", "owner_user_ref_id": "负责人",
    "owner_team_id": "所属团队", "customer_id": "客户", "expected_close_date": "预计关单日期",
    "expected_close_year": "预计关单年份", "expected_close_quarter": "预计关单季度",
    "partner_id": "所属伙伴", "partner_name": "伙伴名称", "associated_partner_ids": "关联伙伴（单项）",
    "sales_channel": "销售渠道", "product_line": "产品线", "follow_up_plan": "跟进计划",
    "source_code": "来源", "customer_source": "商机来源", "closed_at": "关闭时间",
    "priority": "优先级", "is_key": "重点商机", "trial_status": "试用状态", "summary": "简介",
    "note": "备注", "ppl_status": "PPL状态", "external_system_status": "外部系统状态",
    "primary_fde_id": "主责FDE", "fde_support_stage": "FDE支持阶段", "fde_progress": "FDE进展",
    "ownership_resolution": "归属确认状态", "user_ref_id": "参与同事", "participant_role": "参与职责",
    "valid_from": "参与开始", "valid_to": "参与结束", "reference_no": "报价编号", "title": "报价标题",
    "url": "报价链接", "material_id": "物料", "line_no": "物料行号", "quantity": "数量",
    "unit_price": "单价", "line_amount": "行金额", "usage": "用途", "expected_delivery_on": "预计交付日",
    "expected_start_on": "预计开始日", "expected_oa_on": "预计OA日", "service_months": "服务月数",
    "expected_account_count": "预计账号数", "expected_consumption_amount": "预计消耗金额",
    "expected_consumption_year": "预计消耗年份", "expected_consumption_quarter": "预计消耗季度",
    "is_active": "物料是否有效", "year": "预测年份", "quarter": "预测季度",
}
METADATA_FIELDS = frozenset({
    "field_edit_reason", "material_reason", "version_no", "updated_at", "created_at",
})
MONEY_FIELDS = frozenset({"amount", "recognized_amount", "collection_amount"})
FORECAST_LABEL = re.compile(r"^(\d{4}) Q([1-4]) (预测含税确收|预测回款|回款信心度)(?:（万元）)?$")


def _equal(before, after, field):
    if field in MONEY_FIELDS | {"probability"} and before is not None and after is not None:
        try:
            return Decimal(str(before)) == Decimal(str(after))
        except InvalidOperation:
            pass
    return before == after


def _change(raw, *, before_known=True, after_known=True):
    if (not isinstance(raw, dict) or "before" not in raw or "after" not in raw
            or not before_known or not after_known):
        # Unknown history belongs in coverage, never in a displayed null delta.
        return None
    label = raw.get("label") if isinstance(raw.get("label"), str) else None
    field = raw.get("field") if isinstance(raw.get("field"), str) else LABEL_FIELDS.get(label)
    match = FORECAST_LABEL.fullmatch(label or "")
    year, quarter = raw.get("year"), raw.get("quarter")
    if match:
        year, quarter = int(match[1]), int(match[2])
        field = {"预测含税确收": "recognized_amount", "预测回款": "collection_amount",
                 "回款信心度": "collection_confidence"}[match[3]]
    if field in METADATA_FIELDS or not (field or label) or (
        before_known and after_known and _equal(raw["before"], raw["after"], field)
    ):
        return None
    return {
        "field": field, "label": label or FIELD_LABELS.get(field, field),
        "before": raw["before"], "after": raw["after"],
        "before_known": before_known, "after_known": after_known,
        "unit": raw.get("unit") or ("万元" if "（万元）" in (label or "") else "元" if field in MONEY_FIELDS else None),
        "year": year, "quarter": quarter,
    }


def event_changes(event):
    raw = event.get("changes")
    if event.get("kind") == "opportunity_audit":
        # The definer has already projected an explicit field whitelist and
        # withheld restricted values. Unknown sides stay visibly unknown.
        changes = []
        for item in raw if isinstance(raw, list) else []:
            if item.get("before_known") and item.get("after_known"):
                change = _change(item)
            else:
                change = {**item, "label": FIELD_LABELS.get(item["field"], item["field"]),
                          "unit": "元" if item["field"] in MONEY_FIELDS else None}
            if change is not None:
                changes.append(change)
        return changes
    if event.get("kind") in {"business_change", "feishu_notification"}:
        candidates = [(item, True, True) for item in raw] if isinstance(raw, list) else []
    elif isinstance(raw, dict):
        before, after = raw.get("before"), raw.get("after")
        # Forecast constraints prove whole-record absence for INSERT/DELETE.
        # A missing key inside an existing image is still unknown, not null.
        before_absent = (event.get("kind") == "opportunity_forecast" and "before" in raw
                         and before is None and isinstance(after, dict))
        after_absent = (event.get("kind") == "opportunity_forecast" and "after" in raw
                        and after is None and isinstance(before, dict))
        before, after = before if isinstance(before, dict) else {}, after if isinstance(after, dict) else {}
        if event.get("kind") == "opportunity_state":
            before = {**(before.get("state") or {}), **{k: v for k, v in before.items() if k != "state"}}
            after = {**(after.get("state") or {}), **{k: v for k, v in after.items() if k != "state"}}
        candidates = [({
            "field": "stage_code" if field == "stage" else field,
            "before": before.get(field), "after": after.get(field),
            "year": raw.get("year"), "quarter": raw.get("quarter"),
        }, field in before or before_absent, field in after or after_absent)
            for field in sorted(before.keys() | after.keys())]
    else:
        candidates = []
    changes, seen = [], set()
    for raw_change, before_known, after_known in candidates:
        change = _change(raw_change, before_known=before_known, after_known=after_known)
        if change is not None:
            key = json.dumps(change, sort_keys=True, ensure_ascii=False, default=str)
            if key not in seen:
                changes.append(change)
                seen.add(key)
    return changes


def _same_change(left, right):
    return all(c[k] for c in (left, right) for k in ("before_known", "after_known")) and (
        left["field"] is not None and all(left[k] == right[k] for k in ("field", "unit", "year", "quarter"))
    ) and (
        _equal(left["before"], right["before"], left["field"])
        and _equal(left["after"], right["after"], left["field"])
    )


def _time(event):
    value = event["occurred_at"]
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def opportunity_changes(rows):
    """Clean before counts/paging; coalesce only proven same-version fields."""
    result = []
    for row in rows:
        events = []
        for original in row["events"]:
            changes = event_changes(original)
            if changes or original.get("observed_business_change"):
                ref = {"kind": original["kind"], "id": original["id"]}
                events.append({**original, "event_id": original["id"], "changes": changes, "source_refs": [ref]})
        business = [e for e in events if e["kind"] == "business_change"]
        retained = []
        for event in events:
            if event["kind"] == "opportunity_state" and event.get("version_no") is not None:
                remaining = []
                for change in event["changes"]:
                    match = next((b for b in business if b.get("version_no") == event["version_no"]
                                  and any(_same_change(change, c) for c in b["changes"])), None)
                    if match is None:
                        remaining.append(change)
                    elif event["source_refs"][0] not in match["source_refs"]:
                        match["source_refs"].extend(event["source_refs"])
                event = {**event, "changes": remaining}
            if event["changes"] or event.get("observed_business_change"):
                retained.append(event)
        if retained:
            retained.sort(key=lambda e: (_time(e), e["event_id"]))
            refs = {json.dumps(ref, sort_keys=True): ref for e in retained for ref in e["source_refs"]}
            result.append({**row, "events": retained, "event_count": len(retained),
                           "occurred_at": retained[-1]["occurred_at"], "source_refs": list(refs.values())})
    result.sort(key=lambda item: item["id"])
    result.sort(key=lambda item: _time(item), reverse=True)
    return result


def audited_opportunity_changes(rows):
    """Count witnessed updates even if a value or actor cannot be disclosed."""
    items = opportunity_changes(rows)
    for item in items:
        missing = set()
        actors = set()
        for event in item["events"]:
            reasons = []
            if event.get("omitted_field_count", 0):
                reasons.append("fields_outside_review_projection")
            if any(not c.get("before_known") or not c.get("after_known") for c in event["changes"]):
                reasons.append("field_value_unavailable")
            event["detail_coverage"] = dict(status="partial" if reasons else "complete", reason_codes=reasons)
            missing.update(reasons)
            if event.get("actor_status") != "available":
                actors.add("actor_" + event.get("actor_status", "not_recorded"))
        item["detail_coverage"] = dict(status="partial" if missing else "complete", reason_codes=sorted(missing))
        item["actor_coverage"] = dict(status="partial" if actors else "complete", reason_codes=sorted(actors))
    return items


def merge_update_history(legacy, captured):
    """The two inputs cover disjoint time ranges; deduplicate business IDs."""
    by_id = {item["id"]: dict(item) for item in legacy}
    for item in captured:
        old = by_id.get(item["id"])
        if old is None:
            by_id[item["id"]] = item
            continue
        events = {event["event_id"]: event for event in [*old["events"], *item["events"]]}
        combined = sorted(events.values(), key=lambda event: (_time(event), event["event_id"]))
        refs = {json.dumps(ref, sort_keys=True): ref for event in combined for ref in event["source_refs"]}
        by_id[item["id"]] = {**item, "events": combined, "event_count": len(combined),
                             "source_refs": list(refs.values()), "occurred_at": combined[-1]["occurred_at"],
                             "detail_coverage": {"status": "partial", "reason_codes": ["legacy_detail_coverage"]},
                             "actor_coverage": {"status": "partial", "reason_codes": ["legacy_actor_coverage"]}}
    return sorted(by_id.values(), key=lambda item: (_time(item), item["id"]), reverse=True)


def history_coverage(*, has_records):
    return {
        "status": "partial" if has_records else "unavailable", "complete_from": None,
        "reason_codes": ["history_field_coverage_unverified", "legacy_state_subset",
                         "forecast_confidence_not_captured", "event_actor_not_recorded"],
        "message": "仅显示已留存且可核验的商机变化；现有历史不能证明全部业务字段和操作人均完整记录",
    }
