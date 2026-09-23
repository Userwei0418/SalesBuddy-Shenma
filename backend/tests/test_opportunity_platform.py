"""Business candidate handler with real FDE parsing; synthetic HTTP only."""

import asyncio
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
)
from sales_backend.services.agent_platform.routing import ProviderUnavailable
from sales_backend.services.agent_run import handler as business
from sales_backend.services.agent_run.models import RunInput
from tests.test_fde_facts_runtime import ACTOR, SNAPSHOT, Chunks, frame

AGENT = "synthetic-opportunity-agent"
KEY = "app-synthetic-opportunity-key"
OPPORTUNITY = "66666666-6666-4666-8666-666666666666"
FACTS = {
    "customer": {"id": "77777777-7777-4777-8777-777777777777", "name": "合成客户"},
    "current_opportunities": [{"id": OPPORTUNITY, "name": "已有项目", "amount": 90000, "probability": 30}],
    "data_as_of": "2026-09-12T00:00:00Z",
}
ANSWER = {"action": "create", "name": "合成独立项目", "amount": 80000, "probability": 50,
          "expected_close_date": "2026-12-20", "summary": "待用户确认", "rationale": "本次原文明示新增独立项目"}


def configured(*, enabled=True, blocked=False, **kwargs):
    raw = {ACTOR.workspace_id: {"capabilities": {"opportunity_draft": {
        "enabled": enabled, "user_ids": [ACTOR.user_id], "block_platform_requests": blocked,
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }}}}
    bindings = {ACTOR.workspace_id: {"opportunity_draft": {
        "enabled": True, "execution_mode": "filtered_facts", "agent_id": AGENT,
        "expected_snapshot_id": SNAPSHOT,
    }}}
    return replace(get_settings(), **{
        "agent_fde_base_url": "https://platform.invalid/v1", "agent_fde_opportunity_id": AGENT,
        "agent_fde_opportunity_api_key": KEY, "agent_fde_pilot_path": "",
        "agent_platform_bindings_json": json.dumps(bindings), "agent_fde_pilot_json": json.dumps(raw),
        "database_url": "", "senseaudio_api_key": "", "max_retries": 0,
        "agent_inference_platform_seconds": .03, "agent_inference_total_seconds": 3,
        **kwargs,
    })


def test_opportunity_requires_its_own_configuration_and_keeps_other_agents_independent():
    config = configured()
    assert opportunity_pilot_policy(config, ACTOR) is not None
    assert battle_map_pilot_policy(config, ACTOR) is None
    assert chatbi_pilot_policy(config, ACTOR, "opportunity_draft") is None
    assert chatbi_pilot_policy(config, ACTOR, "chatbi") is None
    only_map = {ACTOR.workspace_id: {"capabilities": {"battle_map_review": {"enabled": True, "rollout": "production"}}}}
    changed = replace(config, agent_fde_pilot_json=json.dumps(only_map))
    assert battle_map_pilot_policy(changed, ACTOR) is not None
    assert opportunity_pilot_policy(changed, ACTOR) is None
    assert filtered_facts_runtime(None, replace(config, agent_fde_opportunity_api_key="",
                                agent_fde_chatbi_api_key="other", agent_fde_battle_map_api_key="other"),
                                  capability="opportunity_draft") is None


@pytest.mark.parametrize("change", [
    {"enabled": False}, {"user_ids": []}, {"user_ids": ["88888888-8888-4888-8888-888888888888"]},
    {"expires_at": "2026-01-01T00:00:00Z"}, {"rollout": "unknown"},
])
def test_opportunity_pilot_requires_explicit_valid_authority(change):
    config = configured()
    raw = json.loads(config.agent_fde_pilot_json)
    raw[ACTOR.workspace_id]["capabilities"]["opportunity_draft"].update(change)
    assert opportunity_pilot_policy(replace(config, agent_fde_pilot_json=json.dumps(raw)), ACTOR) is None


def test_opportunity_production_is_explicit_and_cannot_carry_test_block():
    raw = {ACTOR.workspace_id: {"capabilities": {"opportunity_draft": {
        "enabled": True, "rollout": "production", "block_platform_requests": True,
    }}}}
    config = configured(agent_fde_pilot_json=json.dumps(raw))
    assert opportunity_pilot_policy(config, ACTOR).block_platform_requests is False
    other_workspace = ACTOR.model_copy(update={"workspace_id": "88888888-8888-4888-8888-888888888888"})
    assert opportunity_pilot_policy(config, other_workspace) is None


def test_opportunity_adapter_is_bound_to_exact_agent_capability_and_input_mode():
    config = configured()
    runtime = filtered_facts_runtime(None, config, capability="opportunity_draft")
    assert runtime.config.api_key == KEY and runtime.agent_id == AGENT
    binding = binding_for(config.agent_platform_bindings_json, ACTOR.workspace_id, "opportunity_draft")
    request = PlatformRequest("operation", "opportunity_draft", binding, ACTOR, {"mode": "opportunity_draft"})
    assert runtime.operation(request)
    for invalid in (replace(request, capability="chatbi"), replace(request, input={"mode": "visit_entry"}),
                    replace(request, binding=AgentBinding("other-agent", SNAPSHOT, "filtered_facts"))):
        with pytest.raises(ProviderUnavailable):
            runtime.operation(invalid)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "create", "update", "none", "off", "blocked", "timeout", "foreign_id", "invalid_enum", "both_failed",
])
async def test_actual_candidate_handler_selects_route_validates_and_persists_once(monkeypatch, scenario):
    config = configured(enabled=scenario != "off", blocked=scenario in {"blocked", "both_failed"})
    payload = deepcopy(ANSWER)
    if scenario == "update":
        payload = {"action": "update", "opportunity_id": OPPORTUNITY, "amount": 1200}
    if scenario == "none":
        payload = {"action": "none", "name": "应清空", "amount": 80000, "missing_fields": ["应清空"]}
    if scenario == "foreign_id":
        payload = {"action": "update", "opportunity_id": "foreign"}
    if scenario == "invalid_enum":
        payload["probability"] = 80
    payload["inference_route"] = {"provider": "forged"}
    stream = Chunks([], stall=True) if scenario == "timeout" else Chunks([
        frame("message", answer=json.dumps(payload)), frame("message_end"),
    ])
    requests = []

    def wire(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    observer = SimpleNamespace(start=AsyncMock(return_value="attempt"), finish=AsyncMock())

    def runtime_factory(db, settings, *, capability, block_requests):
        runtime = filtered_facts_runtime(db, settings, capability=capability, block_requests=block_requests)
        runtime.client_factory = lambda c: FdeClient(c, transport=httpx.MockTransport(wire))
        runtime.observer_factory = lambda *a, **kw: observer
        return runtime

    direct = SimpleNamespace(chat_json=AsyncMock(return_value=deepcopy(ANSWER)), close=AsyncMock())
    if scenario == "both_failed":
        direct.chat_json.side_effect = SenseAudioError("synthetic failure", retryable=True)

    def service(db, settings, *, platform):
        result = InferenceService(db, settings, platform=platform, direct_factory=lambda *a, **kw: direct)
        result.assert_actor_current = AsyncMock()
        return result

    persist = AsyncMock()
    monkeypatch.setattr(business, "load_runtime_configuration", AsyncMock(return_value=SimpleNamespace(
        settings=config, prompt_overrides={"opportunity_draft": "原业务覆盖规则"},
    )))
    monkeypatch.setattr(business, "filtered_facts_runtime", runtime_factory)
    monkeypatch.setattr(business, "InferenceService", service)
    monkeypatch.setattr(business, "SenseAudioClient", lambda *a, **kw: direct)
    monkeypatch.setattr(business, "AgentRunStore", lambda *a, **kw: SimpleNamespace(persist_result=persist))
    handler = business.AgentRunHandler(None, config)
    run = RunInput("55555555-5555-4555-8555-555555555555", "conversation", "新增独立项目，金额8万元",
                   "opportunity_draft", FACTS["customer"]["id"], ACTOR)
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
    result = persist.call_args.args[1]
    assert "inference_route" not in result
    normal = scenario in {"create", "update", "none"}
    assert direct.chat_json.await_count == (not normal)
    if scenario == "none":
        assert result["action"] == "none" and not result["name"] and result["amount"] is None
        assert result["missing_fields"] == []
    elif scenario == "update":
        assert result["opportunity_id"] == OPPORTUNITY and result["amount"] == 1200
        assert result["probability"] is None  # Do not copy historical 30% into this change.
    else:
        assert result["action"] == "create" and result["amount"] == 80000
    if scenario == "off":
        trace = persist.call_args.kwargs["inference_trace"]
        assert trace["provider"] == "senseaudio" and trace["fallback_reason"] is None
        assert "platform_run" not in trace and not requests
    else:
        trace = persist.call_args.kwargs["inference_trace"]
        assert trace["provider"] == ("agent_platform" if normal else "senseaudio")
        assert trace["platform_run"]["tool_authority_issued"] is False
        if scenario == "blocked":
            assert trace["fallback_reason"] == "ControlledPlatformBlock" and not requests
        else:
            query = json.loads(json.loads(requests[0].content)["query"])
            assert query["facts"] == FACTS and query["mode"] == "opportunity_draft"
            assert query["user_text"] == run.text and KEY not in json.dumps(query)
    if direct.chat_json.await_count:
        assert "原业务覆盖规则" in direct.chat_json.call_args.kwargs["messages"][0].content
        direct.close.assert_awaited_once()
    await asyncio.sleep(0)
