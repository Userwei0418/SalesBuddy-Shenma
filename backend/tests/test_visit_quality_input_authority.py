"""Offline handler-boundary checks for the edited quality snapshot."""

import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.services.agent_platform.inference import InferenceService, PlatformOperation
from sales_backend.services.agent_platform.routing import ProviderUnavailable, WriteState
from sales_backend.services.agent_run import handler as business
from sales_backend.services.agent_run.models import RunInput
from sales_backend.services.runtime_config import RuntimeConfiguration
from sales_backend.services.visit_prompts import model_user_text
from tests.test_agent_platform_pilot import ACTOR, settings
from tests.test_visit_split import FACTS, FIELDS


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["platform", "direct"])
@pytest.mark.parametrize("stage", ["quality", "structure"])
@pytest.mark.parametrize("summary", ["Synthetic: arrange a demo; no date supplied.",
                                     "Synthetic old summary: arrange the demo on 2026-10-12."])
async def test_handler_uses_edited_quality_input_without_changing_stored_source(monkeypatch, route, stage, summary):
    config = settings()
    edited = {**FIELDS, "next_action": "2026-10-16: prepare the product guide and arrange a demo."}
    raw = "Synthetic original: arrange a demo when convenient. ORIGINAL_ONLY_MARKER"
    request = {"stage": stage, "fields": deepcopy(edited), "summary": summary}
    run = RunInput("run", "conversation", raw, "visit_entry", "customer", ACTOR, visit_request=request)
    original_request = deepcopy(request)
    facts = (
        {**deepcopy(FACTS), "fields": deepcopy(edited), "summary": summary,
         "relative_time_context": {"current_date": "2026-10-15", "anchor_date": "2026-10-15"}}
        if stage == "quality" else
        {"visit_stage": "structure", "is_first_visit": False,
         "server_fields": {key: FIELDS[key] for key in ("customer_name", "customer_type", "created_date")}}
    )
    facts["data_as_of"] = "2026-10-15T16:12:20+08:00"
    original_facts = deepcopy(facts)
    failed_review = {
        "follow_up_score": 55,
        "suggestions": ["Synthetic model rejection retained."],
        "next_action": {"passed": False, "time_found": False, "goal_or_plan_found": True,
                        "suggestions": ["Synthetic missing date claim."]},
    }
    model_result = ({"quality_review": deepcopy(failed_review)} if stage == "quality"
                    else {"fields": deepcopy(FIELDS), "summary": "Structured synthetic source."})
    monkeypatch.setattr(business, "load_runtime_configuration",
                        AsyncMock(return_value=RuntimeConfiguration(config, {})))
    monkeypatch.setattr(business, "chatbi_pilot_policy", lambda *a: None)
    for name in ("visit_entry_pilot_policy", "visit_quality_pilot_policy"):
        monkeypatch.setattr(business, name, lambda *a: (
            SimpleNamespace(block_platform_requests=False) if route == "platform" else None))
    monkeypatch.setattr(business, "filtered_facts_runtime", lambda *a, **k: None)
    persist = AsyncMock()
    monkeypatch.setattr(business, "AgentRunStore", lambda *a, **k: SimpleNamespace(persist_result=persist))
    calls = {"platform": 0, "direct": 0, "audit": 0, "close": 0}
    captured = {}

    async def evaluate(**kwargs):
        calls["platform"] += 1
        captured.update(kwargs)
        return SimpleNamespace(payload=kwargs["validate"](deepcopy(model_result)), trace={})

    monkeypatch.setattr(business, "InferenceService", lambda *a, **k: SimpleNamespace(evaluate=evaluate))

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def chat_json(self, *, messages, temperature):
            calls["direct"] += 1
            captured["messages"] = messages
            assert temperature == 0.1
            return deepcopy(model_result)

        async def close(self):
            calls["close"] += 1

    async def audit(**kwargs):
        calls["audit"] += 1
        captured["facts"] = kwargs["facts"]
        return kwargs["validate"](await kwargs["invoke"](None)), {}

    monkeypatch.setattr(business, "SenseAudioClient", Client)
    monkeypatch.setattr(business, "audit_original", audit)
    handler = business.AgentRunHandler(None, config)
    handler._load_and_start = AsyncMock(return_value=run)
    handler.facts_loader.load = AsyncMock(return_value=facts)
    await handler.handle("run", ACTOR)

    direct_envelope = json.loads(captured["messages"][1].content)
    expected_text = "" if stage == "quality" else raw
    assert direct_envelope["user_text"] == expected_text
    assert direct_envelope["facts"] == original_facts
    assert direct_envelope["current_time"] == original_facts["data_as_of"]
    if route == "platform":
        assert captured["user_text"] == direct_envelope["user_text"]
        assert captured["facts"] == direct_envelope["facts"]
        assert calls == {"platform": 1, "direct": 0, "audit": 0, "close": 0}
    else:
        assert calls == {"platform": 0, "direct": 1, "audit": 1, "close": 1}
    if stage == "quality":
        assert "ORIGINAL_ONLY_MARKER" not in "\n".join(m.content for m in captured["messages"])
        assert direct_envelope["facts"]["fields"]["next_action"] == edited["next_action"]
        assert direct_envelope["facts"]["summary"] == summary
        assert "facts.summary不能替代、补齐或否定该字段" in captured["messages"][0].content
        saved = persist.await_args.args[1]
        assert saved["fields"] == edited and saved["summary"] == summary
        assert all(saved["quality_review"][key] == value for key, value in failed_review.items())
    persist.assert_awaited_once()
    assert persist.await_args.args[0] is run
    assert run.text == raw and run.visit_request == original_request
    assert facts == original_facts


@pytest.mark.parametrize("mode", ["chatbi", "today_tasks", "operating_report", "opportunity_draft"])
def test_non_visit_quality_capabilities_retain_original_text(mode):
    run = RunInput("run", "conversation", "Original source", mode, None, ACTOR)
    assert model_user_text(run) == run.text


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["platform_success", "platform_failure_fallback", "pilot_off"])
async def test_quality_real_router_preserves_input_and_call_limits(monkeypatch, route):
    """Run the actual handler, inference router and original audit; fake providers only."""
    bindings = {ACTOR.workspace_id: {"visit_quality": {
        "enabled": True, "execution_mode": "filtered_facts", "agent_id": "synthetic-quality",
        "expected_snapshot_id": "33333333-3333-4333-8333-333333333333",
    }}}
    config = replace(settings(), agent_platform_bindings_json=json.dumps(bindings),
                     agent_inference_total_seconds=5, agent_inference_platform_seconds=1)
    fields = {**FIELDS, "next_action": "2026-10-16: arrange the synthetic demo."}
    facts = {**deepcopy(FACTS), "fields": fields, "summary": "Old summary without the edited date.",
             "data_as_of": "2026-10-15T16:12:20+08:00"}
    answer = {"quality_review": {
        "follow_up_score": 55, "suggestions": ["Synthetic rejection remains a rejection."],
        "next_action": {"passed": False, "time_found": False, "goal_or_plan_found": True,
                        "suggestions": ["Synthetic date conflict."]},
    }}
    captured = []
    invoke = AsyncMock(return_value=deepcopy(answer))
    if route == "platform_failure_fallback":
        invoke.side_effect = ProviderUnavailable("Synthetic transport failure")
    seal = AsyncMock(return_value=WriteState.NONE)

    def operation(request):
        captured.append(request.input)
        return PlatformOperation(invoke, seal)

    platform = SimpleNamespace(operation=operation)
    direct = SimpleNamespace(chat_json=AsyncMock(return_value=deepcopy(answer)), close=AsyncMock())

    def service(db, runtime_settings, *, platform):
        inference = InferenceService(db, runtime_settings, platform=platform,
                                     direct_factory=lambda *a, **k: direct)
        inference.assert_actor_current = AsyncMock()
        return inference

    monkeypatch.setattr(business, "load_runtime_configuration",
                        AsyncMock(return_value=RuntimeConfiguration(config, {})))
    monkeypatch.setattr(business, "chatbi_pilot_policy", lambda *a: None)
    monkeypatch.setattr(business, "visit_quality_pilot_policy", lambda *a: (
        None if route == "pilot_off" else SimpleNamespace(block_platform_requests=False)))
    monkeypatch.setattr(business, "filtered_facts_runtime", lambda *a, **k: platform)
    monkeypatch.setattr(business, "InferenceService", service)
    monkeypatch.setattr(business, "SenseAudioClient", lambda *a, **k: direct)
    persist = AsyncMock()
    monkeypatch.setattr(business, "AgentRunStore", lambda *a, **k: SimpleNamespace(persist_result=persist))
    run = RunInput("55555555-5555-4555-8555-555555555555", "conversation",
                   "ORIGINAL_ONLY_MARKER: arrange a demo, date not specified.", "visit_entry", "customer", ACTOR,
                   visit_request={"stage": "quality", "fields": deepcopy(fields), "summary": facts["summary"]})
    original = deepcopy(run)
    handler = business.AgentRunHandler(None, config)
    handler._load_and_start = AsyncMock(return_value=run)
    handler.facts_loader.load = AsyncMock(return_value=deepcopy(facts))
    await handler.handle(run.run_id, ACTOR)
    assert run == original
    assert invoke.await_count == (0 if route == "pilot_off" else 1)
    assert direct.chat_json.await_count == (0 if route == "platform_success" else 1)
    assert direct.close.await_count == direct.chat_json.await_count
    if captured:
        assert len(captured) == 1
        assert captured[0]["user_text"] == "" and captured[0]["facts"] == facts
    if direct.chat_json.await_count:
        messages = direct.chat_json.await_args.kwargs["messages"]
        sent = json.loads(messages[1].content)
        assert sent["user_text"] == "" and sent["facts"] == facts
        assert "ORIGINAL_ONLY_MARKER" not in "\n".join(m.content for m in messages)
    persist.assert_awaited_once()
    saved = persist.await_args.args[1]
    assert saved["fields"] == fields and saved["summary"] == facts["summary"]
    assert all(saved["quality_review"][k] == v for k, v in answer["quality_review"].items())
    trace = persist.await_args.kwargs["inference_trace"]
    assert trace["provider"] == ("agent_platform" if route == "platform_success" else "senseaudio")
    assert (trace["fallback_reason"] is not None) == (route == "platform_failure_fallback")
