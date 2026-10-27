"""Real report contract with deterministic fake transports; never a live model."""

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import ValidationError

from sales_backend.contracts.work_report import GenerationInput
from sales_backend.domain.work_report import merge_rewrite, validate_generation_result
from sales_backend.services.agent_platform.routing import (
    InvalidAgentResult,
    ProviderUnavailable,
    RoutingPolicy,
    WriteState,
)
from sales_backend.services.work_report_generation import GenerationProviders, engine, generate_work_report, service
from sales_backend.services.work_report_generation.content import validate_model_content
from sales_backend.services.work_report_generation.engine import GenerationStateUnavailable
from sales_backend.services.work_report_generation.prompts import SYSTEM_PROMPT
from tests.test_work_report_contract import content_data, request_data

POLICY = RoutingPolicy(platform_enabled=True, total_seconds=3, platform_seconds=0.5, receipt_seconds=0.1)


def answer(envelope):
    data = envelope["input"]
    items = []
    if data["sources"]:
        items.append(dict(kind="summary", text="本批业务材料摘要。",
                          source_refs=[s["source_id"] for s in data["sources"]], metric_refs=[]))
    if data["supplements"]:
        items.append(dict(kind="user_supplement", text="用户补充材料。",
                          source_refs=[s["source_id"] for s in data["supplements"]], metric_refs=[]))
    if not items:
        metric = next(m for m in data["metrics"] if m["complete"] and m["value"] is not None)
        statement = data["generation_constraints"]["metric_statements"][metric["metric_id"]]
        items.append(dict(kind="fact", text=statement,
                          source_refs=[], metric_refs=[metric["metric_id"]]))
    return dict(title=data["rewrite"]["previous_content"]["title"] if data.get("rewrite") else "本周工作报告",
                sections=[dict(**section, items=items if index == 0 else [])
                          for index, section in enumerate(data["expected_sections"])],
                missing_data=data["required_missing_data"])


async def generate(data=None, platform=None, direct=None, active=None, **kwargs):
    request = GenerationInput(**(data or request_data()))
    platform = platform or AsyncMock(side_effect=answer)
    direct = direct or AsyncMock(side_effect=answer)
    seal = AsyncMock(return_value=WriteState.NONE)
    result = await generate_work_report(request, providers=GenerationProviders(direct, platform, seal),
                                        assert_active=active or AsyncMock(), routing_policy=POLICY, **kwargs)
    validate_generation_result(result, request)
    return result, platform, direct, seal


@pytest.mark.asyncio
async def test_platform_result_uses_backend_identity_and_one_same_envelope():
    result, platform, direct, seal = await generate()
    assert result.status == "generated" and result.provider == "agent_platform"
    assert result.trace.chunks_total == result.trace.chunks_completed == 1
    direct.assert_not_awaited()
    seal.assert_awaited_once()
    envelope = platform.await_args.args[0]
    assert envelope["system_prompt"] == SYSTEM_PROMPT
    assert envelope["input"]["snapshot_hash"] == result.snapshot_hash
    assert envelope["input"]["scheme"]["version_no"] == result.scheme_version
    assert "manual" not in envelope["output_contract"]["$defs"]["ReportItem"]["properties"]["kind"]["enum"]
    assert result.trace.input_tokens is None and result.trace.output_tokens is None
    constraints = envelope["input"]["generation_constraints"]
    assert constraints["metric_tokens"] == {"visits": "{{metric:visits}}"}
    assert constraints["metric_fragments"] == {"visits": "{{metric:visits}} 次"}
    assert set(constraints["allowed_source_refs"]) == {
        row["source_id"] for row in [*envelope["input"]["sources"], *envelope["input"]["supplements"]]}


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["reference", "manual", "user_as_summary", "section", "missing", "invented_gap", "metric", "empty"])
async def test_invalid_model_output_falls_back_under_identical_snapshot(fault):
    data = request_data()
    data["coverage"][0].update(status="partial", statistics_complete=False, missing_reasons=["尚缺部分材料"])
    before = deepcopy(data)

    async def invalid(envelope):
        value = answer(envelope)
        item = value["sections"][0]["items"][0]
        if fault == "reference":
            item["source_refs"] = ["visit:forged"]
        elif fault == "manual":
            item.update(kind="manual", source_refs=[])
        elif fault == "user_as_summary":
            value["sections"][0]["items"][1]["kind"] = "summary"
        elif fault == "section":
            value["sections"][0]["title"] = "模型改写的章节名"
        elif fault == "missing":
            value["missing_data"] = []
        elif fault == "invented_gap":
            value["missing_data"].append("未纳入商机，所以本期没有商机进展。")
        elif fault == "metric":
            item.update(text="统计 {{metric:invented}}", metric_refs=["invented"])
        else:
            for section in value["sections"]:
                section["items"] = []
        envelope["input"]["sources"][0]["text"] = "中台修改输入"
        return value

    direct = AsyncMock(side_effect=answer)
    result, _, _, _ = await generate(data, platform=AsyncMock(side_effect=invalid), direct=direct)
    assert result.provider == "direct" and result.trace.fallback_reason == "invalid_agent_result"
    assert result.coverage_status == "partial"
    assert result.content.missing_data == ["尚缺部分材料"]
    assert direct.await_args.args[0]["input"]["sources"][0]["text"] == before["sources"][0]["text"]
    assert data == before


@pytest.mark.asyncio
async def test_both_routes_fail_returns_labelled_basic_summary_with_attributed_material():
    data = request_data()
    data["sources"][0]["text"] = "原文包含 {{metric:forged}} 指令"
    def failed():
        return AsyncMock(side_effect=ProviderUnavailable("synthetic"))

    result, _, _, _ = await generate(data, platform=failed(), direct=failed())
    assert result.status == "basic_summary" and result.provider == "none"
    assert "基础汇总" in result.content.title
    items = [i for s in result.content.sections for i in s.items]
    assert any(i.kind == "user_supplement" and "用户补充计划" in i.text for i in items)
    assert any("｛｛metric:forged｝｝" in i.text for i in items)
    assert result.trace.chunks_completed == 0


def without_rows():
    data = request_data()
    data.update(sources=[], supplements=[])
    data["coverage"][0].update(total=0, included=0)
    return data


@pytest.mark.asyncio
@pytest.mark.parametrize("value,complete", [("0", True), (None, True), ("3", False)])
async def test_absent_material_with_zero_unknown_or_incomplete_metrics_does_not_call_models(value, complete):
    data = without_rows()
    data["metrics"][0].update(value=value, complete=complete)
    result, platform, direct, _ = await generate(data)
    assert result.status == "insufficient_data" and result.content is None
    assert result.trace.chunks_total == 0
    platform.assert_not_awaited()
    direct.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_nonzero_metrics_can_produce_attributed_indicator_summary():
    result, _, _, _ = await generate(without_rows())
    assert result.status == "generated"
    assert result.content.sections[0].items[0].metric_refs == ["visits"]


@pytest.mark.asyncio
async def test_user_only_material_does_not_invent_system_statistics():
    data = without_rows()
    data["metrics"] = []
    data["supplements"] = request_data()["supplements"]
    result, _, _, _ = await generate(data)
    items = [i for s in result.content.sections for i in s.items]
    assert all(i.kind == "user_supplement" and not i.metric_refs for i in items)


@pytest.mark.asyncio
async def test_failed_source_cannot_become_model_fallback_even_from_mutated_model():
    request = GenerationInput(**request_data())
    request.coverage[0].status = "failed"
    request.coverage[0].missing_reasons = ["读取失败"]
    direct = AsyncMock()
    with pytest.raises(ValidationError):
        await generate_work_report(request, providers=GenerationProviders(direct), assert_active=AsyncMock(),
                                   routing_policy=POLICY)
    direct.assert_not_awaited()


@pytest.mark.asyncio
async def test_materials_beyond_500_are_all_batched_and_merged_with_original_references():
    data = request_data()
    original = data["sources"][0]
    data["sources"] = [original | {"source_id": "visit:" + str(UUID(int=i + 100)), "record_id": str(UUID(int=i + 100))}
                       for i in range(501)]
    data["coverage"][0].update(total=501, included=501)
    result, platform, _, _ = await generate(data)
    calls = [call.args[0] for call in platform.await_args_list]
    batches = [e for e in calls if e["generation"]["phase"] == "summarize"]
    assert len(batches) > 1
    assert [s["source_id"] for e in batches for s in e["input"]["sources"]] == [s["source_id"] for s in data["sources"]]
    actual_refs = {ref for s in result.content.sections for i in s.items for ref in i.source_refs}
    assert actual_refs == {s["source_id"] for s in [*data["sources"], *data["supplements"]]}
    assert result.trace.chunks_total == result.trace.chunks_completed == len(calls)
    assert all("text" not in s for e in calls if e["generation"]["phase"] == "combine"
               for s in e["input"]["sources"])


@pytest.mark.asyncio
async def test_local_rewrite_sends_only_selected_old_sections_and_returns_only_them():
    data = request_data()
    old = content_data()
    old["sections"][1]["items"] = [
        dict(kind="manual", text="PRIVATE_UNSELECTED_MANUAL", source_refs=[], metric_refs=[])]
    data["rewrite"] = dict(document_id=str(UUID(int=10)), base_revision_id=str(UUID(int=11)), base_document_version=2,
                           section_ids=["overview"], previous_content=old)
    result, platform, _, _ = await generate(data)
    assert [s.id for s in result.content.sections] == ["overview"]
    assert result.content.title == old["title"]
    assert "PRIVATE_UNSELECTED_MANUAL" not in json.dumps(platform.await_args.args[0])
    merged = merge_rewrite(result, GenerationInput(**data))
    assert merged.sections[1].model_dump(mode="json") == old["sections"][1]


@pytest.mark.asyncio
async def test_partial_batch_fallback_is_audited_even_if_final_provider_is_platform(monkeypatch):
    monkeypatch.setattr(service, "BATCH_CHARACTERS", 1)
    events = []

    async def platform(envelope):
        if envelope["generation"]["batch_index"] == 0:
            raise InvalidAgentResult("first batch content invalid")
        return answer(envelope)

    result, _, direct, _ = await generate(platform=AsyncMock(side_effect=platform),
                                        observer=lambda *args, **kw: events.append((args, kw)))
    assert result.provider == "agent_platform" and result.trace.fallback_reason == "batch_fallback"
    direct.assert_awaited_once()
    accepted = [event for event in events if event[0][0] == "report_chunk_accepted"]
    assert [event[0][1] for event in accepted] == ["direct", "agent_platform", "agent_platform"]


@pytest.mark.asyncio
async def test_cancel_during_generation_never_returns_basic_summary():
    entered = asyncio.Event()

    async def platform(envelope):
        entered.set()
        await asyncio.Future()

    direct = AsyncMock()
    pending = asyncio.create_task(generate(platform=AsyncMock(side_effect=platform), direct=direct))
    await entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    direct.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonempty_batch_cannot_be_replaced_with_snapshot_metrics_only():
    async def ignores_material(envelope):
        value = answer(envelope)
        value["sections"][0]["items"] = [dict(
            kind="fact", text="可信统计 {{metric:visits}}。", source_refs=[], metric_refs=["visits"])]
        return value

    result, _, direct, _ = await generate(platform=AsyncMock(side_effect=ignores_material))
    assert result.provider == "direct" and result.trace.fallback_reason == "invalid_agent_result"
    direct.assert_awaited_once()


@pytest.mark.asyncio
async def test_planned_metric_cannot_become_accomplished_fact():
    data = without_rows()
    data["metrics"][0]["period_basis"] = "planned_in_period"
    result, _, _, _ = await generate(data)
    assert result.status == "basic_summary"
    assert result.content.sections[0].items[0].kind == "plan"
    assert "本期计划" in result.content.sections[0].items[0].text


@pytest.mark.asyncio
async def test_current_at_snapshot_is_preserved_and_labelled_in_basic_summary():
    data = request_data()
    data["metrics"][0]["period_basis"] = "current_at_snapshot"
    failing = AsyncMock(side_effect=ProviderUnavailable("synthetic"))
    result, platform, _, _ = await generate(data, platform=failing, direct=failing)
    assert platform.await_args.args[0]["input"]["metrics"][0]["period_basis"] == "current_at_snapshot"
    assert "取数时当前状态" in result.content.sections[0].items[0].text


@pytest.mark.asyncio
async def test_clear_missing_metric_unit_is_supplied_and_display_unit_is_not_duplicated():
    data = without_rows()

    async def no_unit(envelope):
        value = answer(envelope)
        value["sections"][0]["items"][0]["text"] = "本期已发生 · 拜访次数：{{metric:visits}}。"
        return value

    result, _, direct, _ = await generate(data, platform=AsyncMock(side_effect=no_unit))
    assert result.provider == "agent_platform" and result.status == "generated"
    assert result.content.sections[0].items[0].text == "本期已发生 · 拜访次数：{{metric:visits}} 次。"
    direct.assert_not_awaited()
    data["metrics"][0]["display_value"] = "1次"
    result, platform, _, _ = await generate(data)
    constraints = platform.await_args.args[0]["input"]["generation_constraints"]
    assert constraints["metric_fragments"] == {"visits": "{{metric:visits}}"}
    assert result.status == "generated"


@pytest.mark.parametrize("display_value,text,expected", [
    ("1", "本期拜访 {{metric:visits}} 次；另一处统计 {{metric:visits}}。", "本期拜访 {{metric:visits}} 次；另一处统计 {{metric:visits}} 次。"),
    ("1", "本期拜访 {{metric:visits}}；另一处统计 {{metric:visits}} 次。", "本期拜访 {{metric:visits}} 次；另一处统计 {{metric:visits}} 次。"),
    ("1", "本期拜访 {{metric:visits}} 次；另一处统计 {{metric:visits}} 次。", "本期拜访 {{metric:visits}} 次；另一处统计 {{metric:visits}} 次。"),
    ("1次", "本期拜访 {{metric:visits}}；另一处统计 {{metric:visits}}。", "本期拜访 {{metric:visits}}；另一处统计 {{metric:visits}}。"),
])
def test_each_repeated_metric_occurrence_retains_its_unit(display_value, text, expected):
    data = request_data()
    data["metrics"][0]["display_value"] = display_value
    request = GenerationInput(**data)
    payload = content_data()
    payload["sections"][0]["items"][0]["text"] = text
    before = deepcopy(payload)
    refs = {source.source_id for source in request.sources}
    validated = validate_model_content(payload, request, allowed_refs=refs)
    assert validated["sections"][0]["items"][0]["text"] == expected
    assert payload == before


@pytest.mark.asyncio
async def test_wall_clock_budget_is_shared_across_all_batches(monkeypatch):
    monkeypatch.setattr(service, "BATCH_CHARACTERS", 1)
    clock = [0.0]
    monkeypatch.setattr(service, "monotonic", lambda: clock[0])
    monkeypatch.setattr(engine, "monotonic", lambda: clock[0])

    async def slow(envelope):
        clock[0] += 2.0
        return answer(envelope)

    platform, direct = AsyncMock(side_effect=slow), AsyncMock(side_effect=answer)
    with pytest.raises(GenerationStateUnavailable):
        await generate(platform=platform, direct=direct)
    assert platform.await_count == 2
    direct.assert_not_awaited()


@pytest.mark.asyncio
async def test_budget_expiring_after_final_permission_check_discards_candidate(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(service, "monotonic", lambda: clock[0])
    monkeypatch.setattr(engine, "monotonic", lambda: clock[0])

    def accepted(event, provider, **details):
        if event == "report_chunk_accepted":
            clock[0] = 4.0

    with pytest.raises(GenerationStateUnavailable):
        await generate(observer=accepted)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_check", range(1, 8))
async def test_every_authority_check_obeys_remaining_total_budget(blocked_check):
    checks = 0
    cancelled = asyncio.Event()

    async def active():
        nonlocal checks
        checks += 1
        if checks == blocked_check:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    direct = AsyncMock(side_effect=answer)
    with pytest.raises(GenerationStateUnavailable):
        await asyncio.wait_for(generate_work_report(
            GenerationInput(**request_data()), providers=GenerationProviders(direct), assert_active=active,
            # Leave scheduler headroom so the chosen check is reached before
            # testing cancellation; 25ms also measured unrelated host load.
            routing_policy=RoutingPolicy(total_seconds=0.15, platform_seconds=0.005, receipt_seconds=0.001),
        ), timeout=2.0)
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
    assert checks == blocked_check
    assert direct.await_count == (0 if blocked_check <= 3 else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_check", [3, 4])
async def test_platform_deadline_during_authority_check_never_calls_direct(blocked_check):
    checks = 0

    async def active():
        nonlocal checks
        checks += 1
        if checks == blocked_check:
            await asyncio.Event().wait()

    platform, direct = AsyncMock(side_effect=answer), AsyncMock(side_effect=answer)
    seal = AsyncMock(return_value=WriteState.NONE)
    with pytest.raises(GenerationStateUnavailable):
        await asyncio.wait_for(generate_work_report(
            GenerationInput(**request_data()), providers=GenerationProviders(direct, platform, seal),
            assert_active=active,
            routing_policy=RoutingPolicy(platform_enabled=True, total_seconds=0.5,
                                         platform_seconds=0.01, receipt_seconds=0.01),
        ), timeout=0.2)
    direct.assert_not_awaited()
    assert platform.await_count == (0 if blocked_check == 3 else 1)
    assert seal.await_count == (0 if blocked_check == 3 else 1)


@pytest.mark.asyncio
async def test_slow_route_entry_guard_never_dispatches_after_absolute_deadline():
    checks = 0

    async def active():
        nonlocal checks
        checks += 1
        if checks == 2:
            await asyncio.sleep(0.08)

    direct = AsyncMock(side_effect=answer)
    with pytest.raises(GenerationStateUnavailable):
        await asyncio.wait_for(generate_work_report(
            GenerationInput(**request_data()), providers=GenerationProviders(direct), assert_active=active,
            routing_policy=RoutingPolicy(total_seconds=0.04, platform_seconds=0.005, receipt_seconds=0.001),
        ), timeout=0.2)
    direct.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_budget_is_recalculated_after_entry_authority_check(monkeypatch):
    clock, checks, budgets = [0.0], 0, []
    monkeypatch.setattr(service, "monotonic", lambda: clock[0])
    monkeypatch.setattr(engine, "monotonic", lambda: clock[0])
    original = engine.AgentRouter.evaluate

    async def evaluate(self, **kwargs):
        budgets.append(kwargs["policy"].total_seconds)
        return await original(self, **kwargs)

    async def active():
        nonlocal checks
        checks += 1
        if checks == 2:
            clock[0] += 1

    monkeypatch.setattr(engine.AgentRouter, "evaluate", evaluate)
    result, _, _, _ = await generate(active=active)
    assert result.status == "generated"
    assert budgets == [2.0]  # Three-second total less the entry check, not a fresh three seconds.


@pytest.mark.asyncio
async def test_late_authority_success_after_timeout_cannot_dispatch_model():
    release, cancelled, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def active():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        finally:
            completed.set()

    direct = AsyncMock(side_effect=answer)
    try:
        with pytest.raises(GenerationStateUnavailable):
            await asyncio.wait_for(generate_work_report(
                GenerationInput(**request_data()), providers=GenerationProviders(direct), assert_active=active,
                routing_policy=RoutingPolicy(total_seconds=0.025, platform_seconds=0.005, receipt_seconds=0.001),
            ), timeout=0.2)
        await asyncio.wait_for(cancelled.wait(), timeout=0.1)
        direct.assert_not_awaited()
    finally:
        release.set()
        await asyncio.wait_for(completed.wait(), timeout=0.1)
    direct.assert_not_awaited()


def test_platform_template_and_direct_prompt_are_the_same_single_source():
    root = Path(__file__).parents[1] / "agent_platform/work_report"
    assert (root / "system_prompt.txt").read_text() == SYSTEM_PROMPT
    assert json.loads((root / "prompt.json").read_text())["prompt"]["system_prompt"] == SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_one_bounded_repair_uses_original_sources_and_strict_schema():
    async def missing(envelope):
        value = answer(envelope)
        value['sections'][0]['items'][0]['source_refs'] = []
        return value
    # Failure payload is never included in the repair input. The model must
    # independently generate a fresh candidate which passes the same validator.
    calls = []
    async def second(envelope):
        calls.append(deepcopy(envelope))
        return await missing(envelope) if len(calls) == 1 else answer(envelope)
    result, platform, direct, _ = await generate(platform=AsyncMock(side_effect=missing), direct=AsyncMock(side_effect=second))
    assert result.status == 'generated' and result.provider == 'direct'
    assert result.trace.repair_attempts == 1 and platform.await_count == 1 and direct.await_count == 2
    assert calls[0]['input'] == calls[1]['input']
    assert calls[1]['generation']['repair_attempt'] == 1
    props = calls[1]['output_contract']['$defs']['ReportItem']
    assert props['required'] == ['kind', 'text', 'source_refs', 'metric_refs']
    assert props['properties']['source_refs']['items']['enum'] == calls[1]['input']['generation_constraints']['allowed_source_refs']
    assert result.trace.generation_issue is None


@pytest.mark.asyncio
async def test_failed_repair_stays_basic_summary_and_never_forges_references():
    async def forged(envelope):
        value = answer(envelope)
        value['sections'][0]['items'][0]['source_refs'] = ['visit:invented']
        return value
    result, platform, direct, _ = await generate(platform=AsyncMock(side_effect=forged), direct=AsyncMock(side_effect=forged))
    assert platform.await_count == 1 and direct.await_count == 2
    assert result.status == 'basic_summary' and result.provider == 'none'
    assert result.trace.repair_attempts == 1 and result.trace.chunks_completed == 0
    assert result.trace.generation_issue.code == 'AI_OUTPUT_INVALID'
    assert 'invented' not in result.content.model_dump_json()


@pytest.mark.asyncio
async def test_unavailable_providers_do_not_trigger_repair():
    result, _, direct, _ = await generate(platform=AsyncMock(side_effect=ProviderUnavailable('private detail')),
                                        direct=AsyncMock(side_effect=ProviderUnavailable('private detail')))
    assert direct.await_count == 1 and result.trace.repair_attempts == 0
    assert result.trace.generation_issue.code == 'AI_PROVIDER_UNAVAILABLE'
    assert 'private detail' not in result.model_dump_json()
