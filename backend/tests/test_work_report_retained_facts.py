"""Deterministic source retention; no inference about arbitrary prose truth."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from sales_backend.contracts.work_report import WEEK_SECTIONS, GenerationInput, ReportContent
from sales_backend.domain.model_contract import contract_failure_details
from sales_backend.domain.work_report import canonical_hash
from sales_backend.services.agent_platform.routing import InvalidAgentResult, RoutingPolicy
from sales_backend.services.work_report_generation import GenerationProviders, generate_work_report
from sales_backend.services.work_report_generation.retained_facts import retain_frozen_facts
from tests.test_work_report_metric_spacing import NOW, SOURCE, content, request
from tests.test_work_report_source_qualifier_wiring import fixed_response, source_request

OPP_ID = UUID(int=71)
OPP_REF = "opportunity:" + str(OPP_ID)


def quoted(text):
    req = request()
    req.sources[0].text = text
    body = ReportContent.model_validate(content("Model synopsis", refs=[]))
    before = body.model_dump(mode="json")
    result = retain_frozen_facts(body, req)
    assert body.model_dump(mode="json") == before
    assert req.sources[0].text == text
    return result.sections[0].items


@pytest.mark.parametrize("label", ["【演示材料】", "【假设情景】【未经验证】", "【已完成记录】"])
def test_opening_annotation_is_quoted_without_interpreting_its_truth(label):
    items = quoted(label + "已完成验证，结果符合要求。")
    assert len(items) == 2 and label in items[1].text
    assert items[1].source_refs == [SOURCE] and items[1].kind == "summary"
    assert "合成" not in items[1].text and "未完成" not in items[1].text


def test_weak_test_words_do_not_classify_real_completed_work():
    assert len(quoted("与测试联络员完成验收，结果符合要求。")) == 1


@pytest.mark.parametrize("raw", [
    "已完成演示。当前尚需核实弱网体验。",
    "并非尚未确认，而是已经确认且验证通过。",
    "待验证是原计划。\n现已完成，结果符合要求。",
])
def test_qualifier_quotes_include_negation_contrast_and_adjacent_context(raw):
    note = quoted(raw)[1]
    assert raw in note.text and "不代表完整限制清单" in note.text
    assert "确认的风险" not in note.text and note.source_refs == [SOURCE]


def test_long_context_is_not_cut_into_a_false_unresolved_claim():
    raw = "尚需核实" + "上下文" * 500 + "；后来已确认完成。"
    note = quoted(raw)[1]
    assert "上下文过长" in note.text and "须结合来源核对" in note.text
    assert "尚需核实" not in note.text  # No partial quote loses the later resolution.


def test_source_literal_tokens_cannot_become_metric_commands():
    note = quoted("【原始标注】尚未确认 {{metric:unknown}} 的含义。")[1]
    assert "{{metric:" not in note.text and "｛｛metric:unknown｝｝" in note.text
    assert note.metric_refs == []


def weekly_request(state="no_recorded_progress_in_week"):
    req = request()
    data = req.model_dump(mode="python")
    data["scheme"]["config"]["sections"] = [{"id": key, "title": key} for key in WEEK_SECTIONS]
    data["scheme"]["config_hash"] = canonical_hash(data["scheme"]["config"])
    data["sources"].append(dict(
        source_id=OPP_REF, source_type="opportunity", record_id=OPP_ID, record_version="1",
        business_at=None, observed_at=NOW, time_basis="observed_at", state_kind="current",
        title="Synthetic opportunity", text="当前阶段与下一步已记录。",
    ))
    data["coverage"][1].update(selected=True, status="complete", total=1, included=1, statistics_complete=True)
    data["coverage"].append(dict(source_type="actual", selected=False, status="not_selected", included=0))
    sections = [dict(
        section_id=key, availability="available", source_refs=[], metric_refs=[], observed_at=NOW,
        period_basis="planned_in_period" if key.startswith("next_") else "occurred_in_period",
    ) for key in WEEK_SECTIONS]
    sections[0].update(source_refs=[SOURCE], metric_refs=["visits"])
    sections[-1].update(source_refs=[OPP_REF], period_basis="current_at_snapshot")
    data["sales_week_facts"] = dict(
        data_as_of=NOW, next_period=dict(start=req.period.end_exclusive,
                                        end_exclusive=req.period.end_exclusive + timedelta(days=7)),
        sections=sections, opportunity_facts=[dict(
            source_ref=OPP_REF, opportunity_id=OPP_ID, opportunity_version=1, probability=50,
            stage_code="solution", status="open", qualified_as=["current_open_ge50"],
            progress_state=state, next_action_state="recorded",
        )], transition_coverage=dict(candidate_count=1, history_complete_count=0, history_unknown_count=1,
                                    proven_first_count=0, cohort_complete=False),
    )
    return GenerationInput.model_validate(data)


def weekly_content():
    return ReportContent.model_validate(dict(title="Report", sections=[dict(
        id=key, title=key, items=([dict(kind="summary", text="Model synopsis", source_refs=[ref], metric_refs=[])]
                                if (ref := {"summary": SOURCE, "ppl_open_50": OPP_REF}.get(key)) else []),
    ) for key in WEEK_SECTIONS], missing_data=[]))


@pytest.mark.parametrize("state,expected", [
    ("no_recorded_progress_in_week", "本周未记录新进展。"),
    ("recorded_in_week", "本周有已登记的进展材料"),
    ("unavailable", "本周进展材料不可得，不能据此认定没有进展。"),
])
def test_ppl_progress_is_rendered_from_frozen_enum_not_model_wording(state, expected):
    req, body = weekly_request(state), weekly_content()
    result = retain_frozen_facts(body, req)
    note = result.sections[-1].items[-1]
    assert note.source_refs == [OPP_REF] and expected in note.text
    if state != "no_recorded_progress_in_week":
        assert "本周未记录新进展" not in note.text
    assert result.sections[-1].items[0] == body.sections[-1].items[0]


def test_rewrite_retains_only_selected_sections_and_never_mutates_old_revision():
    req, previous = weekly_request(), weekly_content()
    req.sources[0].text = "【人工确认】已完成本次验证。"
    saved = previous.model_dump(mode="json")
    req = GenerationInput.model_validate({**req.model_dump(mode="python"), "rewrite": dict(
        document_id=UUID(int=80), base_revision_id=UUID(int=81), base_document_version=1,
        section_ids=["summary"], previous_content=previous,
    )})
    selected = previous.model_copy(deep=True)
    selected.sections = selected.sections[:1]
    result = retain_frozen_facts(selected, req)
    assert [section.id for section in result.sections] == ["summary"]
    assert len(result.sections[0].items) == 2
    assert previous.model_dump(mode="json") == saved


@pytest.mark.parametrize("boundary", ["items", "total"])
def test_added_notes_cannot_bypass_original_content_bounds(boundary):
    req = request()
    req.sources[0].text = "【原文限定】记录内容。"
    body = ReportContent.model_validate(content("x", refs=[]))
    item = body.sections[0].items[0]
    if boundary == "items":
        body.sections[0].items = [deepcopy(item) for _ in range(200)]
    else:
        item.text = "x" * 8000
        body.sections[0].items = [deepcopy(item) for _ in range(8)]
    ReportContent.model_validate(body.model_dump(mode="python"))
    with pytest.raises(InvalidAgentResult) as failure:
        retain_frozen_facts(body, req)
    assert contract_failure_details(failure.value) == {"contract_code": "work_report.retained_facts_invalid"}


@pytest.mark.asyncio
async def test_final_retention_uses_no_second_model_request_and_preserves_completed_claim():
    req = request()
    req.sources[0].text = "【演示】已完成验收。尚需核实其他场景。"
    body = content("已完成验收。", refs=[])
    invoke = AsyncMock(return_value=body)
    result = await generate_work_report(req, providers=GenerationProviders(direct=invoke),
                                       assert_active=AsyncMock(), routing_policy=RoutingPolicy())
    invoke.assert_awaited_once()
    assert result.status == "generated"
    assert result.content.sections[0].items[0].text == "已完成验收。"
    assert req.sources[0].text in result.content.sections[0].items[1].text


@pytest.mark.asyncio
async def test_retention_happens_after_final_combine_not_in_model_summaries():
    req = source_request(True)
    observed = []

    async def invoke(envelope):
        observed.append(deepcopy(envelope))
        return fixed_response(envelope)

    result = await generate_work_report(req, providers=GenerationProviders(direct=invoke),
                                       assert_active=AsyncMock(), routing_policy=RoutingPolicy())
    assert [call["generation"]["phase"] for call in observed] == ["summarize", "summarize", "combine"]
    assert all("来源开头标注" not in item["text"] for summary in observed[-1]["input"]["summaries"]
               for section in summary["sections"] for item in section["items"])
    notes = [item for item in result.content.sections[0].items if "来源开头标注" in item.text]
    assert len(notes) == 1 and "【完全合成验收记录】" in notes[0].text
    assert "上下文过长" in notes[0].text  # No silent clipping of the long source.


@pytest.mark.asyncio
async def test_final_size_failure_rejects_without_retry_or_basic_summary_success():
    req = request()
    req.sources[0].text = "【原文限定】记录内容。"
    body = content("Synopsis", refs=[])
    body["sections"][0]["items"] = [deepcopy(body["sections"][0]["items"][0]) for _ in range(200)]
    invoke = AsyncMock(return_value=body)
    with pytest.raises(InvalidAgentResult):
        await generate_work_report(req, providers=GenerationProviders(direct=invoke),
                                   assert_active=AsyncMock(), routing_policy=RoutingPolicy())
    invoke.assert_awaited_once()


def test_truncated_snapshot_never_claims_complete_original_limitations():
    req = request()
    req.sources[0].text = "【原文标注】已完成。"
    req.sources[0].truncated = True
    req.coverage[0].truncated = True
    result = retain_frozen_facts(ReportContent.model_validate(content("Synopsis", refs=[])), req)
    assert "本快照来源已截断" in result.sections[0].items[-1].text
