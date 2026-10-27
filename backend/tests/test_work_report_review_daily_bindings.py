"""Daily objective claims and real save-entry fences; synthetic transports only."""

import json
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from sales_backend.contracts.work_report import GenerationInput, GenerationResult, PeriodSelection
from sales_backend.contracts.work_report_review import ReviewFactsV2
from sales_backend.domain.work_report import WorkReportError, resolve_period, validate_generation_result
from sales_backend.services.agent_platform.routing import InvalidAgentResult
from sales_backend.services.work_report_generation import GenerationProviders, generate_work_report
from sales_backend.services.work_report_generation import review_daily_bindings as daily
from sales_backend.services.work_report_generation.review import (
    combined_review_content,
    validate_combined_review_wire,
    validate_review_content,
)
from tests.test_work_report_review_fact_bindings import contextual_request, envelope
from tests.test_work_report_review_fact_bindings import request as weekly_request
from tests.test_work_report_review_generation import POLICY


def request():
    data = weekly_request().model_dump(mode="json")
    data["review_mode"] = "daily"
    data["period"] = resolve_period(PeriodSelection(kind="day"),
        now=weekly_request().period.cutoff).model_dump(mode="json")
    data["review_facts"]["period"] = data["period"]
    return GenerationInput.model_validate(data)


def answer(env):
    payload = deepcopy(env["output_template"])
    choices = env["input"]["generation_constraints"]["review_daily_claims"]
    for role, kind in (("fact", "fact"), ("impact", "summary"), ("next", "suggestion")):
        rows = [row for row in choices.values() if row["role"] == role]
        section = next(s for s in payload["sections"] if s["id"] == "review_0_" + role)
        section["items"] = [{"kind": kind, "text": "\n".join(row["token"] for row in rows),
            "source_refs": list(dict.fromkeys(ref for row in rows for ref in row["source_refs"])),
            "metric_refs": []}]
    return payload


def validate(payload, req, **kwargs):
    return validate_combined_review_wire(payload, req,
        allowed_refs={row.source_ref for row in req.review_facts.evidence}, **kwargs)


def test_daily_claim_roles_are_not_the_same_sentence():
    req = request()
    choices = envelope(req)["input"]["generation_constraints"]["review_daily_claims"]
    for measure_refs in {tuple(row["measure_refs"]) for row in choices.values()}:
        rows = {row["role"]: row for row in choices.values()
                if tuple(row["measure_refs"]) == measure_refs}
        assert set(rows) == {"fact", "impact", "next"}
        assert len({rows[role]["text"] for role in rows}) == 3
        assert "业务影响" in rows["impact"]["text"] or "当前数据限制" in rows["impact"]["text"]
        assert rows["next"]["text"].startswith("下一步：")


def test_daily_incomplete_roles_explain_reason_specific_business_impact():
    req = request()
    choices = envelope(req)["input"]["generation_constraints"]["review_daily_claims"]
    impacts = [row["text"] for row in choices.values() if row["role"] == "impact"]
    next_steps = [row["text"] for row in choices.values() if row["role"] == "next"]
    assert any("当前数据限制" in text for text in impacts)
    assert any("昨日定时快照" in text for text in next_steps)
    rendered = daily.catalog(req.review_facts, for_render=True)
    rendered_impacts = [row["text"] for row in rendered.values() if row["role"] == "impact"]
    rendered_next = [row["text"] for row in rendered.values() if row["role"] == "next"]
    assert any("预测金额不完整" in text for text in rendered_impacts)
    assert any("预测金额" in text for text in rendered_next)
    assert "没有可比较的昨日基线" in daily._incomplete_impact(SimpleNamespace(missing_reasons=["no_baseline"]))
    assert "昨日定时快照" in daily._incomplete_next(SimpleNamespace(missing_reasons=["no_baseline"]))
    assert "纳入条件尚未确认" in daily._incomplete_impact(SimpleNamespace(missing_reasons=["unknown_eligibility"]))
    assert "纳入条件" in daily._incomplete_next(SimpleNamespace(missing_reasons=["unknown_eligibility"]))


def test_multiple_subject_claims_render_independently_without_changing_public_contract():
    req = request()
    payload = answer(envelope(req))
    validate(payload, req)
    content = combined_review_content(payload, req)
    text = content.review_content.items[0].fact.text
    assert "东区（仅此团队）" in text and "北区（仅此团队）" in text
    assert "已知部分为 1953000.00 元，不是全量总额" in text
    assert "{{review_claim:" not in text
    assert content._review_daily_binding and "_review_daily_binding" not in content.model_dump()
    daily.validate_generated(content.model_copy(deep=True), req)


@pytest.mark.parametrize("fault", ["wrong_source", "role", "embedded", "free_metric", "metric_refs", "unknown"])
def test_wrong_claim_scope_or_predicate_and_inline_negation_are_not_repaired(fault):
    req = request()
    payload = answer(envelope(req))
    item = payload["sections"][0]["items"][0]
    if fault == "wrong_source":
        item["source_refs"] = ["review:team1"]
    elif fault == "role":
        item["text"] = payload["sections"][1]["items"][0]["text"]
    elif fault == "embedded":
        item["text"] = "并非" + item["text"]
    elif fault == "free_metric":
        item["text"] = "东区确收与回款预测均缺失预测金额。"
        item["metric_refs"] = []
    elif fault == "metric_refs":
        item["metric_refs"] = [req.review_facts.measures[1].measure_id]
    else:
        item["text"] = "{{review_claim:" + "0" * 24 + "}}"
    original = deepcopy(payload)
    with pytest.raises(InvalidAgentResult):
        validate(payload, req)
    assert payload == original


def test_combine_cannot_borrow_other_role_or_unselected_same_source_claim():
    req = request()
    draft = answer(envelope(req))
    accepted = daily.accepted_claims([draft])
    validate(draft, req, accepted_daily_claims=accepted)
    accepted.remove(next(pair for pair in accepted if pair[0] == "impact"))
    with pytest.raises(InvalidAgentResult):
        validate(draft, req, accepted_daily_claims=accepted)


def business_request():
    req = request()
    source = req.review_facts.evidence[0].model_copy(update={
        "source_ref": "review:task", "kind": "task", "record_id": "task",
        "text": json.dumps({"name": "回款预测功能联调", "customer_name": "合成客户",
                            "summary_excerpt": "客户希望先核对回款预测展示逻辑。"}, ensure_ascii=False)})
    req.review_facts.evidence.append(source)
    return req


def business_answer(req):
    payload = deepcopy(envelope(req)["output_template"])
    for index, (kind, text) in enumerate((
        ("fact", "来源名称“回款预测功能联调”当前待办；材料原文“客户希望先核对回款预测展示逻辑。”"),
        ("summary", "若持续延后，可能影响相关商机的验证安排。"),
        ("suggestion", "建议与负责人确认当前阻碍和后续推进安排，待人工确认。"),
    )):
        payload["sections"][index]["items"] = [{"kind": kind, "text": text,
            "source_refs": ["review:task"], "metric_refs": []}]
    return payload


def test_business_analysis_and_literal_metric_names_in_cited_names_or_quotes_remain():
    req = business_request()
    payload = business_answer(req)
    validate(payload, req)
    typed = combined_review_content(payload, req).review_content
    assert "可能影响" in typed.items[0].impact_or_risk.text
    assert typed.items[0].next_step.requires_confirmation
    bad = deepcopy(payload)
    bad["sections"][1]["items"][0]["text"] = "材料原文“确收与回款预测金额缺失。”"
    with pytest.raises(InvalidAgentResult):
        validate(bad, req)
    req.review_facts.evidence[-1].state_kind = "planned"
    with pytest.raises(InvalidAgentResult):
        validate(payload, req)


@pytest.mark.asyncio
async def test_generator_deepcopy_and_actual_save_entry_reject_mutation_and_serialized_witness_loss(monkeypatch):
    from sales_backend.repositories.work_report import WorkReportRepository
    from sales_backend.services.work_report_generation import service

    req = request()
    monkeypatch.setattr(service, "BATCH_CHARACTERS", 800)
    provider = AsyncMock(side_effect=answer)
    result = await generate_work_report(req, providers=GenerationProviders(direct=provider),
        assert_active=AsyncMock(), routing_policy=POLICY)
    assert result.trace.chunks_total > 1
    assert result.trace.prompt_version == daily.PROMPT_VERSION
    validate_generation_result(result.model_copy(deep=True), req)
    # The old/manual read path deliberately accepts its structural contract.
    validate_review_content(result.content.review_content, req.review_facts, mode="daily")
    missing = GenerationResult.model_validate(result.model_dump(mode="json"))
    changed = result.model_copy(deep=True)
    changed.content.review_content.items[0].fact.text += "东区覆盖完整。"
    for bad in (missing, changed):
        repository = WorkReportRepository()
        repository.assert_run_active = AsyncMock(return_value={"status": "running",
            "generation_input": req.model_dump(mode="json")})
        connection = AsyncMock()
        with pytest.raises(WorkReportError):
            await repository.save_result(connection, None, UUID(int=99), bad)
        connection.execute.assert_not_awaited()
        connection.fetchrow.assert_not_awaited()


def test_witness_wire_mutation_and_wrong_snapshot_fail_recomputation():
    req = request()
    content = combined_review_content(answer(envelope(req)), req)
    bad = content.model_copy(deep=True)
    bad._review_daily_binding["wire"]["sections"][0]["items"][0]["source_refs"] = ["review:team1"]
    with pytest.raises((ValueError, InvalidAgentResult)):
        daily.validate_generated(bad, req)
    changed = req.model_copy(update={"snapshot_hash": "f" * 64})
    with pytest.raises(ValueError):
        daily.validate_generated(content, changed)


def test_rendered_clause_size_is_checked_not_only_short_marker_size():
    req = request()
    payload = answer(envelope(req))
    with pytest.raises(ValueError, match="bounds"):
        daily.render_wire(payload, req.review_facts,
            allowed_refs={row.source_ref for row in req.review_facts.evidence}, text_limit=50)


@pytest.mark.asyncio
async def test_actual_handler_complete_run_save_result_passes_private_witness_to_insert_gate(monkeypatch):
    from sales_backend.repositories.work_report import WorkReportRepository
    from sales_backend.services import work_report_jobs as jobs

    req = request()
    result = await generate_work_report(req, providers=GenerationProviders(direct=AsyncMock(side_effect=answer)),
        assert_active=AsyncMock(), routing_policy=POLICY)
    class ReachedInsert(Exception):
        pass

    connection = AsyncMock()
    connection.execute.side_effect = ReachedInsert

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield connection

    repository = WorkReportRepository()
    repository.get_completed_job = AsyncMock(return_value=None)
    repository.start_run = AsyncMock(return_value=SimpleNamespace(version_no=1))
    repository.generation_input = AsyncMock(return_value=req)
    repository.assert_run_active = AsyncMock(return_value={"status": "running",
        "generation_input": req.model_dump(mode="json")})
    monkeypatch.setattr(jobs, "_lease", lambda: None)
    monkeypatch.setattr(jobs, "_check", AsyncMock())
    monkeypatch.setattr(jobs, "assert_source_access", AsyncMock())
    generator = AsyncMock(return_value=result.model_copy(deep=True))
    handler = jobs.WorkReportGenerationHandler(SimpleNamespace(transaction=transaction, settings=None),
        repository=repository, access=SimpleNamespace(), generate=generator)
    actor = SimpleNamespace(workspace_id=str(UUID(int=1)), user_id=str(UUID(int=2)))
    with pytest.raises(ReachedInsert):
        await handler.handle(UUID(int=99), actor)
    assert "INSERT INTO agent.work_report_document" in connection.execute.await_args.args[0]
    generator.assert_awaited_once()


def test_automatic_visit_kr_keeps_activity_evidence_and_free_business_context():
    req = request()
    data = contextual_request().review_facts.model_dump(mode="json")
    data["period"] = req.period.model_dump(mode="json")
    group, measure = data["groups"][-1], data["measures"][-1]
    group["metric_code"] = "visit_count"
    measure.update(metric_code="visit_count", value="1", coverage="complete", missing_reasons=[],
                   unit="次", currency=None, tax_basis="not_applicable",
                   source_refs=["review:kr_query", "review:visit_projection"])
    # Exact _projection_sources shape for a counted visit, including default
    # current state. It is distinct from a rich business activity record.
    data["evidence"].append({**data["evidence"][0], "source_ref": "review:visit_projection",
        "kind": "activity", "record_id": str(UUID(int=123)), "state_kind": "current",
        "text": json.dumps({"kind": "visit", "id": str(UUID(int=123)), "version_no": 1,
                            "business_date": "2026-10-09"})})
    group["source_refs"].extend(["review:visit_projection", "review:activity"])
    for source in data["evidence"]:
        if source["source_ref"] in {"review:kr", "review:kr_query"}:
            raw = json.loads(source["text"])
            raw["metric_code"] = "visit_count"
            if source["source_ref"] == "review:kr_query":
                raw.update(value="1", coverage="complete", missing_reasons=[], known_subtotal="1")
            source["text"] = json.dumps(raw)
    req.review_facts = ReviewFactsV2.model_validate(data)
    env = envelope(req)
    choices = env["input"]["generation_constraints"]["review_daily_claims"]
    kr = [row for row in choices.values() if row["role"] == "fact" and row["measure_refs"] == [measure["measure_id"]]]
    assert len(kr) == 1 and "review:visit_projection" in kr[0]["source_refs"]
    assert any(row["source_ref"] == "review:visit_projection" for row in env["input"]["review_facts"]["evidence"])
    payload = answer(env)
    payload["sections"][0]["items"][0]["text"] += "\n材料原文“客户提出验证安排，尚待双方确认。”"
    payload["sections"][0]["items"][0]["source_refs"].append("review:activity")
    validate(payload, req)
    content = combined_review_content(payload, req)
    assert "客户提出验证安排" in content.review_content.items[0].fact.text
    assert measure["measure_id"] in content.review_content.items[0].fact.measure_refs
    # Existing weekly callers still retain activity context with the default.
    from sales_backend.services.work_report_generation import review_fact_bindings as weekly
    assert any("客户提出验证安排" in row["text"] for row in weekly.catalog(req.review_facts)[-1]["progress"])
