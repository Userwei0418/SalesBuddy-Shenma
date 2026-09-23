"""One result contract for platform and direct inference, before persistence."""

import math

from sales_backend.contracts.opportunity_candidate import validate_candidate
from sales_backend.domain.visit_contract import ensure_visit_result
from sales_backend.integrations.senseaudio import SenseAudioError
from sales_backend.services.agent_platform.routing import InvalidAgentResult

PRIVATE_FIELDS = {
    "provider",
    "fallback_reason",
    "agent_id",
    "revision",
    "snapshot_id",
    "approved_snapshot_id",
    "model_ref",
    "inference_route",
    "execution_mode",
    "expected_snapshot_id",
    "actual_snapshot_id",
    "runtime_snapshot_verified",
    "platform_run",
    "operation_id",
    "tool_authority_issued",
}


def validate_run_result(run, result, facts):
    from sales_backend.services.agent_run.contract import ensure_instant_summary_contract
    from sales_backend.services.agent_run.materialize import RISK_TYPE_CODES, SEVERITY_LABELS, TASK_PRIORITY_CODES

    if not isinstance(result, dict):
        raise InvalidAgentResult("AI未返回结构化结果")
    result = {key: value for key, value in result.items() if key not in PRIVATE_FIELDS}
    try:
        if run.mode == "visit_entry":
            if facts.get("visit_stage"):
                from sales_backend.contracts.visit_flow import validate_stage_result

                return validate_stage_result(result, facts)
            from sales_backend.domain.visit_review import review_text

            result = ensure_visit_result(result, facts.get("company_policy"), server_fields=facts.get("server_fields"))
            if run.text.startswith("拜访审核 v2\n") and review_text(result["fields"]) != run.text:
                raise ValueError("审核结果改写了本次正文或首次拜访字段")
            return result
        if run.mode == "opportunity_draft":
            return validate_candidate(result, facts)
        if run.mode == "operating_report":
            return ensure_instant_summary_contract(run, result, facts)
        if run.mode in {"chatbi", "customer_chatbi"}:
            if not isinstance(result.get("summary"), str) or not result["summary"].strip():
                raise ValueError("问数结果缺少说明")
            # Existing ChatBI accepts JSON numbers for metric values. Normalize
            # those scalars for the card contract instead of rejecting a valid
            # answer from BOTH providers merely because a count was not quoted.
            if isinstance(result.get("metrics"), list):
                metrics = []
                for item in result["metrics"]:
                    if isinstance(item, dict):
                        item = item.copy()
                        value = item.get("value")
                        if type(value) in {int, float} and (isinstance(value, int) or math.isfinite(value)):
                            item["value"] = str(value)
                    metrics.append(item)
                result["metrics"] = metrics
            for key, fields in (("metrics", ("label", "value")), ("rows", ("title", "detail"))):
                items = result.get(key)
                if not isinstance(items, list) or any(
                    not isinstance(item, dict) or any(not isinstance(item.get(field), str) for field in fields)
                    for item in items
                ):
                    raise ValueError("问数结果格式不完整")
            scope = facts.get("scope") or {}
            result["scope"] = str(
                scope.get("scope_label")
                or {
                    "self": "本人",
                    "team": "当前团队",
                    "workspace": "当前工作空间",
                }.get(run.actor.data_scope.value, "当前权限范围")
            )
            result["data_as_of"] = facts["data_as_of"]
        elif run.mode == "today_tasks":
            existing = {(str(item["source_type"]), str(item["source_id"])) for item in facts.get("active_tasks", [])}
            candidates = {
                (str(item["source_type"]), str(item["source_id"])) for item in facts.get("follow_up_candidates", [])
            }
            # Existing follow-ups share their source_type with new candidates.
            # Only the trusted facts membership selects the read-only contract;
            # conflicting membership must not hide a proposed task's validation.
            if existing & candidates:
                raise ValueError("今日待办的已有任务与新候选来源重叠")
            visible = existing | candidates
            items = result.get("ordered_items")
            if not isinstance(items, list):
                raise ValueError("今日待办缺少排序结果")
            seen = set()
            normalized = []
            for item in items:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("source_type"), str)
                    or not isinstance(item.get("source_id"), str)
                    or (item["source_type"], item["source_id"]) not in visible
                ):
                    raise ValueError("今日待办引用了本次事实以外的对象")
                key = (item["source_type"], item["source_id"])
                if key in seen:
                    raise ValueError("今日待办重复引用同一来源")
                seen.add(key)
                if item.get("reason") is not None and not isinstance(item["reason"], str):
                    raise ValueError("今日待办排序说明格式不合法")
                if key in existing:
                    # The model ranks an existing task, never restates its
                    # official fields. Even malformed extra dates/priorities are
                    # untrusted and unused; the final card re-queries the DB.
                    normalized.append(
                        {
                            field: item[field]
                            for field in (
                                "source_type",
                                "source_id",
                                "reason",
                            )
                            if field in item
                        }
                    )
                    continue
                if not isinstance(item.get("priority"), str) or item["priority"] not in TASK_PRIORITY_CODES:
                    raise ValueError("今日待办优先级不合法")
                if any(
                    item.get(field) is not None and not isinstance(item[field], str) for field in ("due_at", "reason")
                ):
                    raise ValueError("今日待办时间或排序说明格式不合法")
                # New candidates retain strict priority/source-date validation,
                # including candidates omitted from the model's ranking.
                normalized.append(
                    {
                        field: item[field]
                        for field in (
                            "source_type",
                            "source_id",
                            "priority",
                            "due_at",
                            "reason",
                        )
                        if field in item
                    }
                )
            if any(
                result.get(field) is not None and not isinstance(result[field], str) for field in ("title", "summary")
            ):
                raise ValueError("今日待办标题或说明格式不合法")
            result = {field: result[field] for field in ("title", "summary") if field in result}
            result["ordered_items"] = normalized
            from sales_backend.domain.follow_up_schedule import validate_today_task_dates

            validate_today_task_dates(normalized, facts)
        elif run.mode == "personal_risks":
            visible = {str(item["source_visit_id"]) for item in facts.get("visits", [])}
            items = result.get("risks")
            if not isinstance(items, list) or len(items) > 8:
                raise ValueError("风险结果条目须为最多8项的列表")
            seen = set()
            for item in items:
                if not isinstance(item, dict) or str(item.get("source_visit_id")) not in visible:
                    raise ValueError("风险引用了本次事实以外的拜访")
                if item.get("risk_type") not in RISK_TYPE_CODES or item.get("severity") not in SEVERITY_LABELS:
                    raise ValueError("风险类型或级别不合法")
                key = (str(item["source_visit_id"]), item["risk_type"])
                if key in seen:
                    raise ValueError("同一拜访的同类风险重复")
                seen.add(key)
                if any(
                    not isinstance(item.get(key), str) or not item[key].strip()
                    for key in ("description", "evidence_detail")
                ):
                    raise ValueError("风险缺少描述或证据")
                if any(
                    item.get(field) is not None and not isinstance(item[field], str)
                    for field in ("title", "suggested_action")
                ):
                    raise ValueError("风险标题或建议格式不合法")
            if result.get("title") is not None and not isinstance(result["title"], str):
                raise ValueError("风险清单标题格式不合法")
        return result
    except (ValueError, SenseAudioError) as exc:
        raise InvalidAgentResult(str(exc)) from exc


def valid_map_score(value):
    if isinstance(value, bool):
        raise ValueError("作战地图分数不能是布尔值")
    score = float(value)
    if not math.isfinite(score) or not 0 <= score <= 100:
        raise ValueError("作战地图分数必须为0到100之间的有限数字")
    return round(score, 1)
