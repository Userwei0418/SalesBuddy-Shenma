"""Generate a private report candidate from one backend-owned facts snapshot."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from time import monotonic

from sales_backend.contracts.work_report import GenerationInput, GenerationResult, GenerationTrace, ReportContent
from sales_backend.domain.work_report import validate_generation_result
from sales_backend.domain.work_report_metrics import metric_statement
from sales_backend.services.agent_platform.routing import AgentUnavailable, ProviderUnavailable, RoutingPolicy

from . import review_daily_bindings as daily_bindings
from . import review_fact_bindings as bindings
from .content import (
    basic_summary,
    known_gaps,
    metric_fragment,
    model_output_schema,
    overview_material,
    project_overview,
    sections_for,
    validate_model_content,
)
from .engine import GenerationStateUnavailable, route_generation
from .prompts import SYSTEM_PROMPT
from .providers import AssertActive, GenerationProviders
from .retained_facts import retain_frozen_facts
from .review import (
    augment_review_envelope,
    combined_review_content,
    review_material,
    validate_combined_review_wire,
)
from .weekly import project_weekly

PROMPT_VERSION = "work_report.references_repair.v2_20261026"
BATCH_CHARACTERS = 24000
SUMMARY_CHARACTERS = 6000
MERGE_WIDTH = 4


def _batches(request):
    batches, current, size = [], [], 0
    for row in _material(request):
        length = len(json.dumps(row, ensure_ascii=False))
        if current and size + length > BATCH_CHARACTERS:
            batches.append(current)
            current, size = [], 0
        current.append(row)
        size += length
    if current:
        batches.append(current)
    return batches or [[]]  # Non-zero metric-only reports still have one generation call.


def _material(request):
    review = review_material(request)
    if request.review_mode == "daily":
        return review
    return ([source.model_dump(mode="json") for source in [*request.sources, *request.supplements]]
            + overview_material(request) + review)


def _call_count(count):
    total = count
    while count > 1:
        count = (count + MERGE_WIDTH - 1) // MERGE_WIDTH
        total += count
    return total


def _envelope(request, *, material, summaries, phase, index, total, text_limit):
    data = request.model_dump(mode="json")
    if request.rewrite:
        selected = set(request.rewrite.section_ids)
        data["rewrite"]["previous_content"]["sections"] = [
            s for s in data["rewrite"]["previous_content"]["sections"] if s["id"] in selected
        ]
        for section in data["rewrite"]["previous_content"]["sections"]:
            for item in section["items"]:
                item.pop("source_notes", None)
    data["sources"] = [row for row in material if not row["source_id"].startswith(("user:", "overview:"))]
    data["supplements"] = [row for row in material if row["source_id"].startswith("user:")]
    data["summaries"] = summaries
    data["expected_sections"] = [{"id": s.id, "title": s.title} for s in sections_for(request)]
    data["required_missing_data"] = known_gaps(request)
    data["output_text_limit"] = text_limit
    data["generation_constraints"] = {
        "allowed_source_refs": [row["source_id"] for row in material],
        "user_supplement_refs": [row["source_id"] for row in material if row["source_id"].startswith("user:")],
        "metric_tokens": {m.metric_id: "{{metric:" + m.metric_id + "}}" for m in request.metrics
                          if m.complete and m.value is not None},
        "metric_fragments": {m.metric_id: metric_fragment(m) for m in request.metrics
                             if m.complete and m.value is not None},
        "metric_statements": {m.metric_id: metric_statement(m) for m in request.metrics
                              if m.complete and m.value is not None},
        "rules": [
            "每个条目必须引用allowed_source_refs或metric_tokens中的ID；快照ID、记录ID不是source_refs。",
            "引用任何user:来源的条目kind只能为user_supplement，包括用户声明的成果、计划和背景。",
            "无依据章节保留items=[]；不能用无引用的暂无数据句子填充。缺口只写missing_data。",
            "metric_refs只列正文实际插入的metric_tokens键，正文原样保留占位符，不替换为数值。",
            "正文必须逐字复制metric_fragments中的完整片段（占位符+必要单位），包括空格，不得只写数字。",
            "source_refs为空时，text只能逐字选用metric_statements中的完整统计句；多句仅用换行连接，"
            "不得增删标签、口径或追加解释。非计划统计kind只能为fact或summary，含计划统计只能为plan或summary。",
            "自由业务分析、风险和建议须有本条对应的当前批次真实业务source_refs；统计不能支撑自由扩写或建议。"
            "不得为通过校验借用无关来源或把全局引用附到每条。",
            "没有可用统计占位符时metric_refs=[]，统计不完整的说明只写missing_data。",
            "已有计划用plan；模型新提出的动作只能用suggestion，不能冒充已商定计划。",
        ],
    }
    project_weekly(data, request, {row["source_id"] for row in material}, data["expected_sections"])
    project_overview(data, request, {row["source_id"] for row in material}, data["expected_sections"], phase=phase)
    # The public schema also supports manual edits. Models get its stricter
    # generated subset, still validated against the same backend-owned schema.
    schema = model_output_schema()
    kinds = schema["$defs"]["ReportItem"]["properties"]["kind"]["enum"]
    kinds.remove("manual")
    schema["$defs"]["ReportItem"]["required"] = ["kind", "text", "source_refs", "metric_refs"]
    item_properties = schema["$defs"]["ReportItem"]["properties"]
    for key, values in (("source_refs", data["generation_constraints"]["allowed_source_refs"]),
                        ("metric_refs", list(data["generation_constraints"]["metric_tokens"]))):
        item_properties[key]["uniqueItems"] = True
        if values:
            item_properties[key]["items"]["enum"] = values
        else:
            item_properties[key]["maxItems"] = 0
    envelope = {
        "contract_version": request.contract_version,
        "capability": "work_report",
        "prompt_version": PROMPT_VERSION,
        "system_prompt": SYSTEM_PROMPT,
        "output_contract": schema,
        "output_template": {
            "title": (request.rewrite.previous_content.title if request.rewrite
                      else ("工作报告 · " + request.period.label)[:240]),
            "sections": [{"id": section["id"], "title": section["title"], "items": []}
                         for section in data["expected_sections"]],
            "missing_data": list(data["required_missing_data"]),
        },
        "input": data,
        "generation": {"phase": phase, "batch_index": index, "batches_total": total},
    }
    if request.review_mode:
        envelope["prompt_version"] = "work_report.review_wire.v1_20261014"
        return augment_review_envelope(envelope, request, material)
    return envelope


def _policy(original, remaining):
    if remaining <= 0.01:
        raise AgentUnavailable("work-report generation budget exhausted")
    return RoutingPolicy(
        platform_enabled=original.platform_enabled,
        total_seconds=remaining,
        platform_seconds=min(original.platform_seconds, remaining / 2),
        receipt_seconds=min(original.receipt_seconds, remaining / 6),
    )


def _fallback_code(reason):
    return {"ProviderUnavailable": "provider_unavailable", "InvalidAgentResult": "invalid_agent_result",
            "TimeoutError": "timeout"}.get(reason, "provider_unavailable") if reason else None


def generation_issue(failure=None):
    """Only fixed public diagnostics; never expose model output or trace details."""
    failure = failure or {}
    if failure.get("contract_code") == "work_report.missing_source_refs":
        code, message = "AI_SOURCE_REFERENCES_MISSING", "AI未提供有效的材料引用，本次仅保留基础汇总。"
    elif failure.get("error_code") == "InvalidAgentResult":
        code, message = "AI_OUTPUT_INVALID", "AI输出未通过结构或事实校验，本次仅保留基础汇总。"
    elif failure.get("error_code") == "TimeoutError":
        code, message = "AI_GENERATION_TIMEOUT", "AI未在规定时间内完成，本次仅保留基础汇总。"
    elif failure.get("error_code") == "ProviderUnavailable":
        code, message = "AI_PROVIDER_UNAVAILABLE", "AI服务暂不可用，本次仅保留基础汇总。"
    else:
        code, message = "AI_RESULT_UNAVAILABLE", "本次未取得可用的AI报告，当前内容为基础汇总。"
    return {"code": code, "message": message, "retryable": True}


async def _active_before_deadline(check, deadline):
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise GenerationStateUnavailable("work-report authority budget exhausted")
    task = asyncio.create_task(check())

    def consume_late(completed):
        if not completed.cancelled():
            completed.exception()

    try:
        done, _ = await asyncio.wait({task}, timeout=remaining)
        if not done:
            raise GenerationStateUnavailable("work-report authority check timed out")
        await task
        if monotonic() >= deadline:
            raise GenerationStateUnavailable("work-report authority budget exhausted")
    except (TimeoutError, ProviderUnavailable) as exc:
        raise GenerationStateUnavailable("work-report authority could not be verified") from exc
    finally:
        if not task.done():
            # Do not wait for a callback that suppresses cancellation. Its late
            # result cannot authorize a model call or report delivery.
            task.cancel()
            task.add_done_callback(consume_late)


async def generate_work_report(
    request: GenerationInput,
    *,
    providers: GenerationProviders,
    assert_active: AssertActive,
    routing_policy: RoutingPolicy,
    observer=None,
    renew_budget_on_progress: bool = False,
) -> GenerationResult:
    """No persistence, network client, credential loading or implicit job retry.

    By default all calls share one wall-clock budget. The report job can opt
    into a new bounded window after each accepted batch; failed routes, retries
    and heartbeats never renew it. Cancellation, permission
    changes and uncertain operation receipts propagate without basic-summary
    fallback. Backend must recheck cancellation and document CAS on persistence.
    """
    started = monotonic()
    deadline = started + routing_policy.total_seconds
    # Defend against callers passing a mutated/model_construct'ed Pydantic object.
    request = GenerationInput.model_validate(request.model_dump(mode="python"))

    async def active():
        await _active_before_deadline(assert_active, deadline)

    await active()
    has_material = bool(request.sources or any(s.text.strip() for s in request.supplements)
                        or overview_material(request)) or any(
        m.complete and m.value is not None and Decimal(m.value) != 0 for m in request.metrics
    )
    if request.review_mode == "daily":
        has_material = bool(review_material(request))
    elif request.review_mode:
        has_material = has_material or bool(review_material(request))
    coverage = "partial" if (any(c.status == "partial" for c in request.coverage)
                             or (request.review_facts and request.review_facts.missing_data)) else "complete"
    calls_completed = 0
    calls_total = _call_count(len(_batches(request))) if has_material else 0
    route_history = []
    repair_attempts = 0
    last_failure = {}
    attempt_policy = routing_policy

    async def finish(status, provider, content, reason=None):
        await active()
        if status == "generated" and monotonic() >= deadline:
            raise AgentUnavailable("work-report generation budget exhausted")
        result = GenerationResult(
            status=status, provider=provider, snapshot_id=request.snapshot_id, snapshot_hash=request.snapshot_hash,
            scheme_id=request.scheme.scheme_id, scheme_version=request.scheme.version_no,
            coverage_status=coverage, content=content,
            trace=GenerationTrace(prompt_version=(
                daily_bindings.PROMPT_VERSION
                if request.review_facts and daily_bindings.enabled(request.review_facts, request.review_mode)
                else "work_report.review_bound_choices.v2_20261016"
                if request.review_facts and bindings.enabled(request.review_facts, request.review_mode)
                else "work_report.review_wire.v1_20261014" if request.review_mode else PROMPT_VERSION),
                                  fallback_reason=reason,
                                  generation_issue=(generation_issue(last_failure) if status == "basic_summary" else None),
                                  repair_attempts=repair_attempts,
                                  elapsed_ms=max(0, round((monotonic() - started) * 1000)),
                                  chunks_total=calls_total, chunks_completed=calls_completed),
        )
        validate_generation_result(result, request)
        await active()
        if status == "generated" and monotonic() >= deadline:
            raise AgentUnavailable("work-report generation budget exhausted")
        return result

    if not has_material:
        return await finish("insufficient_data", "none", None)

    all_material = {row["source_id"]: row for row in _material(request)}

    async def invoke(material, summaries, phase, final):
        nonlocal calls_completed, attempt_policy, deadline, repair_attempts, last_failure
        index = calls_completed
        allowed = {s["source_id"] for s in material}
        text_limit = 64000 if final else SUMMARY_CHARACTERS
        envelope = _envelope(request, material=material, summaries=summaries, phase=phase,
                             index=index, total=calls_total, text_limit=text_limit)

        def observe(event, provider, **details):
            nonlocal last_failure
            if event == "route_failed":
                last_failure = {k: details[k] for k in ("error_code", "contract_code") if k in details}
            if observer:
                observer(event, provider, **details, chunk_index=index, generation_phase=phase)

        validator = validate_combined_review_wire if request.review_mode else validate_model_content
        validation_options = {}
        if request.review_facts and bindings.enabled(request.review_facts, request.review_mode) and phase == "combine":
            validation_options["accepted_choices"] = bindings.accepted_choices(summaries)
        if (request.review_facts and daily_bindings.enabled(request.review_facts, request.review_mode)
                and phase == "combine"):
            validation_options["accepted_daily_claims"] = daily_bindings.accepted_claims(summaries)
        async def dispatch(current_envelope, policy):
            return await route_generation(
                current_envelope, providers=providers, assert_active=active,
                routing_policy=_policy(policy, deadline - monotonic()), observer=observe,
                deadline=deadline,
                validate=lambda value: validator(value, request, allowed_refs=allowed, text_limit=text_limit,
                                                 **validation_options),
            )

        try:
            routed = await dispatch(envelope, attempt_policy)
        except AgentUnavailable:
            # One extra model call across the entire generation, not per chunk.
            # Never repair transport/authority failures or renew a failed budget.
            if (request.review_mode or repair_attempts or last_failure.get("error_code") != "InvalidAgentResult"
                    or deadline - monotonic() <= 0.01):
                raise
            repair_attempts += 1
            repaired = deepcopy(envelope)
            repaired["generation"]["repair_attempt"] = 1
            repaired["generation"]["previous_contract_code"] = last_failure.get("contract_code", "work_report.invalid_content")
            repaired["system_prompt"] += (
                "\n这是唯一一次格式纠正。仅从本次冻结原材料重新生成完整JSON，不复用未通过校验的草稿。"
                "逐条核对kind/text/source_refs/metric_refs；真实业务摘要必须引用本条确实使用的source_id，"
                "不得省略引用、借用无关引用或创造ID。统计占位符必须与metric_refs逐一相等。"
                "无依据时保留空章节，不补造内容；missing_data原样返回。"
            )
            routed = await dispatch(repaired, replace(attempt_policy, platform_enabled=False))
        if monotonic() >= deadline:
            raise AgentUnavailable("work-report generation budget exhausted")
        # Only an accepted direct result has crossed the router's seal-NONE
        # fence and fresh authority checks. Keep later batches/merges on direct
        # after transport failure; content failures and new attempts keep the
        # configured route. Progress renewal changes the deadline, not this latch.
        if routed.provider == "direct" and routed.fallback_reason in {"TimeoutError", "ProviderUnavailable"}:
            attempt_policy = replace(attempt_policy, platform_enabled=False)
        route_history.append(routed)
        calls_completed += 1
        if renew_budget_on_progress:
            deadline = monotonic() + routing_policy.total_seconds
        observe("report_chunk_accepted", routed.provider)
        return routed.payload

    try:
        batches = _batches(request)
        drafts = []
        for batch in batches:
            drafts.append(await invoke(batch, [], "compose" if len(batches) == 1 else "summarize",
                                       final=len(batches) == 1))
        while len(drafts) > 1:
            merged = []
            for offset in range(0, len(drafts), MERGE_WIDTH):
                group = drafts[offset:offset + MERGE_WIDTH]
                refs = {ref for draft in group for section in draft["sections"] for item in section["items"]
                        for ref in item["source_refs"]}
                # Merges receive validated summaries and the original provenance
                # index, not expanded raw material or invented intermediate IDs.
                index = [{k: deepcopy(v) for k, v in row.items() if k != "text"}
                         for key, row in all_material.items() if key in refs]
                merged.append(await invoke(index, group, "combine", final=len(drafts) <= MERGE_WIDTH))
            drafts = merged
        final = route_history[-1]
        reason = _fallback_code(final.fallback_reason)
        if any(r.fallback_reason for r in route_history[:-1]) or len({r.provider for r in route_history}) > 1:
            reason = "batch_fallback"
        if request.review_mode:
            content = combined_review_content(drafts[0], request)
            if request.review_mode == "weekly":
                content = retain_frozen_facts(content, request)
        else:
            content = retain_frozen_facts(ReportContent.model_validate(drafts[0]), request)
        return await finish("generated", final.provider, content, reason)
    except AgentUnavailable:
        if request.review_mode == "daily":
            raise  # R09 cannot present a non-model basic summary as AI output.
        # Only exhausted provider routes/budget reach this path. No partial AI
        # drafts are silently treated as a complete report or written anywhere.
        content = basic_summary(request)
        if getattr(request, "overview_facts", None):
            content = retain_frozen_facts(content, request)
        return await finish("basic_summary", "none", content, "providers_unavailable")
