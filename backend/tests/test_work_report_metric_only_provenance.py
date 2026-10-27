"""Ordinary report provenance: offline counterexamples and real boundary calls."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from sales_backend.contracts.work_report import GenerationInput, GenerationResult, ReportContent
from sales_backend.domain.model_contract import contract_failure_details
from sales_backend.domain.work_report import WorkReportError, render_content, validate_generation_result
from sales_backend.domain.work_report_metrics import metric_statement
from sales_backend.services.agent_platform.routing import InvalidAgentResult, RoutingPolicy
from sales_backend.services.work_report_generation import GenerationProviders, generate_work_report, service
from sales_backend.services.work_report_generation.content import validate_model_content
from sales_backend.services.work_report_generation.review import combined_review_content, validate_combined_review_wire
from tests.test_work_report_contract import content_data, request_data, result_data
from tests.test_work_report_review_fact_bindings import envelope
from tests.test_work_report_review_fact_bindings import request as weekly_request

ORIGINAL_BAD_ITEMS = [
    dict(kind="summary", text="本期正式跟进为 {{metric:visits_completed}} 条，"
         "相关取数缺口（如预测金额、历史基线、生效目标等）待补充核实。",
         source_refs=[], metric_refs=["visits_completed"]),
    dict(kind="suggestion", text="建议结合本期正式跟进 {{metric:visits_completed}} 条的情况，"
         "补充核实预测与目标相关数据，待人工确认。",
         source_refs=[], metric_refs=["visits_completed"]),
]


def request():
    data = request_data()
    data.update(sources=[], supplements=[])
    data["coverage"][0].update(total=0, included=0)
    data["metrics"][0].update(metric_id="visits_completed", label="本期正式跟进", unit="条",
                              value="0", display_value="0")
    return GenerationInput.model_validate(data)


def payload(req, item):
    value = content_data()
    value["sections"][0]["items"] = [deepcopy(item)]
    value["missing_data"] = []
    return value


def generated(req, content):
    data = result_data()
    data.update(snapshot_id=req.snapshot_id, snapshot_hash=req.snapshot_hash, scheme_id=req.scheme.scheme_id,
                scheme_version=req.scheme.version_no, coverage_status="partial" if req.review_facts else "complete",
                content=content)
    return GenerationResult.model_validate(data)


def statistic(req, *, kind="summary"):
    return dict(kind=kind, text=metric_statement(req.metrics[0]), source_refs=[],
                metric_refs=[req.metrics[0].metric_id])


@pytest.mark.parametrize("item", ORIGINAL_BAD_ITEMS)
def test_original_two_unsupported_items_fail_model_and_new_save_but_old_read_is_unchanged(item):
    req = request()
    raw = payload(req, item)
    before = deepcopy(raw)
    with pytest.raises(InvalidAgentResult) as error:
        validate_model_content(raw, req, allowed_refs=set())
    assert contract_failure_details(error.value)["contract_code"].startswith("work_report.metric_only.")
    with pytest.raises(WorkReportError, match="完整统计句"):
        validate_generation_result(generated(req, raw), req)
    old = ReportContent.model_validate(raw)
    assert item["text"].replace("{{metric:visits_completed}}", "0") in render_content(old, req)
    assert raw == before and old.model_dump(mode="json")["sections"] == before["sections"]


@pytest.mark.parametrize("kind", ["fact", "summary"])
def test_zero_complete_statistic_has_name_basis_value_unit_and_survives_save(kind):
    req = request()
    raw = payload(req, statistic(req, kind=kind))
    checked = validate_model_content(raw, req, allowed_refs=set())
    result = generated(req, checked)
    validate_generation_result(result, req)
    assert "本期已发生 · 本期正式跟进：0 条。" in render_content(result.content, req)


@pytest.mark.parametrize("fault", ["append", "prefix", "rename", "suggestion", "literal", "duplicate"])
def test_count_only_statement_cannot_become_analysis_or_action(fault):
    req = request()
    item = statistic(req)
    if fault == "append":
        item["text"] += "预测与目标缺口待核实。"
    elif fault == "prefix":
        item["text"] = "情况不佳，" + item["text"]
    elif fault == "rename":
        item["text"] = item["text"].replace("本期正式跟进", "本期成交")
    elif fault == "suggestion":
        item["kind"] = "suggestion"
    elif fault == "literal":
        item.update(text="本期已发生 · 本期正式跟进：0 条。", metric_refs=[])
    else:
        item["text"] += "\n" + item["text"]
    with pytest.raises(InvalidAgentResult):
        validate_model_content(payload(req, item), req, allowed_refs=set())
    with pytest.raises(WorkReportError):
        validate_generation_result(generated(req, payload(req, item)), req)


@pytest.mark.parametrize("kind", ["plan", "summary", "fact", "suggestion"])
def test_planned_statistic_keeps_its_period_and_kind_at_both_boundaries(kind):
    req = request()
    req.metrics[0].period_basis = "planned_in_period"
    raw = payload(req, statistic(req, kind=kind))
    if kind in {"plan", "summary"}:
        checked = validate_model_content(raw, req, allowed_refs=set())
        validate_generation_result(generated(req, checked), req)
    else:
        with pytest.raises(InvalidAgentResult):
            validate_model_content(raw, req, allowed_refs=set())
        with pytest.raises(WorkReportError):
            validate_generation_result(generated(req, raw), req)


def test_multiple_exact_statements_are_allowed_without_new_interpretation():
    req = request()
    req.metrics.append(req.metrics[0].model_copy(update={"metric_id": "other", "label": "另一完整统计"}))
    item = statistic(req)
    item["text"] += "\n" + metric_statement(req.metrics[1])
    item["metric_refs"].append("other")
    raw = payload(req, item)
    checked = validate_model_content(raw, req, allowed_refs=set())
    validate_generation_result(generated(req, checked), req)


@pytest.mark.parametrize("source_type,kind,text", [
    ("task", "summary", "任务记录为执行中，交付前仍需确认验收安排。"),
    ("task", "suggestion", "建议围绕任务中登记的验收安排核实当前阻碍，待人工确认。"),
    ("visit", "summary", "已确认客户需求，客户对交付安排仍有疑问。"),
    ("opportunity", "suggestion", "建议结合商机当前登记的试用安排与客户核实下一步。"),
])
def test_business_prose_is_preserved_with_its_own_current_batch_source(source_type, kind, text):
    data = request_data()
    data["supplements"] = []
    source = data["sources"][0]
    source.update(source_type=source_type, source_id=source_type + ":" + source["record_id"])
    source.update(text=text, state_kind="current" if source_type != "visit" else "occurred")
    for coverage in data["coverage"]:
        selected = coverage["source_type"] == source_type
        coverage.update(selected=selected, status="complete" if selected else "not_selected",
                        total=1 if selected else None, included=1 if selected else 0, statistics_complete=selected)
    req = GenerationInput.model_validate(data)
    item = dict(kind=kind, text=text, source_refs=[source["source_id"]], metric_refs=[])
    raw = payload(req, item)
    checked = validate_model_content(raw, req, allowed_refs={source["source_id"]})
    assert checked == raw
    validate_generation_result(generated(req, checked), req)
    with pytest.raises(InvalidAgentResult):
        validate_model_content(raw, req, allowed_refs=set())


@pytest.mark.parametrize("phase", ["compose", "summarize", "combine"])
@pytest.mark.parametrize("item", ORIGINAL_BAD_ITEMS)
def test_enhanced_weekly_partition_uses_same_closed_rule_each_phase(phase, item):
    req = weekly_request().model_copy(update={"sources": [], "supplements": [], "metrics": request().metrics})
    env = envelope(req, phase=phase)
    raw = deepcopy(env["output_template"])
    raw["sections"][2]["items"] = [deepcopy(item)]
    refs = {s.source_ref for s in req.review_facts.evidence}
    with pytest.raises(InvalidAgentResult):
        validate_combined_review_wire(raw, req, allowed_refs=refs)
    # Closed valid statistics can coexist with server-owned typed Review facts.
    raw["sections"][2]["items"] = [statistic(req)]
    assert env["input"]["generation_constraints"]["metric_statements"]["visits_completed"] == statistic(req)["text"]
    checked = validate_combined_review_wire(raw, req, allowed_refs=refs)
    content = combined_review_content(checked, req)
    validate_generation_result(generated(req, content), req)
    content.sections[2].items[0] = ReportContent.model_validate(payload(req, item)).sections[0].items[0]
    with pytest.raises(WorkReportError, match="完整统计句"):
        validate_generation_result(generated(req, content), req)


@pytest.mark.asyncio
async def test_real_multibatch_generator_rejects_merge_invention_preserving_sourced_business(monkeypatch):
    data = request_data()
    data["supplements"] = []
    second = deepcopy(data["sources"][0])
    second.update(source_id="task:" + data["snapshot_id"], record_id=data["snapshot_id"], source_type="task")
    data["sources"].append(second)
    next(c for c in data["coverage"] if c["source_type"] == "task").update(
        selected=True, status="complete", total=1, included=1, statistics_complete=True)
    req = GenerationInput.model_validate(data)
    monkeypatch.setattr(service, "BATCH_CHARACTERS", 1)

    def answer(env):
        raw = deepcopy(env["output_template"])
        refs = [s["source_id"] for s in env["input"]["sources"]]
        raw["sections"][0]["items"] = [dict(kind="summary", text="确认需求并核对任务执行安排。",
                                            source_refs=refs, metric_refs=[])]
        if env["generation"]["phase"] == "combine":
            item = statistic(req)
            item["text"] += "需补充预测金额。"
            raw["sections"][2]["items"] = [item]
        return raw

    result = await generate_work_report(req, providers=GenerationProviders(direct=AsyncMock(side_effect=answer)),
        assert_active=AsyncMock(), routing_policy=RoutingPolicy(platform_enabled=False, total_seconds=2,
                                                               platform_seconds=0.5, receipt_seconds=0.1))
    assert result.status == "basic_summary"
    assert result.trace.chunks_completed == 2  # Summaries passed; invented merge did not.
    assert all("需补充预测金额" not in item.text for section in result.content.sections for item in section.items)
