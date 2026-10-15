"""New deterministic projection boundaries; no models or runtime services."""

from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from sales_backend.contracts.work_report import GenerationInput, ReportContent
from sales_backend.domain.model_contract import contract_failure_details
from sales_backend.services.agent_platform.routing import InvalidAgentResult, RoutingPolicy
from sales_backend.services.work_report_generation import GenerationProviders, generate_work_report
from sales_backend.services.work_report_generation.retained_facts import retain_frozen_facts
from tests.test_work_report_metric_spacing import NOW, SOURCE, content, request
from tests.test_work_report_retained_facts import OPP_REF, weekly_content, weekly_request

PREFIX = "当前登记下一步（日期未结构化核定）："
VISIT_PREFIX = "已登记下一步（日期未结构化核定）："


def populated_request():
    req = weekly_request()
    data = req.model_dump(mode="python")
    source = next(row for row in data["sources"] if row["source_id"] == OPP_REF)
    source["contributors"] = [dict(user_id=UUID(int=102), name="销售甲"), dict(user_id=UUID(int=103), name="同事乙")]
    source["text"] = "当前状态。\n" + PREFIX + "下周三核对方案；此前已完成需求确认。"
    data["sales_week_facts"]["opportunity_facts"][0]["next_action_refs"] = [OPP_REF]
    return GenerationInput.model_validate(data)


def projected_note(req, body=None):
    body = body or weekly_content()
    before = body.model_dump(mode="json")
    result = retain_frozen_facts(body, req)
    for section, previous in zip(result.sections, body.sections, strict=True):
        assert section.items[:len(previous.items)] == previous.items
    assert body.model_dump(mode="json") == before
    return result.sections[-1].items[-1]


def test_complete_ppl_projection_preserves_exact_frozen_values_and_next_action_limit():
    req = populated_request()
    before = req.model_dump(mode="json")
    note = projected_note(req)
    assert "当前阶段：solution；当前状态：open；当前概率：50%。" in note.text
    assert "贡献者：销售甲、同事乙。" in note.text
    assert "本周未记录新进展。" in note.text and "已登记下一步，不代表已执行或已完成。" in note.text
    assert PREFIX + "下周三核对方案；此前已完成需求确认。" in note.text
    assert "不核定自由文本日期" in note.text and "正式计划登记截止时间" not in note.text
    assert note.source_refs == [OPP_REF]
    assert req.model_dump(mode="json") == before


@pytest.mark.parametrize("probability,display", [(None, "未知（未核定，不按0处理）"), (0, "0%"), (75, "75%")])
def test_probability_is_never_filled_in_from_threshold_or_converted_to_zero(probability, display):
    req = populated_request()
    req.sales_week_facts.opportunity_facts[0].probability = probability
    assert "当前概率：" + display + "。" in projected_note(req).text


def test_empty_contributors_and_missing_state_do_not_invent_people_or_status():
    req = populated_request()
    req.sources[-1].contributors = []
    fact = req.sales_week_facts.opportunity_facts[0]
    fact.stage_code = fact.status = ""
    note = projected_note(req)
    assert "贡献者：快照未提供，不能据此认定无人参与。" in note.text
    assert "当前阶段：未登记；当前状态：未登记" in note.text


@pytest.mark.parametrize("state,expected", [
    ("not_recorded", "未登记下一步；不能据此推断没有后续工作。"),
    ("unavailable", "下一步材料不可得，不能据此认定未登记。"),
])
def test_next_action_absence_and_unavailability_remain_distinct(state, expected):
    req = populated_request()
    fact = req.sales_week_facts.opportunity_facts[0]
    fact.next_action_state, fact.next_action_refs = state, []
    note = projected_note(req)
    assert expected in note.text and "未提供可核定的下一步日期" in note.text
    assert "下周三核对" not in note.text


def test_unreferenced_next_action_is_not_read_or_added_to_section():
    req = populated_request()
    req.sources[0].text = VISIT_PREFIX + "SECRET_UNCITED_CONTENT"
    req.sales_week_facts.opportunity_facts[0].next_action_refs = [SOURCE]
    note = projected_note(req)
    assert "SECRET_UNCITED_CONTENT" not in note.text
    assert note.source_refs == [OPP_REF]
    assert "未在本章引用" in note.text


def test_referenced_action_must_also_be_allowed_in_that_section():
    req = populated_request()
    req.sources[0].text = VISIT_PREFIX + "2030-01-02验证，不保证日期已确认。"
    req.sales_week_facts.opportunity_facts[0].next_action_refs = [SOURCE]
    body = weekly_content()
    body.sections[-1].items[0].source_refs.append(SOURCE)
    with pytest.raises(InvalidAgentResult):
        retain_frozen_facts(body, req)
    req.sales_week_facts.sections[-1].source_refs.append(SOURCE)
    result = retain_frozen_facts(body, req)
    note = next(item for item in result.sections[-1].items if "快照当前事实" in item.text)
    assert note.source_refs == [OPP_REF, SOURCE]
    assert req.sources[0].text in note.text and "正式计划登记截止时间" not in note.text


@pytest.mark.parametrize("text", ["明天14点验收", "当前状态。\n下一步：2026-12-01完成。"])
def test_dates_in_arbitrary_prose_are_not_extracted_or_certified(text):
    req = populated_request()
    req.sources[-1].text = text
    note = projected_note(req)
    assert "日期未核定，须结合来源核对" in note.text
    assert "正式计划登记截止时间" not in note.text


def test_next_action_marker_keeps_entire_multiline_context_and_literal_tokens():
    req = request()
    req.sources[0].text = "已完成演示。\n" + VISIT_PREFIX + "下周验证。\n但日期尚未确认，{{metric:unknown}}是原文。"
    result = retain_frozen_facts(ReportContent.model_validate(content("已完成演示。", refs=[])), req)
    note = result.sections[0].items[-1]
    assert VISIT_PREFIX + "下周验证。\n但日期尚未确认，｛｛metric:unknown｝｝是原文。" in note.text
    assert "{{metric:" not in note.text and note.metric_refs == []


def test_long_next_action_keeps_date_disclaimer_without_cutting_resolution():
    req = populated_request()
    req.sources[-1].text = PREFIX + "上下文" * 500 + "但后来取消了该计划。"
    note = projected_note(req)
    assert "下一步日期未结构化核定；来源片段过长" in note.text
    assert "上下文" not in note.text


@pytest.mark.parametrize("planned,due", [(True, True), (False, True), (True, False)])
def test_only_typed_planned_due_time_can_be_presented_as_registered_deadline(planned, due):
    req = populated_request()
    source = req.sources[-1]
    source.text = "任意日期2026-12-01。"
    source.state_kind = "planned" if planned else "current"
    source.time_basis = "due_at" if due else "observed_at"
    source.business_at = NOW
    note = projected_note(req)
    assert ("正式计划登记截止时间：" + NOW.isoformat() in note.text) == (planned and due)


def test_additions_still_reject_total_length_and_leave_original_content_unchanged():
    req, body = populated_request(), weekly_content()
    body.sections[0].items[0].text = "x" * 8000
    body.sections[0].items = [deepcopy(body.sections[0].items[0]) for _ in range(7)]
    body.sections[-1].items[0].text = "x" * 8000
    before = body.model_dump(mode="json")
    ReportContent.model_validate(before)
    with pytest.raises(InvalidAgentResult) as failure:
        retain_frozen_facts(body, req)
    assert contract_failure_details(failure.value) == {"contract_code": "work_report.retained_facts_invalid"}
    assert body.model_dump(mode="json") == before


@pytest.mark.asyncio
async def test_full_projection_is_assembled_once_after_the_only_model_call():
    req, body = populated_request(), weekly_content()
    provider = AsyncMock(return_value=body.model_dump(mode="json"))
    result = await generate_work_report(req, providers=GenerationProviders(direct=provider),
                                       assert_active=AsyncMock(), routing_policy=RoutingPolicy())
    provider.assert_awaited_once()
    assert result.status == "generated"
    assert sum("快照当前事实" in item.text for item in result.content.sections[-1].items) == 1
    assert result.content.sections[-1].items[0] == body.sections[-1].items[0]
