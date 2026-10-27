"""Bounded evidence choices and server facts, with no live provider or database."""

import json
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from sales_backend.contracts.work_report import GenerationInput
from sales_backend.contracts.work_report_review import ReviewFactsV2
from sales_backend.domain.work_report import WorkReportError, validate_generation_result
from sales_backend.services.agent_platform.routing import InvalidAgentResult, RoutingPolicy, WriteState
from sales_backend.services.work_report_generation import GenerationProviders, generate_work_report
from sales_backend.services.work_report_generation import review_fact_bindings as bindings
from sales_backend.services.work_report_generation.review import (
    combined_review_content,
    render_review_content,
    validate_combined_review_wire,
    validate_review_content,
)
from sales_backend.services.work_report_generation.service import _envelope, _material
from tests.test_work_report_contract import request_data
from tests.test_work_report_review_numbers import multi_facts


def request():
    facts = multi_facts().model_dump(mode="json")
    facts["missing_data"] = ["missing_forecast_amount"]
    facts["measures"][0].update(coverage="partial", missing_reasons=["missing_forecast_amount"])
    for index, source in enumerate(facts["evidence"]):
        measure = facts["measures"][index]
        subject = facts["scope"]["subjects"][index]
        source["text"] = json.dumps(
            {
                "basis": "shared_review_projection",
                "metric_code": measure["metric_code"],
                "scope": {"scope_type": subject["scope_type"], "record_id": subject["id"]},
                "value": measure["value"],
                "coverage": measure["coverage"],
                "known_subtotal": "1953000.00" if index == 0 else "0.00",
                "missing_reasons": measure["missing_reasons"],
                "period": {"year": 2026, "quarter": 4},
            }
        )
    data = request_data()
    data.update(scope=facts["scope"], review_mode="weekly", review_facts=facts)
    return GenerationInput.model_validate(data)


def envelope(req, *, material=None, summaries=None, phase="compose"):
    return _envelope(
        req,
        material=_material(req) if material is None else material,
        summaries=summaries or [],
        phase=phase,
        index=0,
        total=1,
        text_limit=64000,
    )


def answer(env):
    payload = deepcopy(env["output_template"])
    choices = env["input"]["generation_constraints"]["review_suggestion_choices"]
    for section in payload["sections"]:
        if section["id"] in choices:
            section["items"] = deepcopy(choices[section["id"]][:20])
        elif not section["id"].startswith("review_") and env["input"]["sources"]:
            section["items"] = [
                {
                    "kind": "summary",
                    "text": "原普通正文保留。",
                    "source_refs": [env["input"]["sources"][0]["source_id"]],
                    "metric_refs": [],
                }
            ]
    return payload


def validate(payload, req, **kwargs):
    return validate_combined_review_wire(
        payload, req, allowed_refs={row["source_id"] for row in _material(req)}, **kwargs
    )


def test_server_east_partial_north_zero_and_real_wrong_subject_rejected():
    req = request()
    frozen = req.model_dump(mode="json")
    payload = answer(envelope(req))
    validate(payload, req)
    content = combined_review_content(payload, req).review_content
    text = render_review_content(content, req.review_facts)
    assert "北区（仅此团队）：确收预测金额为 0.00 元" in text
    assert "东区（仅此团队）：确收预测金额尚无法确定" in text
    assert "已知部分为 1953000.00 元，不是全量总额" in text
    assert content.groups[0].risks[0].source_refs == ["review:team0"]
    assert content.groups[0].progress[0].source_refs == ["review:team1"]
    bad = {
        "kind": "summary",
        "text": "东区（仅此团队）的确收预测金额缺失，相关统计覆盖不完整，"
        "缺口与差异暂无法核定，需待数据补齐后再确认，需保留不确定性。",
        "source_refs": ["review:team1"],
        "metric_refs": [],
    }
    next(s for s in payload["sections"] if s["id"] == "review_0_risk")["items"] = [bad]
    before = deepcopy(payload)
    with pytest.raises(InvalidAgentResult):
        validate(payload, req)
    assert payload == before and req.model_dump(mode="json") == frozen


@pytest.mark.parametrize("fault", ["ref", "text", "role", "metric", "duplicate", "outside_batch"])
def test_reject_changed_candidates_not_repair(fault):
    req = request()
    payload = answer(envelope(req))
    items = next(s for s in payload["sections"] if s["id"] == "review_0_next")["items"]
    if fault == "ref":
        items[0]["source_refs"] = ["review:team1"]
    elif fault == "text":
        items[0]["text"] += "东区覆盖完整。"
    elif fault == "role":
        items[0]["kind"] = "summary"
    elif fault == "metric":
        items[0]["metric_refs"] = [req.review_facts.measures[1].measure_id]
    elif fault == "duplicate":
        items.append(deepcopy(items[0]))
    before = deepcopy(payload)
    with pytest.raises(InvalidAgentResult):
        if fault == "outside_batch":
            validate_combined_review_wire(payload, req, allowed_refs={"review:team1"})
        else:
            validate(payload, req)
    assert payload == before


def test_combine_cannot_invent_choice_from_same_source_or_another_subtree():
    req = request()
    draft = answer(envelope(req))
    choices = bindings.candidate_sections(
        req.review_facts, allowed_refs={s.source_ref for s in req.review_facts.evidence}
    )
    next(s for s in draft["sections"] if s["id"] == "review_0_next")["items"] = []
    env = envelope(req, summaries=[draft], phase="combine")
    assert env["input"]["generation_constraints"]["review_suggestion_choices"]["review_0_next"] == []
    forged = answer(env)
    next(s for s in forged["sections"] if s["id"] == "review_0_next")["items"] = choices["review_0_next"]
    with pytest.raises(InvalidAgentResult):
        validate(forged, req, accepted_choices=bindings.accepted_choices([draft]))


def test_query_scope_and_coverage_cannot_be_swapped_in_catalog():
    req = request()
    query = json.loads(req.review_facts.evidence[0].text)
    query["scope"]["record_id"] = str(req.review_facts.scope.subjects[1].id)
    req.review_facts.evidence[0].text = json.dumps(query)
    with pytest.raises(ValueError, match="subject identity"):
        bindings.catalog(req.review_facts)


@pytest.mark.asyncio
async def test_real_generator_and_persistence_fence_preserve_history(monkeypatch):
    req = request()
    # Force summarize and combine through the real generator without inference.
    from sales_backend.services.work_report_generation import service

    monkeypatch.setattr(service, "BATCH_CHARACTERS", 800)
    transport = AsyncMock(side_effect=answer)
    result = await generate_work_report(
        req,
        providers=GenerationProviders(
            direct=transport, platform=transport, seal_platform=AsyncMock(return_value=WriteState.NONE)
        ),
        assert_active=AsyncMock(),
        routing_policy=RoutingPolicy(platform_enabled=True, total_seconds=5, platform_seconds=1, receipt_seconds=0.1),
    )
    assert result.status == "generated" and result.trace.chunks_completed == result.trace.chunks_total
    assert result.trace.chunks_total > 1
    validate_generation_result(result, req)
    assert len(result.content.review_content.groups[0].risks) == 1
    bad = result.model_copy(deep=True)
    bad.content.review_content.groups[0].risks[0].source_refs = ["review:team1"]
    before = bad.model_dump(mode="json")
    with pytest.raises(WorkReportError):
        validate_generation_result(bad, req)
    assert bad.model_dump(mode="json") == before
    # Read/manual validation remains the historical structural contract.
    assert validate_review_content(bad.content.review_content.model_dump(mode="json"), req.review_facts, mode="weekly")
    from sales_backend.repositories.work_report import WorkReportRepository

    repository = WorkReportRepository()
    repository.assert_run_active = AsyncMock(
        return_value={
            "status": "running",
            "generation_input": req.model_dump(mode="json"),
        }
    )
    connection = AsyncMock()
    with pytest.raises(WorkReportError):
        await repository.save_result(connection, None, UUID(int=99), bad)
    repository.assert_run_active.assert_awaited_once()
    connection.execute.assert_not_awaited()
    connection.fetchrow.assert_not_awaited()
    oversized = result.model_copy(deep=True)
    oversized.content.sections[0].items[0].text = "正文" * 33000
    with pytest.raises(WorkReportError):
        validate_generation_result(oversized, req)


def contextual_request():
    req = request()
    facts = req.review_facts.model_dump(mode="json")
    base = facts["evidence"][0]
    entries = [
        ("task", "task", "current", {"name": "安排产品验证", "customer_name": "合成客户", "status_label": "执行中"}),
        (
            "activity",
            "activity",
            "occurred",
            {
                "activity_label": "正式跟进",
                "customer_name": "合成客户",
                "summary_excerpt": "客户提出验证安排，尚待双方确认。",
                "next_action": "先完成接口联调，再安排客户验证。",
            },
        ),
        (
            "opportunity",
            "opportunity",
            "current",
            {"name": "合成商机", "customer_name": "合成客户", "forecast_id": None},
        ),
        ("plan", "task", "planned", {"expected_contribution": {"note": "拟支持团队目标"}}),
        ("declaration", "task", "declaration", {"actual_contribution": {"note": "已提交验证材料，仍待验收"}}),
    ]
    for ident, kind, state, data in entries:
        ref = "review:" + ident
        facts["evidence"].append(
            {
                **base,
                "source_ref": ref,
                "kind": kind,
                "record_id": ident,
                "state_kind": state,
                "text": json.dumps(data, ensure_ascii=False),
            }
        )
        facts["groups"][0]["source_refs"].append(ref)
    # Existing producer contract: selection namespace remains personal/team
    # selection; an authorized task link may point to a different team KR.
    kr_id = str(UUID(int=42))
    team_id = str(UUID(int=43))
    kr_ref = "review:kr"
    facts["evidence"].append(
        {
            **base,
            "source_ref": kr_ref,
            "kind": "key_result",
            "record_id": kr_id,
            "text": json.dumps(
                {
                    "title": "团队验证目标",
                    "metric_code": "forecast_recognized",
                    "scope": {"scope_type": "team", "team_id": team_id},
                }
            ),
        }
    )
    query = deepcopy(facts["evidence"][0])
    query["source_ref"] = "review:kr_query"
    query["record_id"] = "kr_" + kr_id
    q = json.loads(query["text"])
    q["scope"] = {"scope_type": "team", "record_id": team_id}
    query["text"] = json.dumps(q)
    facts["evidence"].append(query)
    measure = deepcopy(facts["measures"][0])
    measure["measure_id"] += "_kr"
    measure["role"] = "kr_progress"
    measure["source_refs"] = ["review:kr_query"]
    facts["measures"].append(measure)
    facts["groups"].append(
        {
            "group_id": "kr:" + kr_id,
            "metric_code": "forecast_recognized",
            "key_result_id": kr_id,
            "objective_id": None,
            "title": "团队验证目标",
            "source_refs": [kr_ref, "review:kr_query", "review:task"],
            "measure_refs": [measure["measure_id"]],
        }
    )
    return req.model_copy(update={"review_facts": ReviewFactsV2.model_validate(facts)})


def test_business_context_task_activity_opportunity_and_linked_team_kr():
    req = contextual_request()
    payload = answer(envelope(req))
    validate(payload, req)
    content = combined_review_content(payload, req).review_content
    bindings.validate_generated(content, req.review_facts)
    rendered = render_review_content(content, req.review_facts)
    for text in [
        "安排产品验证",
        "执行中",
        "正式跟进",
        "客户提出验证安排",
        "先完成接口联调，再安排客户验证",
        "合成商机",
        "团队验证目标",
        "已登记计划",
        "贡献声明不等于已实现业绩",
        "核实执行进展与阻碍",
    ]:
        assert text in rendered
    assert "missing_forecast_amount" not in "\n".join(t.text for g in content.groups for t in g.next_week_tasks)
    assert "东区（仅此团队）" not in "\n".join(r.text for r in content.groups[1].risks)


def test_task_review_material_preserves_feedback_waiting_and_stop_meaning():
    req = contextual_request()
    source = next(s for s in req.review_facts.evidence if s.source_ref == 'review:task')
    source.text = json.dumps(dict(name='安排产品验证', status_label='需调整',
        latest_feedback='已提交验证材料', next_step='确认验收意见',
        processing_reason='awaiting_review', processing_detail='等待下发人确认'))
    payload = answer(envelope(req))
    validate(payload, req)
    content = combined_review_content(payload, req).review_content
    bindings.validate_generated(content, req.review_facts)
    rendered = render_review_content(content, req.review_facts)
    for text in ('已提交验证材料', '确认验收意见', '等待下发人确认', '待验收', '不计入完成数量或目标达成'):
        assert text in rendered
    source.state_kind = 'occurred'
    source.text = json.dumps(dict(name='停止验证', status_label='已完成', completion_action='close',
        completion_result='客户取消计划', completion_note='旧完成说明', next_step='不应继续的旧计划'))
    payload = answer(envelope(req))
    validate(payload, req)
    content = combined_review_content(payload, req).review_content
    bindings.validate_generated(content, req.review_facts)
    rendered = render_review_content(content, req.review_facts)
    assert '停止说明“客户取消计划”' in rendered
    assert '下发人决定不再继续，并非验收通过或目标达成' in rendered
    assert '不应继续的旧计划' not in rendered
    _, choices = bindings._business_context(source)
    assert all('核实执行进展' not in choice['text'] for choice in choices)


def test_target_manual_kr_and_non_aggregate_actual_provenance():
    req = contextual_request()
    data = req.review_facts.model_dump(mode="json")
    kr = data["groups"][1]
    manual = data["measures"][-1]
    manual.update(value="12.00", coverage="complete", missing_reasons=[], source_refs=["review:confirmed"])
    base = data["evidence"][0]
    data["evidence"].append(
        {
            **base,
            "source_ref": "review:confirmed",
            "kind": "key_result",
            "record_id": kr["key_result_id"],
            "text": json.dumps({"confirmed_progress": {"value": "12.00", "status": "confirmed"}}),
        }
    )
    kr["source_refs"].append("review:confirmed")
    target = {
        **data["measures"][1],
        "measure_id": data["measures"][1]["measure_id"] + "_target",
        "role": "effective_target",
        "value": "30.00",
        "source_refs": ["review:target"],
    }
    data["measures"].append(target)
    data["evidence"].append(
        {
            **base,
            "source_ref": "review:target",
            "kind": "target",
            "target_status": "effective",
            "text": json.dumps({"value": "30.00", "status": "effective"}),
        }
    )
    data["groups"][0]["source_refs"].append("review:target")
    data["groups"][0]["measure_refs"].append(target["measure_id"])
    data["evidence"].append(
        {
            **base,
            "source_ref": "review:actual",
            "kind": "metric_query",
            "text": json.dumps({"source": {"kind": "effective_actual"}, "actual": {"amount": "0.00"}}),
        }
    )
    data["measures"][1]["source_refs"].append("review:actual")
    data["groups"][0]["source_refs"].append("review:actual")
    req = req.model_copy(update={"review_facts": ReviewFactsV2.model_validate(data)})
    content = combined_review_content(answer(envelope(req)), req).review_content
    bindings.validate_generated(content, req.review_facts)
    text = render_review_content(content, req.review_facts)
    assert "生效目标为 30.00 元" in text and "KR进度为 12.00 元" in text


def test_shared_query_does_not_replace_derived_role_gaps_or_baseline():
    req = request()
    data = req.review_facts.model_dump(mode="json")
    for role, reason in (("gap", "no_effective_target"), ("week_change", "no_baseline")):
        row = {
            **data["measures"][1],
            "measure_id": data["measures"][1]["measure_id"] + "_" + role,
            "role": role,
            "value": None,
            "coverage": "unavailable",
            "missing_reasons": [reason],
        }
        if role == "week_change":
            row["comparison_boundary"] = data["period"]["start"]
        data["measures"].append(row)
        data["groups"][0]["measure_refs"].append(row["measure_id"])
        data["missing_data"].append(reason)
    req = req.model_copy(update={"review_facts": ReviewFactsV2.model_validate(data)})
    content = combined_review_content(answer(envelope(req)), req).review_content
    text = render_review_content(content, req.review_facts)
    assert "北区（仅此团队）：确收预测差额尚无法确定" in text
    assert "北区（仅此团队）：确收预测周变化尚无法确定" in text
    assert "尚无生效目标" in text and "缺少可信历史比较基线" in text
    assert "北区（仅此团队）：确收预测金额为 0.00 元" in text


def test_kr_own_scope_tamper_rejected():
    req = contextual_request()
    query = next(row for row in req.review_facts.evidence if row.source_ref == "review:kr_query")
    data = json.loads(query.text)
    data["scope"]["record_id"] = str(req.review_facts.scope.subjects[0].id)
    query.text = json.dumps(data)
    with pytest.raises(ValueError, match="subject identity"):
        bindings.catalog(req.review_facts)


def test_server_owned_only_batch_can_be_empty_but_ordinary_material_cannot():
    req = request()
    material = [r for r in _material(req) if r["source_id"].startswith("review:")]
    payload = deepcopy(envelope(req, material=material)["output_template"])
    validate_combined_review_wire(payload, req, allowed_refs={r["source_id"] for r in material})
    with pytest.raises(InvalidAgentResult):
        validate(payload, req)


def test_packing_preserves_all_exact_fact_roles_refs_and_fails_on_bounds():
    rows = [
        {"text": f"主体{n}：" + "内容" * 400, "source_refs": [f"review:{n}"], "measure_refs": [], "kind": "current"}
        for n in range(12)
    ]
    packed = bindings._pack(rows)
    assert "\n".join(r["text"] for r in packed) == "\n".join(r["text"] for r in rows)
    assert {ref for r in packed for ref in r["source_refs"]} == {f"review:{n}" for n in range(12)}
    with pytest.raises(ValueError):
        bindings._pack([{**rows[0], "text": "x" * 2001}])
    with pytest.raises(ValueError, match="section bounds"):
        bindings._pack([{**rows[0], "text": "简短事实", "source_refs": [f"review:{n}"]} for n in range(31)])
