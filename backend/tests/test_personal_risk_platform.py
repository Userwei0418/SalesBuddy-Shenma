"""Actual risk handler and FDE parser, with synthetic provider HTTP only."""

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
)
from sales_backend.services.agent_platform.routing import ProviderUnavailable
from sales_backend.services.agent_run import handler as business
from sales_backend.services.agent_run.models import RunInput
from tests.test_fde_facts_runtime import ACTOR, SNAPSHOT, Chunks, frame

AGENT = "synthetic-personal-risk-agent"
KEY = "app-synthetic-personal-risk-key"
VISIT = "66666666-6666-4666-8666-666666666666"
FACTS = {"visits": [{"source_visit_id": VISIT, "customer_id": "77777777-7777-4777-8777-777777777777",
                     "follow_up_record": "客户预算尚未获批", "next_action": "下周与财务确认"}],
         "existing_open_risks": [], "data_as_of": "2026-09-12T00:00:00Z"}
ANSWER = {"title": "个人客户风险", "summary": "预算尚未获批", "risks": [{
    "source_visit_id": VISIT, "risk_type": "budget_risk", "title": "预算待批", "description": "客户预算尚未获批",
    "evidence_detail": "客户预算尚未获批", "suggested_action": "与财务确认预算", "severity": "high",
    "due_at": "2027-01-01T09:00:00+08:00",
}]}


def configured(*, enabled=True, blocked=False, **kwargs):
    raw = {ACTOR.workspace_id: {"capabilities": {"personal_risks": {
        "enabled": enabled, "user_ids": [ACTOR.user_id], "block_platform_requests": blocked,
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }}}}
    bindings = {ACTOR.workspace_id: {"personal_risks": {
        "enabled": True, "execution_mode": "filtered_facts", "agent_id": AGENT,
        "expected_snapshot_id": SNAPSHOT,
    }}}
    return replace(get_settings(), **{
        "agent_fde_base_url": "https://platform.invalid/v1", "agent_fde_personal_risks_id": AGENT,
        "agent_fde_personal_risks_api_key": KEY, "agent_fde_pilot_path": "",
        "agent_platform_bindings_json": json.dumps(bindings), "agent_fde_pilot_json": json.dumps(raw),
        "database_url": "", "senseaudio_api_key": "", "max_retries": 0,
        "agent_inference_platform_seconds": .03, "agent_inference_total_seconds": 3,
        **kwargs,
    })


def test_personal_risk_configuration_does_not_reuse_other_agent_authority():
    config = configured()
    assert personal_risk_pilot_policy(config, ACTOR) is not None
    assert battle_map_pilot_policy(config, ACTOR) is None
    assert opportunity_pilot_policy(config, ACTOR) is None
    assert chatbi_pilot_policy(config, ACTOR, "chatbi") is None
    raw = {ACTOR.workspace_id: {"capabilities": {
        cap: {"enabled": True, "rollout": "production"} for cap in ("battle_map_review", "opportunity_draft")
    }}}
    assert personal_risk_pilot_policy(replace(config, agent_fde_pilot_json=json.dumps(raw)), ACTOR) is None
    assert filtered_facts_runtime(None, replace(config, agent_fde_personal_risks_api_key="",
                                  agent_fde_opportunity_api_key="other"), capability="personal_risks") is None
    assert KEY not in repr(config)


@pytest.mark.parametrize("change", [
    {"enabled": False}, {"user_ids": []}, {"user_ids": ["88888888-8888-4888-8888-888888888888"]},
    {"expires_at": "2026-01-01T00:00:00Z"}, {"rollout": "unknown"},
])
def test_personal_risk_pilot_rejects_missing_or_expired_authority(change):
    config = configured()
    raw = json.loads(config.agent_fde_pilot_json)
    raw[ACTOR.workspace_id]["capabilities"]["personal_risks"].update(change)
    assert personal_risk_pilot_policy(replace(config, agent_fde_pilot_json=json.dumps(raw)), ACTOR) is None


def test_production_ignores_test_fault_and_adapter_enforces_agent_mode_binding():
    raw = {ACTOR.workspace_id: {"capabilities": {"personal_risks": {
        "enabled": True, "rollout": "production", "block_platform_requests": True,
    }}}}
    config = configured(agent_fde_pilot_json=json.dumps(raw))
    assert personal_risk_pilot_policy(config, ACTOR).block_platform_requests is False
    assert personal_risk_pilot_policy(config, ACTOR.model_copy(update={"workspace_id": str(SNAPSHOT)})) is None
    runtime = filtered_facts_runtime(None, config, capability="personal_risks")
    assert runtime.config.api_key == KEY
    binding = binding_for(config.agent_platform_bindings_json, ACTOR.workspace_id, "personal_risks")
    request = PlatformRequest("operation", "personal_risks", binding, ACTOR, {"mode": "personal_risks"})
    assert runtime.operation(request)
    for invalid in (replace(request, capability="opportunity_draft"), replace(request, input={"mode": "chatbi"}),
                    replace(request, binding=AgentBinding("other", SNAPSHOT, "filtered_facts"))):
        with pytest.raises(ProviderUnavailable):
            runtime.operation(invalid)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "normal", "empty", "off", "blocked", "timeout", "foreign_visit", "invalid_enum", "missing_evidence",
    "duplicates", "too_many", "invalid_title", "both_failed",
])
async def test_personal_risk_handler_validates_before_any_persistence(monkeypatch, scenario):
    config = configured(enabled=scenario != "off", blocked=scenario in {"blocked", "both_failed"})
    payload = deepcopy(ANSWER)
    if scenario == "empty":
        payload["risks"] = []
    if scenario == "foreign_visit":
        payload["risks"][0]["source_visit_id"] = "foreign"
    if scenario == "invalid_enum":
        payload["risks"][0]["risk_type"] = "next_action_missing"
    if scenario == "missing_evidence":
        payload["risks"][0]["evidence_detail"] = ""
    if scenario in {"duplicates", "too_many"}:
        payload["risks"] *= 2 if scenario == "duplicates" else 9
    if scenario == "invalid_title":
        payload["title"] = {"untrusted": "object"}
    if scenario != "off":
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

    direct = SimpleNamespace(chat_json=AsyncMock(return_value=deepcopy(ANSWER)), close=AsyncMock())
    if scenario == "both_failed":
        direct.chat_json.side_effect = SenseAudioError("synthetic failure", retryable=True)

    def service(db, settings, *, platform):
        result = InferenceService(db, settings, platform=platform, direct_factory=lambda *a, **kw: direct)
        result.assert_actor_current = AsyncMock()
        return result

    persist = AsyncMock()
    monkeypatch.setattr(business, "load_runtime_configuration", AsyncMock(return_value=SimpleNamespace(
        settings=config, prompt_overrides={"personal_risks": "原风险规则覆盖"},
    )))
    monkeypatch.setattr(business, "filtered_facts_runtime", runtime_factory)
    monkeypatch.setattr(business, "InferenceService", service)
    monkeypatch.setattr(business, "SenseAudioClient", lambda *a, **kw: direct)
    monkeypatch.setattr(business, "AgentRunStore", lambda *a, **kw: SimpleNamespace(persist_result=persist))
    handler = business.AgentRunHandler(None, config)
    run = RunInput("55555555-5555-4555-8555-555555555555", "conversation", "查看个人风险",
                   "personal_risks", None, ACTOR)
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
    normal = scenario in {"normal", "empty"}
    assert direct.chat_json.await_count == (not normal)
    assert result["risks"] == ([] if scenario == "empty" else ANSWER["risks"])
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
            assert query["facts"] == FACTS and query["mode"] == "personal_risks"
            assert KEY not in json.dumps(query)
    if direct.chat_json.await_count:
        assert "原风险规则覆盖" in direct.chat_json.call_args.kwargs["messages"][0].content
        direct.close.assert_awaited_once()
