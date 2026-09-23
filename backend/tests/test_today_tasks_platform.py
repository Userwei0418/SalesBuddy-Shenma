"""Today task routing, scoped result validation and original-model fallback."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from sales_backend.config import get_settings
from sales_backend.integrations.senseaudio import SenseAudioError
from sales_backend.integrations.supreme_fde import FdeClient
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import AgentBinding, InferenceService, PlatformRequest, binding_for
from sales_backend.services.agent_platform.pilot import (
    battle_map_pilot_policy,
    chatbi_pilot_policy,
    opportunity_pilot_policy,
    personal_risk_pilot_policy,
    today_tasks_pilot_policy,
    visit_entry_pilot_policy,
)
from sales_backend.services.agent_platform.result_contracts import validate_run_result
from sales_backend.services.agent_platform.routing import InvalidAgentResult, ProviderUnavailable
from sales_backend.services.agent_run import handler as business
from sales_backend.services.agent_run.materialize import follow_up_task_drafts
from sales_backend.services.agent_run.models import RunInput
from tests.test_fde_facts_runtime import ACTOR, SNAPSHOT, Chunks, frame

AGENT = "synthetic-today-tasks-agent"
KEY = "app-synthetic-today-tasks-key"
SOURCE = "66666666-6666-4666-8666-666666666666"
MANAGEMENT = "77777777-7777-4777-8777-777777777777"
FACTS = {
    "data_as_of": "2026-09-12T00:00:00Z",
    "scope": {"type": "self", "user_id": ACTOR.user_id},
    "active_tasks": [{"source_type": "management_task", "source_id": MANAGEMENT,
                      "title": "核对本周计划", "priority_code": "medium", "due_at": "2099-09-15T10:00:00+08:00"}],
    "follow_up_candidates": [{"source_type": "visit_follow_up", "source_id": SOURCE,
                              "next_action": "2099-09-15 10:00前向客户提交方案", "customer_id": SNAPSHOT}],
}
ANSWER = {"title": "今日行动计划", "summary": "优先完成客户方案", "ordered_items": [
    {"source_type": "visit_follow_up", "source_id": SOURCE, "priority": "high",
     "due_at": "2099-09-15T10:00:00+08:00", "reason": "已有明确后续行动"},
    {"source_type": "management_task", "source_id": MANAGEMENT, "reason": "随后核对计划"},
]}


def configured(*, enabled=True, blocked=False, **kwargs):
    raw = {ACTOR.workspace_id: {"capabilities": {"today_tasks": {
        "enabled": enabled, "user_ids": [ACTOR.user_id], "block_platform_requests": blocked,
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }}}}
    bindings = {ACTOR.workspace_id: {"today_tasks": {
        "enabled": True, "execution_mode": "filtered_facts", "agent_id": AGENT,
        "expected_snapshot_id": SNAPSHOT,
    }}}
    return replace(get_settings(), **{
        "agent_fde_base_url": "https://platform.invalid/v1", "agent_fde_today_tasks_id": AGENT,
        "agent_fde_today_tasks_api_key": KEY, "agent_fde_pilot_path": "",
        "agent_platform_bindings_json": json.dumps(bindings), "agent_fde_pilot_json": json.dumps(raw),
        "database_url": "", "senseaudio_api_key": "", "max_retries": 0,
        "agent_inference_platform_seconds": .03, "agent_inference_total_seconds": 3,
        **kwargs,
    })


def test_today_configuration_is_independent(monkeypatch):
    monkeypatch.setenv("AGENT_FDE_TODAY_TASKS_ID", AGENT)
    monkeypatch.setenv("AGENT_FDE_TODAY_TASKS_API_KEY", KEY)
    get_settings.cache_clear()
    try:
        assert get_settings().agent_fde_today_tasks_id == AGENT
        assert get_settings().agent_fde_today_tasks_api_key == KEY
    finally:
        get_settings.cache_clear()
    config = configured()
    assert today_tasks_pilot_policy(config, ACTOR) is not None
    assert all(policy(config, ACTOR) is None for policy in (
        battle_map_pilot_policy, opportunity_pilot_policy, personal_risk_pilot_policy, visit_entry_pilot_policy,
    ))
    assert chatbi_pilot_policy(config, ACTOR, "chatbi") is None
    assert filtered_facts_runtime(None, replace(config, agent_fde_today_tasks_api_key="",
                                 agent_fde_visit_entry_api_key="other"), capability="today_tasks") is None
    assert KEY not in repr(config)


@pytest.mark.parametrize("change", [
    {"enabled": False}, {"user_ids": []}, {"user_ids": [SNAPSHOT]},
    {"expires_at": "2026-01-01T00:00:00Z"}, {"rollout": "unknown"},
])
def test_today_pilot_rejects_wrong_or_expired_scope(change):
    config = configured()
    raw = json.loads(config.agent_fde_pilot_json)
    raw[ACTOR.workspace_id]["capabilities"]["today_tasks"].update(change)
    assert today_tasks_pilot_policy(replace(config, agent_fde_pilot_json=json.dumps(raw)), ACTOR) is None


def test_today_production_ignores_fault_flag_and_binds_exact_mode():
    raw = {ACTOR.workspace_id: {"capabilities": {"today_tasks": {
        "enabled": True, "rollout": "production", "block_platform_requests": True,
    }}}}
    config = configured(agent_fde_pilot_json=json.dumps(raw))
    assert today_tasks_pilot_policy(config, ACTOR).block_platform_requests is False
    assert today_tasks_pilot_policy(config, ACTOR.model_copy(update={"workspace_id": SNAPSHOT})) is None
    runtime = filtered_facts_runtime(None, config, capability="today_tasks")
    assert runtime.config.api_key == KEY
    binding = binding_for(config.agent_platform_bindings_json, ACTOR.workspace_id, "today_tasks")
    request = PlatformRequest("operation", "today_tasks", binding, ACTOR, {"mode": "today_tasks"})
    assert runtime.operation(request)
    for invalid in (replace(request, capability="visit_entry"), replace(request, input={"mode": "operating_report"}),
                    replace(request, binding=AgentBinding("other", SNAPSHOT, "filtered_facts"))):
        with pytest.raises(ProviderUnavailable):
            runtime.operation(invalid)


def changed_answer(scenario):
    payload = deepcopy(ANSWER)
    item = payload["ordered_items"][0]
    if scenario == "foreign_source":
        item["source_id"] = SNAPSHOT
    if scenario == "wrong_source_type":
        item["source_type"] = "management_task"
    if scenario == "duplicates":
        payload["ordered_items"].append(deepcopy(item))
    if scenario == "bad_priority":
        item["priority"] = ["urgent"]
    if scenario == "bad_due_type":
        item["due_at"] = {"raw": "tomorrow"}
    if scenario == "bad_title":
        payload["title"] = {"private": "bad"}
    if scenario == "bad_reason":
        item["reason"] = ["bad"]
    if scenario == "bad_source_type":
        item["source_id"] = {"id": SOURCE}
    return payload


@pytest.mark.parametrize("scenario", [
    "foreign_source", "wrong_source_type", "duplicates", "bad_priority", "bad_due_type",
    "bad_title", "bad_reason", "bad_source_type",
])
def test_today_contract_rejects_ambiguous_or_out_of_scope_items(scenario):
    run = RunInput("run", "conversation", "整理待办", "today_tasks", None, ACTOR)
    with pytest.raises(InvalidAgentResult):
        validate_run_result(run, changed_answer(scenario), FACTS)


@pytest.mark.parametrize("existing_type", ["management_task", "visit_follow_up"])
@pytest.mark.parametrize("untrusted", [
    {},
    {"due_at": "2000-01-01T00:00:00Z", "priority": "urgent"},
    {"due_at": {"reschedule": "tomorrow"}, "priority": ["critical"], "title": "伪造标题",
     "status": "completed", "owner_user_ref_id": SNAPSHOT, "source_visit_id": SOURCE},
])
def test_existing_task_is_only_a_ranking_reference(existing_type, untrusted):
    facts = deepcopy(FACTS)
    facts["active_tasks"][0]["source_type"] = existing_type
    answer = deepcopy(ANSWER)
    reference = {"source_type": existing_type, "source_id": MANAGEMENT, "reason": "随后核对计划"}
    answer["ordered_items"][1] = {**reference, **untrusted}
    original = deepcopy(answer)
    normalized = validate_run_result(SimpleNamespace(mode="today_tasks"), answer, facts)
    assert normalized["ordered_items"][1] == reference
    assert answer == original  # Retained provider evidence is not rewritten.
    assert facts["active_tasks"][0]["due_at"] == FACTS["active_tasks"][0]["due_at"]
    drafts = follow_up_task_drafts(normalized, {SOURCE: facts["follow_up_candidates"][0]})
    assert len(drafts) == 1 and drafts[0].priority == "high"
    assert drafts[0].due_at == datetime(2099, 9, 15, 2, tzinfo=UTC)


@pytest.mark.parametrize("case", ["duplicate_existing", "foreign_existing", "bad_reason", "candidate_without_priority",
                                  "candidate_impersonates_existing", "overlapping_fact_sets"])
def test_read_only_existing_contract_does_not_relax_identity_or_new_candidates(case):
    facts, answer = deepcopy(FACTS), deepcopy(ANSWER)
    if case == "duplicate_existing":
        answer["ordered_items"].append(deepcopy(answer["ordered_items"][1]))
    elif case == "foreign_existing":
        answer["ordered_items"][1]["source_id"] = SNAPSHOT
    elif case == "bad_reason":
        answer["ordered_items"][1]["reason"] = {"reason": "not text"}
    elif case == "candidate_without_priority":
        answer["ordered_items"][0].pop("priority")
        answer["ordered_items"][0]["existing"] = True  # Model flags cannot select a less strict branch.
    elif case == "candidate_impersonates_existing":
        answer["ordered_items"][0]["source_type"] = "management_task"
    else:
        facts["active_tasks"].append(deepcopy(facts["follow_up_candidates"][0]))
        answer["ordered_items"] = []  # Omission cannot bypass conflicting membership.
    with pytest.raises(InvalidAgentResult):
        validate_run_result(SimpleNamespace(mode="today_tasks"), answer, facts)


@pytest.mark.parametrize("due", [None, "", "2099-09-15T10:00:00+08:00"])
def test_today_materialization_preserves_source_deadline_even_when_model_omits_it(due):
    run = RunInput("run", "conversation", "整理待办", "today_tasks", None, ACTOR)
    answer = deepcopy(ANSWER)
    answer["ordered_items"][0].update(due_at=due, owner_user_ref_id=SNAPSHOT, status="completed")
    normalized = validate_run_result(run, answer, FACTS)
    assert "owner_user_ref_id" not in normalized["ordered_items"][0]
    candidates = {SOURCE: FACTS["follow_up_candidates"][0]}
    drafts = follow_up_task_drafts(normalized, candidates)
    assert drafts[0].due_at == datetime(2099, 9, 15, 2, tzinfo=UTC) and drafts[0].priority == "high"
    # Omission does not change the pre-existing backend policy: every eligible
    # candidate gets its one follow-up task, even if the model returns no ranking.
    empty = validate_run_result(run, {"ordered_items": []}, FACTS)
    assert len(follow_up_task_drafts(empty, candidates)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "normal", "off", "blocked", "timeout", "foreign_source", "duplicates", "bad_priority", "both_failed",
])
async def test_today_business_handler_routes_and_persists_once(monkeypatch, scenario):
    config = configured(enabled=scenario != "off", blocked=scenario in {"blocked", "both_failed"})
    payload = changed_answer(scenario)
    repeated_fields = {"due_at": "2000-01-01T00:00:00Z", "priority": {"forged": "urgent"},
                       "title": "模型伪造旧任务", "status": "completed", "owner_user_ref_id": SNAPSHOT}
    payload["ordered_items"][1].update(repeated_fields)
    payload["ordered_items"][0].update(owner_user_ref_id=SNAPSHOT, status="completed", customer_id="forged")
    payload["inference_route"] = {"provider": "forged"}
    stream = Chunks([], stall=True) if scenario == "timeout" else Chunks([
        frame("message", answer=json.dumps(payload)), frame("message_end"),
    ])
    requests = []

    def wire(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    def runtime_factory(db, settings, **kwargs):
        runtime = filtered_facts_runtime(db, settings, **kwargs)
        runtime.client_factory = lambda c: FdeClient(c, transport=httpx.MockTransport(wire))
        runtime.observer_factory = lambda *a, **kw: SimpleNamespace(
            start=AsyncMock(return_value="attempt"), finish=AsyncMock(),
        )
        return runtime

    original_answer = deepcopy(ANSWER)
    original_answer["ordered_items"][1].update(repeated_fields)
    direct = SimpleNamespace(chat_json=AsyncMock(return_value=original_answer), close=AsyncMock())
    if scenario == "both_failed":
        direct.chat_json.side_effect = SenseAudioError("synthetic failure", retryable=True)

    def service(db, settings, *, platform):
        result = InferenceService(db, settings, platform=platform, direct_factory=lambda *a, **kw: direct)
        result.assert_actor_current = AsyncMock()
        return result

    persist = AsyncMock()
    monkeypatch.setattr(business, "load_runtime_configuration", AsyncMock(return_value=SimpleNamespace(
        settings=config, prompt_overrides={"today_tasks": "原待办规则覆盖"},
    )))
    monkeypatch.setattr(business, "filtered_facts_runtime", runtime_factory)
    monkeypatch.setattr(business, "InferenceService", service)
    monkeypatch.setattr(business, "SenseAudioClient", lambda *a, **kw: direct)
    monkeypatch.setattr(business, "AgentRunStore", lambda *a, **kw: SimpleNamespace(persist_result=persist))
    handler = business.AgentRunHandler(None, config)
    run = RunInput("55555555-5555-4555-8555-555555555555", "conversation", "整理待办", "today_tasks", None, ACTOR)
    handler._load_and_start = AsyncMock(return_value=run)
    handler.facts_loader.load = AsyncMock(return_value=deepcopy(FACTS))
    if scenario == "both_failed":
        with pytest.raises(SenseAudioError) as error:
            await handler.handle(run.run_id, ACTOR)
        assert error.value.retryable is False
        persist.assert_not_awaited()
        return
    await handler.handle(run.run_id, ACTOR)
    persist.assert_awaited_once()
    assert persist.call_args.args[1] == ANSWER
    assert direct.chat_json.await_count == (scenario != "normal")
    if scenario == "off":
        trace = persist.call_args.kwargs["inference_trace"]
        assert trace["provider"] == "senseaudio" and trace["fallback_reason"] is None
        assert "platform_run" not in trace and not requests
    else:
        trace = persist.call_args.kwargs["inference_trace"]
        assert trace["provider"] == ("agent_platform" if scenario == "normal" else "senseaudio")
        assert trace["platform_run"]["tool_authority_issued"] is False
        if scenario == "blocked":
            assert trace["fallback_reason"] == "ControlledPlatformBlock" and not requests
        else:
            query = json.loads(json.loads(requests[0].content)["query"])
            assert query["facts"] == FACTS and query["mode"] == "today_tasks"
            assert KEY not in json.dumps(query)
    if direct.chat_json.await_count:
        assert "原待办规则覆盖" in direct.chat_json.call_args.kwargs["messages"][0].content
        direct.close.assert_awaited_once()
