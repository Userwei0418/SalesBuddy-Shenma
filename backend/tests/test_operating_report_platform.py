"""Role-specific report results must retain the same contract on either provider."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from sales_backend.config import get_settings
from sales_backend.domain.agent import RoleCode
from sales_backend.integrations.senseaudio import SenseAudioError
from sales_backend.integrations.supreme_fde import FdeClient
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import InferenceService, binding_for
from sales_backend.services.agent_platform.pilot import operating_report_pilot_policy, today_tasks_pilot_policy
from sales_backend.services.agent_platform.result_contracts import validate_run_result
from sales_backend.services.agent_platform.routing import InvalidAgentResult
from sales_backend.services.agent_run import handler as business
from sales_backend.services.agent_run.contract import INSTANT_SUMMARY_CONTRACT
from sales_backend.services.agent_run.models import RunInput
from tests.test_fde_facts_runtime import ACTOR, SNAPSHOT, Chunks, frame

AGENT = "synthetic-operating-report-agent"
KEY = "app-synthetic-operating-report-key"


def answer(role):
    return {
        "title": "模型标题不决定业务角色",
        "summary": "当前数据中暂无需处理事项。",
        **{key: [] for key in INSTANT_SUMMARY_CONTRACT[role][1]},
    }


def configured(actor=ACTOR, *, enabled=True, blocked=False, **kwargs):
    return replace(
        get_settings(),
        **{
            "database_url": "",
            "senseaudio_api_key": "",
            "agent_fde_pilot_path": "",
            "max_retries": 0,
            "agent_fde_base_url": "https://platform.invalid/v1",
            "agent_fde_operating_report_id": AGENT,
            "agent_fde_operating_report_api_key": KEY,
            "agent_fde_pilot_json": json.dumps(
                {
                    actor.workspace_id: {
                        "capabilities": {
                            "operating_report": {
                                "enabled": enabled,
                                "user_ids": [actor.user_id],
                                "block_platform_requests": blocked,
                                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                            }
                        }
                    }
                }
            ),
            "agent_platform_bindings_json": json.dumps(
                {
                    actor.workspace_id: {
                        "operating_report": {
                            "enabled": True,
                            "execution_mode": "filtered_facts",
                            "agent_id": AGENT,
                            "expected_snapshot_id": SNAPSHOT,
                        }
                    }
                }
            ),
            "agent_inference_platform_seconds": 0.03,
            "agent_inference_total_seconds": 3,
            **kwargs,
        },
    )


def test_report_has_dedicated_binding_and_cannot_borrow_another_key(monkeypatch):
    monkeypatch.setenv("AGENT_FDE_OPERATING_REPORT_ID", AGENT)
    monkeypatch.setenv("AGENT_FDE_OPERATING_REPORT_API_KEY", KEY)
    get_settings.cache_clear()
    try:
        assert get_settings().agent_fde_operating_report_id == AGENT
        assert get_settings().agent_fde_operating_report_api_key == KEY
    finally:
        get_settings.cache_clear()
    settings = configured()
    assert KEY not in repr(settings)
    assert operating_report_pilot_policy(settings, ACTOR)
    assert today_tasks_pilot_policy(settings, ACTOR) is None
    assert binding_for(settings.agent_platform_bindings_json, ACTOR.workspace_id, "operating_report").agent_id == AGENT
    assert (
        filtered_facts_runtime(
            None,
            replace(settings, agent_fde_operating_report_api_key="", agent_fde_today_tasks_api_key="other"),
            capability="operating_report",
        )
        is None
    )
    raw = {
        ACTOR.workspace_id: {
            "capabilities": {
                "operating_report": {"enabled": True, "rollout": "production", "block_platform_requests": True}
            }
        }
    }
    settings = replace(settings, agent_fde_pilot_json=json.dumps(raw))
    assert operating_report_pilot_policy(settings, ACTOR).block_platform_requests is False
    assert operating_report_pilot_policy(settings, ACTOR.model_copy(update={"workspace_id": SNAPSHOT})) is None


@pytest.mark.parametrize("role", [RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER])
@pytest.mark.parametrize("bad", ["summary_object", "item_string", "item_empty", "item_object", "missing_section"])
def test_report_rejects_unrenderable_or_missing_sections(role, bad):
    actor = ACTOR.model_copy(update={"role": role})
    run = RunInput("run", "conversation", "当前总结", "operating_report", None, actor)
    payload = answer(role)
    if bad == "summary_object":
        payload["summary"] = {"text": "bad"}
    elif bad == "missing_section":
        del payload["action_plan"]
    else:
        payload["action_plan"] = [{"item_string": "bad", "item_empty": {}, "item_object": {"title": []}}[bad]]
    with pytest.raises(InvalidAgentResult):
        validate_run_result(run, payload, {"scope": {"scope_label": "本人"}})


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER])
@pytest.mark.parametrize("scenario", ["normal", "off", "blocked", "timeout", "bad_sections", "bad_item", "both_failed"])
async def test_report_handler_routes_role_contract_and_persists_once(monkeypatch, role, scenario):
    actor = ACTOR.model_copy(update={"role": role})
    settings = configured(actor, enabled=scenario != "off", blocked=scenario in {"blocked", "both_failed"})
    facts = {
        "scope": {"role": role.value, "scope_label": "真实权限范围"},
        "visits": [],
        "tasks": [],
        "risks": [],
        "opportunities": [],
        "data_as_of": "2026-09-12T00:00:00Z",
    }
    valid = answer(role)
    valid["action_plan"] = [{"title": "保持当前跟进", "detail": "仅建议", "create_task": True, "owner": SNAPSHOT}]
    payload = deepcopy(valid)
    payload.update(inference_route={"provider": "forged"}, other_role_section=[{"title": "不能进入结果"}])
    if scenario == "bad_sections":
        del payload["action_plan"]
    if scenario == "bad_item":
        payload["action_plan"] = [False]
    requests = []

    def wire(request):
        requests.append(request)
        stream = (
            Chunks([], stall=True)
            if scenario == "timeout"
            else Chunks([frame("message", answer=json.dumps(payload)), frame("message_end")])
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    def runtime_factory(db, config, **kwargs):
        runtime = filtered_facts_runtime(db, config, **kwargs)
        runtime.client_factory = lambda c: FdeClient(c, transport=httpx.MockTransport(wire))
        runtime.observer_factory = lambda *a, **kw: SimpleNamespace(
            start=AsyncMock(return_value="attempt"), finish=AsyncMock()
        )
        return runtime

    direct = SimpleNamespace(chat_json=AsyncMock(return_value=valid), close=AsyncMock())
    if scenario == "both_failed":
        direct.chat_json.side_effect = SenseAudioError("synthetic", retryable=True)

    def service(db, config, *, platform):
        value = InferenceService(db, config, platform=platform, direct_factory=lambda *a, **kw: direct)
        value.assert_actor_current = AsyncMock()
        return value

    persist = AsyncMock()
    monkeypatch.setattr(
        business,
        "load_runtime_configuration",
        AsyncMock(
            return_value=SimpleNamespace(settings=settings, prompt_overrides={"operating_report": "既有总结提示规则"})
        ),
    )
    monkeypatch.setattr(business, "filtered_facts_runtime", runtime_factory)
    monkeypatch.setattr(business, "InferenceService", service)
    monkeypatch.setattr(business, "SenseAudioClient", lambda *a, **kw: direct)
    monkeypatch.setattr(business, "AgentRunStore", lambda *a, **kw: SimpleNamespace(persist_result=persist))
    handler = business.AgentRunHandler(None, settings)
    run = RunInput("55555555-5555-4555-8555-555555555555", "conversation", "当前总结", "operating_report", None, actor)
    handler._load_and_start = AsyncMock(return_value=run)
    handler.facts_loader.load = AsyncMock(return_value=facts)
    if scenario == "both_failed":
        with pytest.raises(SenseAudioError) as exc:
            await handler.handle(run.run_id, actor)
        assert not exc.value.retryable
        persist.assert_not_awaited()
        return
    await handler.handle(run.run_id, actor)
    persist.assert_awaited_once()
    result = persist.call_args.args[1]
    assert result["title"] == INSTANT_SUMMARY_CONTRACT[role][0]
    assert result["scope"] == "真实权限范围" and result["period"] == "当前实时状态"
    assert result["action_plan"] == [{"title": "保持当前跟进", "detail": "仅建议"}]
    assert not set(result) & {"inference_route", "other_role_section"}
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
            assert query["mode"] == "operating_report" and query["facts"] == facts
            assert KEY not in json.dumps(query)
    if direct.chat_json.await_count:
        assert "既有总结提示规则" in direct.chat_json.call_args.kwargs["messages"][0].content
        direct.close.assert_awaited_once()
