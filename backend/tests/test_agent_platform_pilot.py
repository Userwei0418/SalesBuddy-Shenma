import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.config import get_settings
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.services.agent_platform.inference import InferenceService, PlatformOperation
from sales_backend.services.agent_platform.pilot import chatbi_pilot_policy
from sales_backend.services.agent_platform.routing import ControlledPlatformBlock, WriteState
from sales_backend.services.agent_run import handler as handler_module
from sales_backend.services.agent_run.models import RunInput

ACTOR = ActorContext(
    workspace_id="11111111-1111-4111-8111-111111111111",
    user_id="22222222-2222-4222-8222-222222222222",
    role=RoleCode.SALES,
    data_scope=DataScope.SELF,
)
NOW = datetime(2026, 9, 12, tzinfo=UTC)
ANSWER = {"summary": "合成结果", "metrics": [{"label": "任务", "value": "3"}], "rows": []}
FACTS = {"data_as_of": "2026-09-12T00:00:00Z", "summary": {"open_tasks": 3}}


def settings(*, enabled=True, blocked=False, **kwargs):
    config = {
        ACTOR.workspace_id: {
            "enabled": enabled,
            "user_ids": [ACTOR.user_id],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "block_platform_requests": blocked,
        }
    }
    bindings = {
        ACTOR.workspace_id: {
            "chatbi": {
                "enabled": True,
                "agent_id": "synthetic-agent",
                "execution_mode": "filtered_facts",
                "expected_snapshot_id": "33333333-3333-4333-8333-333333333333",
            }
        }
    }
    return replace(
        get_settings(),
        **{
            "database_url": "",
            "senseaudio_api_key": "",
            "agent_fde_pilot_path": "",
            "agent_fde_pilot_json": json.dumps(config),
            "agent_platform_bindings_json": json.dumps(bindings),
            **kwargs,
        },
    )


@pytest.mark.parametrize("broken", ["{}", "null", "[]", "bad", " " * 64001])
def test_missing_or_malformed_pilot_cannot_enable_platform(broken):
    assert chatbi_pilot_policy(settings(agent_fde_pilot_json=broken), ACTOR, "chatbi") is None


@pytest.mark.parametrize(
    "change",
    [
        {"enabled": False},
        {"enabled": "true"},
        {"user_ids": []},
        {"user_ids": "*"},
        {"user_ids": ["*"]},
        {"user_ids": [None]},
        {"user_ids": ["99999999-9999-4999-8999-999999999999"]},
        {"expires_at": "2026-09-11T00:00:00Z"},
        {"expires_at": "2026-09-13"},
        {"expires_at": "bad"},
    ],
)
def test_pilot_requires_explicit_current_user_and_expiry(change):
    config = json.loads(settings().agent_fde_pilot_json)
    config[ACTOR.workspace_id].update(change)
    assert chatbi_pilot_policy(settings(agent_fde_pilot_json=json.dumps(config)), ACTOR, "chatbi", now=NOW) is None


@pytest.mark.parametrize(
    "mode", ["visit_entry", "personal_risks", "today_tasks", "operating_report", "battle_map_review"]
)
def test_pilot_never_enables_other_capabilities(mode):
    assert chatbi_pilot_policy(settings(), ACTOR, mode) is None


def test_pilot_is_scoped_to_workspace_and_supports_both_question_modes():
    assert chatbi_pilot_policy(settings(), ACTOR, "chatbi") is not None
    assert chatbi_pilot_policy(settings(), ACTOR, "customer_chatbi") is not None
    other = ACTOR.model_copy(update={"workspace_id": "99999999-9999-4999-8999-999999999999"})
    assert chatbi_pilot_policy(settings(), other, "chatbi") is None


def test_operator_file_is_read_each_run_and_missing_invalid_or_expired_file_keeps_original(tmp_path):
    path = tmp_path / "pilot.json"
    config = settings(agent_fde_pilot_path=str(path))
    assert chatbi_pilot_policy(config, ACTOR, "chatbi") is None
    path.write_text(settings(blocked=True).agent_fde_pilot_json)
    assert chatbi_pilot_policy(config, ACTOR, "chatbi").block_platform_requests is True
    path.write_text(settings(blocked=False).agent_fde_pilot_json)
    assert chatbi_pilot_policy(config, ACTOR, "chatbi").block_platform_requests is False
    path.write_text(settings(enabled=False).agent_fde_pilot_json)
    assert chatbi_pilot_policy(config, ACTOR, "chatbi") is None
    path.write_text("x" * 64001)
    assert chatbi_pilot_policy(config, ACTOR, "chatbi") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled,blocked,provider",
    [(True, False, "agent_platform"), (True, True, "senseaudio"), (False, True, "senseaudio")],
)
async def test_actual_handler_selects_pilot_or_original_and_persists_one_validated_result(
    monkeypatch, enabled, blocked, provider
):
    config = settings(enabled=enabled, blocked=blocked)
    run = RunInput(
        "44444444-4444-4444-8444-444444444444",
        "55555555-5555-4555-8555-555555555555",
        "合成问题",
        "chatbi",
        None,
        ACTOR,
    )
    direct = SimpleNamespace(chat_json=AsyncMock(return_value=ANSWER.copy()), close=AsyncMock())
    persist = AsyncMock()
    platform_invoked = AsyncMock(return_value=ANSWER.copy())
    sealed = AsyncMock(return_value=WriteState.NONE)
    constructed = []
    if blocked:
        platform_invoked.side_effect = ControlledPlatformBlock("controlled test")

    def build_runtime(database, runtime_settings, *, block_requests, capability):
        assert capability == "chatbi"
        constructed.append((runtime_settings, block_requests))
        return SimpleNamespace(operation=lambda request: PlatformOperation(platform_invoked, sealed))

    def build_inference(database, runtime_settings, *, platform):
        service = InferenceService(database, runtime_settings, platform=platform, direct_factory=lambda *a, **k: direct)
        service.assert_actor_current = AsyncMock()
        return service

    monkeypatch.setattr(
        handler_module,
        "load_runtime_configuration",
        AsyncMock(return_value=SimpleNamespace(settings=config, prompt_overrides={})),
    )
    monkeypatch.setattr(handler_module, "filtered_facts_runtime", build_runtime)
    monkeypatch.setattr(handler_module, "InferenceService", build_inference)
    monkeypatch.setattr(handler_module, "SenseAudioClient", lambda *a, **k: direct)
    monkeypatch.setattr(handler_module, "AgentRunStore", lambda *a, **k: SimpleNamespace(persist_result=persist))
    handler = handler_module.AgentRunHandler(None, config)
    handler._load_and_start = AsyncMock(return_value=run)
    handler.facts_loader.load = AsyncMock(return_value=FACTS)
    handler.prompts.build = lambda *a, **k: []
    await handler.handle(run.run_id, ACTOR)
    persist.assert_awaited_once()
    assert persist.call_args.args[0] is run
    assert persist.call_args.args[1]["summary"] == ANSWER["summary"]
    assert direct.chat_json.await_count == (provider == "senseaudio")
    if enabled:
        assert constructed == [(config, blocked)]
        platform_invoked.assert_awaited_once()
        trace = persist.call_args.kwargs["inference_trace"]
        assert trace["provider"] == provider
        assert trace["fallback_reason"] == ("ControlledPlatformBlock" if blocked else None)
        assert persist.call_args.args[1]["scope"] == "本人"
        sealed.assert_awaited_once()
    else:
        assert not constructed
        platform_invoked.assert_not_awaited()
        trace = persist.call_args.kwargs["inference_trace"]
        assert trace["provider"] == "senseaudio" and trace["fallback_reason"] is None
        assert "platform_run" not in trace
