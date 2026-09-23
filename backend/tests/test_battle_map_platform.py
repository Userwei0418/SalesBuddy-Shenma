"""Second capability: existing business handler, real SSE adapter and bounded fallback."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from sales_backend.integrations.senseaudio import SenseAudioError
from sales_backend.integrations.supreme_fde import FdeClient
from sales_backend.services import battle_map_reviews as business
from sales_backend.services.agent_platform.fde_facts_runtime import FdeFactsRuntime, filtered_facts_runtime
from sales_backend.services.agent_platform.inference import AgentBinding, InferenceService, PlatformRequest, binding_for
from sales_backend.services.agent_platform.pilot import battle_map_pilot_policy, chatbi_pilot_policy
from sales_backend.services.agent_platform.routing import ProviderUnavailable
from tests.test_agent_platform_pilot import ACTOR, NOW, settings
from tests.test_fde_facts_runtime import Chunks, frame

MAP_AGENT = "synthetic-map-agent"
MAP_KEY = "app-synthetic-map-key"
SNAPSHOT = "33333333-3333-4333-8333-333333333333"
FACTS = {
    "customer": {"id": "44444444-4444-4444-8444-444444444444", "name": "合成客户"},
    "visits": [{"id": "55555555-5555-4555-8555-555555555555", "follow_up_record": "客户同意试用"}],
    "contacts": [], "opportunities": [], "previous_score": None, "data_as_of": "2026-09-12T00:00:00Z",
}
ANSWER = {
    "potential_score": 75, "relationship_score": 50, "summary": "客户同意试用，其他证据不足。",
    "rationale": {"potential": "同意试用", "relationship": "仅一次跟进"},
    "evidence": [{"source_type": "visit", "source_id": FACTS["visits"][0]["id"], "detail": "客户同意试用"}],
}


def map_settings(*, enabled=True, blocked=False, **overrides):
    base = settings()
    allowlist = json.loads(base.agent_fde_pilot_json)[ACTOR.workspace_id]
    allowlist.update(enabled=enabled, block_platform_requests=blocked)
    return replace(base, **{
        "agent_fde_pilot_json": json.dumps({ACTOR.workspace_id: {"capabilities": {"battle_map_review": allowlist}}}),
        "agent_platform_bindings_json": json.dumps({ACTOR.workspace_id: {"battle_map_review": {
            "enabled": True, "agent_id": MAP_AGENT, "execution_mode": "filtered_facts",
            "expected_snapshot_id": SNAPSHOT,
        }}}),
        "agent_fde_base_url": "https://platform.invalid/v1",
        "agent_fde_battle_map_id": MAP_AGENT, "agent_fde_battle_map_api_key": MAP_KEY,
        "agent_inference_platform_seconds": .03, "agent_inference_total_seconds": 3,
        **overrides,
    })


def test_capabilities_have_independent_allowlists_and_credentials():
    assert battle_map_pilot_policy(settings(), ACTOR) is None  # Existing ChatBI pilot cannot enable map.
    config = map_settings()
    assert battle_map_pilot_policy(config, ACTOR) is not None
    assert chatbi_pilot_policy(config, ACTOR, "chatbi") is None
    assert battle_map_pilot_policy(map_settings(enabled=False), ACTOR) is None
    assert battle_map_pilot_policy(map_settings(blocked=True), ACTOR).block_platform_requests
    assert MAP_KEY not in repr(config)
    runtime = filtered_facts_runtime(None, config, capability="battle_map_review")
    assert runtime.agent_id == MAP_AGENT and runtime.capability == "battle_map_review"
    assert filtered_facts_runtime(None, config) is None
    assert filtered_facts_runtime(None, replace(config, agent_fde_battle_map_api_key=""),
                                  capability="battle_map_review") is None


@pytest.mark.parametrize("change", [
    {"expires_at": "2026-09-11T00:00:00Z"}, {"user_ids": []}, {"user_ids": "*"}, {"enabled": "true"},
])
def test_map_pilot_requires_own_valid_expiry_and_actor(change):
    config = map_settings()
    raw = json.loads(config.agent_fde_pilot_json)
    raw[ACTOR.workspace_id]["capabilities"]["battle_map_review"].update(change)
    assert battle_map_pilot_policy(replace(config, agent_fde_pilot_json=json.dumps(raw)), ACTOR, now=NOW) is None


def test_map_runtime_rejects_chatbi_binding_or_mode():
    config = map_settings()
    runtime = filtered_facts_runtime(None, config, capability="battle_map_review")
    binding = binding_for(config.agent_platform_bindings_json, ACTOR.workspace_id, "battle_map_review")
    assert binding == AgentBinding(MAP_AGENT, SNAPSHOT, "filtered_facts")
    request = PlatformRequest("operation", "battle_map_review", binding, ACTOR, {"mode": "battle_map_review"})
    runtime.operation(request)  # Construction is inert.
    for invalid in [replace(request, capability="chatbi"), replace(request, input={"mode": "chatbi"}),
                    replace(request, binding=AgentBinding("question-agent", SNAPSHOT, "filtered_facts"))]:
        with pytest.raises(ProviderUnavailable):
            runtime.operation(invalid)


def test_approved_production_rollout_persists_without_test_fault_or_actor_allowlist():
    config = map_settings()
    raw = {ACTOR.workspace_id: {"capabilities": {"battle_map_review": {
        "enabled": True, "rollout": "production", "block_platform_requests": True,
    }}}}
    config = replace(config, agent_fde_pilot_json=json.dumps(raw))
    assert battle_map_pilot_policy(config, ACTOR).block_platform_requests is False
    second = ACTOR.model_copy(update={"user_id": "77777777-7777-4777-8777-777777777777"})
    assert battle_map_pilot_policy(config, second) is not None
    outside = ACTOR.model_copy(update={"workspace_id": "88888888-8888-4888-8888-888888888888"})
    assert battle_map_pilot_policy(config, outside) is None
    assert chatbi_pilot_policy(config, ACTOR, "chatbi") is None
    raw[ACTOR.workspace_id]["capabilities"]["battle_map_review"]["enabled"] = False
    assert battle_map_pilot_policy(replace(config, agent_fde_pilot_json=json.dumps(raw)), ACTOR) is None


def test_unrecognized_rollout_cannot_enable_map():
    config = map_settings()
    raw = json.loads(config.agent_fde_pilot_json)
    raw[ACTOR.workspace_id]["capabilities"]["battle_map_review"]["rollout"] = "typo"
    assert battle_map_pilot_policy(replace(config, agent_fde_pilot_json=json.dumps(raw)), ACTOR) is None


@pytest.mark.parametrize("score", [True, False, None, "NaN", "75", "101", -1, 100.01, float("nan"), float("inf"), -float("inf"), {}, []])
def test_invalid_scores_cannot_become_plausible_scores(score):
    with pytest.raises(ValueError):
        business.BattleMapReviewHandler._normalize_result({**ANSWER, "potential_score": score}, FACTS)


def test_original_normalization_and_backend_quadrant_rules_remain_authoritative():
    result = business.BattleMapReviewHandler._normalize_result({
        **ANSWER, "potential_score": 100, "relationship_score": 69.94,
    }, FACTS)
    assert (result["potential_score"], result["relationship_score"]) == (100, 69.9)
    assert business.quadrant_code(result["potential_score"], result["relationship_score"]) == "main_attack"
    assert result["evidence"] == ANSWER["evidence"] and "_inference_trace" not in result


@pytest.mark.parametrize("key", list(ANSWER))
def test_every_battle_map_business_field_is_required(key):
    value = deepcopy(ANSWER)
    del value[key]
    with pytest.raises(ValueError):
        business.BattleMapReviewHandler._normalize_result(value, FACTS)


@pytest.mark.parametrize("change", [
    {"summary": None}, {"summary": 1}, {"rationale": {}},
    {"rationale": {"potential": "依据", "relationship": None}},
    {"rationale": {**ANSWER["rationale"], "color": "green"}},
    {"evidence": None}, {"evidence": [None]},
    {"evidence": [{"source_type": "visit", "source_id": "foreign", "detail": "fake"}]},
    {"evidence": [{**ANSWER["evidence"][0], "source_type": "contact"}]},
    {"evidence": [{**ANSWER["evidence"][0], "detail": 1}]},
    {"evidence": [{**ANSWER["evidence"][0], "color": "green"}]},
    {"color": "red"}, {"quadrant_code": "customer_asset"},
    {"_inference_trace": {"provider": "forged"}}, {"company_policy_id": "unrequested"},
])
def test_malformed_or_foreign_evidence_rejects_the_whole_result(change):
    with pytest.raises(ValueError):
        business.BattleMapReviewHandler._normalize_result({**ANSWER, **change}, FACTS)


def test_empty_strings_and_no_evidence_are_explicit_not_fabricated():
    value = {**ANSWER, "potential_score": 0, "relationship_score": 100,
             "summary": "", "rationale": {"potential": "", "relationship": ""}, "evidence": []}
    assert business.BattleMapReviewHandler._normalize_result(value, FACTS) == {**value, "evaluation_mode": "agent"}


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["normal", "off", "blocked", "timeout", "invalid_score", "empty", "both_failed"])
async def test_business_handler_persists_once_after_platform_or_original(monkeypatch, scenario):
    config = map_settings(enabled=scenario != "off", blocked=scenario in {"blocked", "both_failed"})
    response = deepcopy(ANSWER)
    if scenario == "invalid_score":
        response["potential_score"] = "NaN"
    if scenario == "empty":
        response = {}
    stream = Chunks([], stall=True) if scenario == "timeout" else Chunks([
        frame("message", answer=json.dumps(response)), frame("message_end"),
    ])
    requests = []
    observer = SimpleNamespace(start=AsyncMock(return_value="attempt"), finish=AsyncMock())

    async def wire(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    def runtime_factory(db, settings, *, capability, block_requests):
        normal = filtered_facts_runtime(db, settings, capability=capability)
        return FdeFactsRuntime(
            db, normal.config, agent_id=normal.agent_id, capability=capability, block_requests=block_requests,
            client_factory=lambda c: FdeClient(c, transport=httpx.MockTransport(wire)),
            observer_factory=lambda *a, **kw: observer,
        )

    direct = SimpleNamespace(chat_json=AsyncMock(return_value=deepcopy(ANSWER)), close=AsyncMock())
    if scenario == "both_failed":
        direct.chat_json.side_effect = SenseAudioError("synthetic unavailable", retryable=True)

    def service(db, settings, *, platform):
        result = InferenceService(db, settings, platform=platform, direct_factory=lambda *a, **kw: direct)
        result.assert_actor_current = AsyncMock()
        return result

    monkeypatch.setattr(business, "load_runtime_configuration", AsyncMock(return_value=SimpleNamespace(
        settings=config, prompt_overrides={"battle_map_review": "原业务覆盖提示词"},
    )))
    monkeypatch.setattr(business, "filtered_facts_runtime", runtime_factory)
    monkeypatch.setattr(business, "InferenceService", service)
    monkeypatch.setattr(business, "SenseAudioClient", lambda *a, **kw: direct)
    handler = business.BattleMapReviewHandler(None, config)
    handler._load_facts = AsyncMock(return_value=deepcopy(FACTS))
    handler._persist = AsyncMock()
    if scenario == "both_failed":
        with pytest.raises(SenseAudioError) as failure:
            await handler.handle(FACTS["customer"]["id"], ACTOR)
        assert failure.value.retryable is False
        handler._persist.assert_not_awaited()
        direct.chat_json.assert_awaited_once()
        return
    await handler.handle(FACTS["customer"]["id"], ACTOR)
    handler._persist.assert_awaited_once()
    result = handler._persist.call_args.args[3]
    assert result["potential_score"] == 75 and result["evidence"] == ANSWER["evidence"]
    assert direct.chat_json.await_count == (scenario != "normal")
    if scenario == "off":
        trace = result["_inference_trace"]
        assert trace["provider"] == "senseaudio" and trace["fallback_reason"] is None
        assert "platform_run" not in trace and not requests
        observer.start.assert_not_awaited()
    else:
        trace = result["_inference_trace"]
        assert trace["provider"] == ("agent_platform" if scenario == "normal" else "senseaudio")
        assert trace["platform_run"]["tool_authority_issued"] is False
        observer.start.assert_awaited_once()
        assert observer.start.call_args.args[2]["agent_id"] == MAP_AGENT
        if scenario == "blocked":
            assert not requests and trace["fallback_reason"] == "ControlledPlatformBlock"
        else:
            assert len(requests) == 1  # No automatic replay of the Agent request.
            query = json.loads(json.loads(requests[0].content)["query"])
            assert query["mode"] == "battle_map_review" and query["facts"] == FACTS
            assert MAP_KEY not in json.dumps(query)
    if direct.chat_json.await_count:
        prompt = direct.chat_json.call_args.kwargs["messages"][0].content
        assert "原业务覆盖提示词" in prompt
        assert "输出JSON：{potential_score,relationship_score" in prompt
        assert "固定契约" in prompt and "source_id必须来自输入事实" in prompt
        direct.close.assert_awaited_once()
    await asyncio.sleep(0)  # Allow cancelled transport cleanup, never a live-model wait.
