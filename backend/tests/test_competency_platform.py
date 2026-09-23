"""Synthetic provider tests; real platform quality needs separate live evidence."""

import json
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from sales_backend.config import get_settings
from sales_backend.domain.company_rules import policy_snapshot
from sales_backend.domain.competency_review import (
    CONTRACT_VERSION,
    CompetencyContractError,
    review_window,
    scored_review,
    validate_competency_result,
)
from sales_backend.integrations.senseaudio import SenseAudioError
from sales_backend.integrations.supreme_fde import FdeClient
from sales_backend.repositories.competency_reviews import CompetencyReviewRepository
from sales_backend.services import competency_reviews as business
from sales_backend.services.agent_platform import inference as inference_module
from sales_backend.services.agent_platform.audit import InferenceAudit
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import InferenceService, binding_for
from sales_backend.services.agent_platform.pilot import competency_review_pilot_policy, operating_report_pilot_policy
from sales_backend.services.runtime_config import apply_execution_policy
from tests.test_fde_facts_runtime import ACTOR, SNAPSHOT, Chunks, frame

AGENT = "synthetic-competency-agent"
KEY = "app-synthetic-competency-key"
REVIEW_ID = "55555555-5555-4555-8555-555555555555"
VISIT_ID = "66666666-6666-4666-8666-666666666666"
FRAMEWORK = {
    "version_no": 1, "display_name": "六维销售能力", "scoring_rules": {"evidence_required": True},
    "dimensions": [{"code": f"dimension_{i}", "name": f"维度{i}", "weight": 1 if i else 5} for i in range(6)],
}
FACTS = {
    "subject_user_id": ACTOR.user_id, "review_date": "2026-09-13", "window_days": 30,
    "window_start": "2026-08-15T00:00:00+08:00", "window_end_exclusive": "2026-09-14T00:00:00+08:00",
    "visits": [{"visit_id": VISIT_ID, "follow_up_record": "合成事实，仅用于契约测试"}],
    "visit_count": 1, "previous_review": None, "data_as_of": "2026-09-13T00:00:00Z",
}


def answer():
    return {
        "summary": "根据本人跟进事实形成六维复盘。", "strengths": ["明确下一步动作"],
        "improvements": [f"维度{i}：下次拜访前复核两项关键信息" for i in range(6)],
        "dimensions": [{
            "code": f"dimension_{i}", "score": 90 if i == 0 else 50,
            "assessment": "已有沟通证据，仍需确认具体决策人。",
            "coaching_action": "下次拜访前绘制决策链，补齐两名关键联系人的职责。",
            "evidence": [{"visit_id": VISIT_ID, "detail": "本人记录列明沟通结果和下一步计划"}],
        } for i in range(6)],
    }


def configured(*, enabled=True, blocked=False, **changes):
    return replace(get_settings(), **{
        "database_url": "", "senseaudio_api_key": "", "max_retries": 0,
        "agent_fde_pilot_path": "", "agent_fde_base_url": "https://platform.invalid/v1",
        "agent_fde_competency_review_id": AGENT, "agent_fde_competency_review_api_key": KEY,
        "agent_fde_pilot_json": json.dumps({ACTOR.workspace_id: {"capabilities": {"competency_review": {
            "enabled": enabled, "user_ids": [ACTOR.user_id], "block_platform_requests": blocked,
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }}}}),
        "agent_platform_bindings_json": json.dumps({ACTOR.workspace_id: {"competency_review": {
            "enabled": True, "agent_id": AGENT, "expected_snapshot_id": SNAPSHOT, "execution_mode": "filtered_facts",
        }}}),
        "agent_inference_platform_seconds": .03, "agent_inference_total_seconds": 3,
        **changes,
    })


@pytest.mark.parametrize("bad", [
    "missing_dimension", "duplicate_code", "foreign_code", "nan", "infinite", "string_score", "bool_score",
    "score_low", "score_high", "foreign_evidence", "duplicate_evidence", "blank_detail", "object_assessment",
    "empty_action", "missing_summary", "bad_strength", "missing_improvement", "dimensions_object",
])
def test_bad_output_is_rejected_instead_of_saved_as_a_zero_or_clipped_score(bad):
    value = answer()
    first = value["dimensions"][0]
    if bad == "missing_dimension":
        value["dimensions"].pop()
    elif bad == "duplicate_code":
        value["dimensions"][1]["code"] = first["code"]
    elif bad == "foreign_code":
        first["code"] = "invented"
    elif bad in {"nan", "infinite", "string_score", "bool_score", "score_low", "score_high"}:
        first["score"] = {
            "nan": float("nan"), "infinite": float("inf"), "string_score": "90", "bool_score": True,
            "score_low": -1, "score_high": 101,
        }[bad]
    elif bad == "foreign_evidence":
        first["evidence"][0]["visit_id"] = SNAPSHOT
    elif bad == "duplicate_evidence":
        first["evidence"].append(deepcopy(first["evidence"][0]))
    elif bad == "blank_detail":
        first["evidence"][0]["detail"] = " "
    elif bad == "object_assessment":
        first["assessment"] = {"text": "wrong type"}
    elif bad == "empty_action":
        first["coaching_action"] = ""
    elif bad == "missing_summary":
        del value["summary"]
    elif bad == "bad_strength":
        value["strengths"] = [False]
    elif bad == "missing_improvement":
        value["improvements"].pop()
    elif bad == "dimensions_object":
        value["dimensions"] = {}
    with pytest.raises(CompetencyContractError, match="competency_review"):
        validate_competency_result(value, FRAMEWORK, FACTS)


def test_framework_controls_dimension_order_weights_and_total_despite_model_fields():
    value = answer()
    value.update(overall_score=100, framework_version=999, inference_route={"provider": "forged"})
    value["dimensions"].reverse()
    value["dimensions"][-1].update(weight=0, name="模型自定名称")
    result = scored_review(value, FRAMEWORK, FACTS)
    assert result["overall_score"] == 70
    assert list(result["dimension_scores"]) == [f"dimension_{i}" for i in range(6)]
    assert result["dimension_scores"]["dimension_0"]["weight"] == 5
    assert result["dimension_scores"]["dimension_0"]["name"] == "维度0"
    assert result["improvements"][0].startswith("维度0：下次拜访前")
    assert "inference_route" not in result


def test_no_records_can_have_explicit_insufficient_evidence_without_fabricated_sources():
    value = answer()
    for item in value["dimensions"]:
        item.update(evidence=[], assessment="当前没有本人拜访证据，无法判断该维度表现。")
    assert validate_competency_result(value, FRAMEWORK, {**FACTS, "visits": []})["dimensions"][0]["evidence"] == []


@pytest.mark.parametrize("day,expected_start,expected_end", [
    (date(2026, 9, 13), "2026-08-15T00:00:00+08:00", "2026-09-14T00:00:00+08:00"),
    (date(2024, 3, 1), "2024-02-01T00:00:00+08:00", "2024-03-02T00:00:00+08:00"),
    (date(2026, 1, 1), "2025-12-03T00:00:00+08:00", "2026-01-02T00:00:00+08:00"),
])
def test_review_window_is_thirty_beijing_days_including_today(day, expected_start, expected_end):
    start, end = review_window(day)
    assert (start.isoformat(), end.isoformat()) == (expected_start, expected_end)
    assert end - start == timedelta(days=30)
    assert start.astimezone(UTC).hour == 16
    assert start <= end - timedelta(microseconds=1) < end


def test_competency_has_an_independent_binding_secret_and_company_policy(monkeypatch):
    monkeypatch.setenv("AGENT_FDE_COMPETENCY_REVIEW_ID", AGENT)
    monkeypatch.setenv("AGENT_FDE_COMPETENCY_REVIEW_API_KEY", KEY)
    get_settings.cache_clear()
    try:
        assert get_settings().agent_fde_competency_review_id == AGENT
        assert get_settings().agent_fde_competency_review_api_key == KEY
    finally:
        get_settings.cache_clear()
    settings = configured()
    assert KEY not in repr(settings)
    assert binding_for(settings.agent_platform_bindings_json, ACTOR.workspace_id, "competency_review").agent_id == AGENT
    assert competency_review_pilot_policy(settings, ACTOR) is not None
    assert operating_report_pilot_policy(settings, ACTOR) is None
    assert competency_review_pilot_policy(settings, ACTOR.model_copy(update={"workspace_id": SNAPSHOT})) is None
    assert filtered_facts_runtime(None, replace(settings, agent_fde_competency_review_api_key=""),
                                  capability="competency_review") is None
    rule = policy_snapshot("agent_execution.competency_review")
    rule["definition"]["strategy"] = "direct_only"
    assert competency_review_pilot_policy(apply_execution_policy(settings, rule), ACTOR) is None
    production = {ACTOR.workspace_id: {"capabilities": {"competency_review": {
        "enabled": True, "rollout": "production", "block_platform_requests": True,
    }}}}
    assert not competency_review_pilot_policy(
        replace(settings, agent_fde_pilot_json=json.dumps(production)), ACTOR
    ).block_platform_requests


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "normal", "off", "direct_only", "blocked", "timeout", "bad_contract", "both_failed", "missing_adapter",
])
async def test_competency_uses_one_audited_operation_and_only_persists_a_valid_result(monkeypatch, scenario):
    settings = configured(enabled=scenario != "off", blocked=scenario == "blocked")
    if scenario == "direct_only":
        settings = replace(settings, agent_execution_policy={"definition": {"strategy": "direct_only"}})
    if scenario == "missing_adapter":
        settings = replace(settings, agent_fde_competency_review_api_key="")
    payload = answer()
    if scenario in {"bad_contract", "both_failed"}:
        payload["dimensions"][0]["score"] = 101
    requests, attempts, audits = [], [], []

    def wire(request):
        requests.append(request)
        stream = Chunks([], stall=True) if scenario == "timeout" else Chunks([
            frame("message", answer=json.dumps(payload)), frame("message_end"),
        ])
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    def runtime_factory(db, config, **kwargs):
        runtime = filtered_facts_runtime(db, config, **kwargs)
        if runtime is None:
            return None
        runtime.client_factory = lambda c: FdeClient(c, transport=httpx.MockTransport(wire))

        def observer(*args, **kwargs):
            value = SimpleNamespace(start=AsyncMock(return_value="attempt"), finish=AsyncMock())
            attempts.append((kwargs, value))
            return value

        runtime.observer_factory = observer
        return runtime

    direct = SimpleNamespace(chat_json=AsyncMock(return_value=answer()), close=AsyncMock())
    if scenario == "both_failed":
        direct.chat_json.return_value = payload

    def service(db, config, *, platform):
        value = InferenceService(db, config, platform=platform, direct_factory=lambda *args, **kwargs: direct)
        value.assert_actor_current = AsyncMock()
        return value

    def audit_factory(*args, **kwargs):
        audit = InferenceAudit(*args, **kwargs)
        audits.append(audit)
        return audit

    runtime_load = AsyncMock(return_value=SimpleNamespace(
        settings=settings, prompt_overrides={"competency_review": "业务侧调优：建议优先讲方法。"},
    ))
    monkeypatch.setattr(business, "load_runtime_configuration", runtime_load)
    monkeypatch.setattr(business, "filtered_facts_runtime", runtime_factory)
    monkeypatch.setattr(business, "InferenceService", service)
    monkeypatch.setattr(inference_module, "InferenceAudit", audit_factory)
    handler = business.CompetencyReviewHandler(None, settings)
    handler._load_and_start = AsyncMock(return_value=({"review_date": date(2026, 9, 13)}, FRAMEWORK))
    handler._load_facts = AsyncMock(return_value=FACTS)
    handler._persist = AsyncMock()
    if scenario == "both_failed":
        with pytest.raises(SenseAudioError) as error:
            await handler.handle(REVIEW_ID, ACTOR)
        assert not error.value.retryable  # Do not replay both billed routes through job retries.
        handler._persist.assert_not_awaited()
    else:
        await handler.handle(REVIEW_ID, ACTOR)
        handler._persist.assert_awaited_once()
        args = handler._persist.call_args.args
        assert args[4] == validate_competency_result(answer(), FRAMEWORK, FACTS)
        trace = args[5]
        assert trace["provider"] == ("agent_platform" if scenario == "normal" else "senseaudio")
        assert trace["model_ref"] == (f"agent-platform:{AGENT}" if scenario == "normal" else settings.llm_model)
        assert trace["fallback_reason"] is None if scenario in {"normal", "off", "direct_only"} else (
            trace["fallback_reason"] is not None
        )
    runtime_load.assert_awaited_once_with(None, ACTOR, settings, capability="competency_review")
    assert len(audits) == 1 and audits[0].mode == "competency_review" and audits[0].run_id is None
    if scenario in {"bad_contract", "both_failed"}:
        failures = [event for event in audits[0].events if event["phase"] == "route_failed"]
        assert failures[0]["contract_code"] == "competency_review.score"
        assert "合成事实" not in json.dumps(failures, ensure_ascii=False)
    assert direct.chat_json.await_count == (scenario != "normal")
    assert handler.settings is settings  # Per-run configuration must not mutate a reusable handler.
    if requests:
        query = json.loads(json.loads(requests[0].content)["query"])
        assert query["mode"] == "competency_review"
        assert query["facts"]["framework"] == FRAMEWORK
        assert query["facts"]["contract_version"] == CONTRACT_VERSION
        assert "业务侧调优" in query["backend_prompt"]
        assert KEY not in json.dumps(query)
    if attempts:
        metadata = attempts[0][1].start.call_args.args[2]
        assert metadata["runtime_snapshot_verified"] is False and metadata["tool_authority_issued"] is False
        assert attempts[0][0]["operation_id"] == audits[0].operation_id
        assert attempts[0][0]["run_id"] is None


@pytest.mark.asyncio
async def test_invalid_framework_fails_before_loading_model_configuration(monkeypatch):
    load = AsyncMock()
    monkeypatch.setattr(business, "load_runtime_configuration", load)
    handler = business.CompetencyReviewHandler(None, configured())
    handler._load_and_start = AsyncMock(return_value=({}, {**FRAMEWORK, "dimensions": []}))
    handler._load_facts = AsyncMock()
    with pytest.raises(ValueError, match="competency_framework"):
        await handler.handle(REVIEW_ID, ACTOR)
    load.assert_not_awaited()
    handler._load_facts.assert_not_awaited()


class TransactionDatabase:
    def __init__(self, connection):
        self.connection = connection
        self.transactions = []

    @asynccontextmanager
    async def transaction(self, actor, *, readonly=False):
        self.transactions.append((actor, readonly))
        yield self.connection


@pytest.mark.asyncio
async def test_fact_query_receives_only_subject_and_explicit_beijing_window():
    connection = SimpleNamespace(fetch=AsyncMock(return_value=[]), fetchrow=AsyncMock(return_value=None))
    db = TransactionDatabase(connection)
    handler = business.CompetencyReviewHandler(db, configured())
    facts = await handler._load_facts(ACTOR, {"review_date": date(2026, 9, 13)})
    sql, subject, start, end = connection.fetch.call_args.args
    assert subject == ACTOR.user_id
    assert start.isoformat() == "2026-08-15T00:00:00+08:00"
    assert end.isoformat() == "2026-09-14T00:00:00+08:00"
    assert "v.recorder_user_ref_id=$1::uuid" in sql
    assert "v.interaction_at >= $2::timestamptz" in sql and "v.interaction_at < $3::timestamptz" in sql
    assert facts["window_days"] == 30 and facts["visit_count"] == 0
    assert db.transactions == [(ACTOR, True)]


@pytest.mark.asyncio
async def test_competency_facts_keep_midnight_visit_on_its_beijing_business_date():
    instant = datetime(2026, 9, 12, 16, tzinfo=UTC)
    visit = {"visit_id": VISIT_ID, "interaction_at": instant}
    db = TransactionDatabase(object())
    handler = business.CompetencyReviewHandler(db, configured())
    handler.repository.facts = AsyncMock(return_value=([visit], None))
    facts = await handler._load_facts(ACTOR, {"review_date": date(2026, 9, 13)})
    assert facts["visits"][0]["visit_date"] == "2026-09-13"
    assert facts["visits"][0]["interaction_at"].isoformat() == "2026-09-13T00:00:00+08:00"
    assert facts["visits"][0]["interaction_at"] == instant
    assert visit["interaction_at"] is instant and "visit_date" not in visit
    assert facts["business_timezone"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_review_storage_links_actual_provider_and_server_scores_without_overwriting_settings():
    db = TransactionDatabase(object())
    handler = business.CompetencyReviewHandler(db, configured())
    handler.repository.save = AsyncMock()
    trace = {"operation_id": SNAPSHOT, "provider": "agent_platform", "model_ref": f"agent-platform:{AGENT}",
             "fallback_reason": None}
    await handler._persist(ACTOR, REVIEW_ID, FRAMEWORK, FACTS, answer(), trace)
    _, subject, review_id, values, snapshot, model_ref = handler.repository.save.call_args.args
    assert subject == ACTOR and review_id == REVIEW_ID
    assert values["overall_score"] == 70
    assert snapshot["review_id"] == REVIEW_ID and snapshot["contract_version"] == CONTRACT_VERSION
    assert snapshot["framework_version"] == FRAMEWORK["version_no"]
    assert snapshot["inference_route"] == trace and "run_id" not in snapshot
    assert model_ref == f"agent-platform:{AGENT}"
    assert db.transactions == [(ACTOR, False)]


@pytest.mark.asyncio
async def test_invalid_result_never_opens_a_persistence_transaction():
    db = TransactionDatabase(object())
    handler = business.CompetencyReviewHandler(db, configured())
    value = answer()
    value["dimensions"].pop()
    with pytest.raises(CompetencyContractError):
        await handler._persist(ACTOR, REVIEW_ID, FRAMEWORK, FACTS, value, {"model_ref": "synthetic"})
    assert not db.transactions


@pytest.mark.asyncio
async def test_another_subject_cannot_start_review_even_when_row_is_returned():
    connection = SimpleNamespace(
        fetchrow=AsyncMock(return_value={"subject_user_ref_id": SNAPSHOT}), execute=AsyncMock()
    )
    with pytest.raises(PermissionError):
        await CompetencyReviewRepository().start(connection, ACTOR, REVIEW_ID)
    connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_or_missing_review_is_not_reopened():
    connection = SimpleNamespace(fetchrow=AsyncMock(return_value=None), execute=AsyncMock())
    with pytest.raises(LookupError):
        await CompetencyReviewRepository().start(connection, ACTOR, REVIEW_ID)
    connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_replaced_review_cannot_record_a_completion_effect(monkeypatch):
    from sales_backend.repositories import competency_reviews as repository_module

    effect = AsyncMock()
    monkeypatch.setattr(repository_module, "record_job_effect", effect)
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=None))
    with pytest.raises(LookupError):
        await CompetencyReviewRepository().save(
            connection, ACTOR, REVIEW_ID, scored_review(answer(), FRAMEWORK, FACTS), {}, "synthetic"
        )
    effect.assert_not_awaited()
