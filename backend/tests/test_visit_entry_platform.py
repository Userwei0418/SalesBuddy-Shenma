"""Visit business handler and FDE parser; synthetic provider HTTP, no live data."""

import json
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from sales_backend.config import get_settings
from sales_backend.domain.visit_contract import PROMPT_VERSION, ensure_visit_result
from sales_backend.domain.visit_review import review_text
from sales_backend.integrations.senseaudio import SenseAudioError
from sales_backend.integrations.supreme_fde import FdeClient
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import AgentBinding, InferenceService, PlatformRequest, binding_for
from sales_backend.services.agent_platform.pilot import (
    battle_map_pilot_policy,
    chatbi_pilot_policy,
    opportunity_pilot_policy,
    personal_risk_pilot_policy,
    visit_entry_pilot_policy,
)
from sales_backend.services.agent_platform.result_contracts import validate_run_result
from sales_backend.services.agent_platform.routing import ProviderUnavailable
from sales_backend.services.agent_run import handler as business
from sales_backend.services.agent_run.facts import AgentFactsLoader
from sales_backend.services.agent_run.models import RunInput
from tests.test_fde_facts_runtime import ACTOR, SNAPSHOT, Chunks, frame

AGENT = "synthetic-visit-entry-agent"
KEY = "app-synthetic-visit-entry-key"
SERVER_FIELDS = {
    "customer_name": "【演示】数据库绑定客户",
    "customer_type": "伙伴",
    "created_date": "2026-09-13",
    "recorder_user_id": "当前记录人",
}
FACTS = {"data_as_of": "2026-09-12T00:00:00Z", "server_fields": SERVER_FIELDS}
FIELDS = {
    "customer_name": "【演示】拜访结构化验收",
    "contact_name": "王经理",
    "is_first_visit": False,
    "interaction_at": "2026-09-12",
    "created_date": "2026-09-12",
    "customer_type": "商机客户",
    "recorder_user_id": "模型猜测人员",
    "follow_up_record": "客户测试5条问题，4条准确，1条缺少最新退款规则；同意补齐资料后小范围试点。",
    "next_action": "9月15日前销售提供试点方案，王经理提供20条脱敏样本。",
}
ANSWER = {
    "fields": FIELDS,
    "summary": "已整理沟通内容与下一步计划",
    "quality_review": {
        "follow_up_score": 85,
        "suggestions": [],
        "next_action": {"passed": True, "time_found": True, "goal_or_plan_found": True, "suggestions": []},
    },
}
SOURCE_TEXT = "录入类型：再次拜访\n客户：【演示】拜访结构化验收\n" + review_text(FIELDS).removeprefix("拜访审核 v2\n")


def configured(*, enabled=True, blocked=False, **kwargs):
    raw = {
        ACTOR.workspace_id: {
            "capabilities": {
                "visit_entry": {
                    "enabled": enabled,
                    "user_ids": [ACTOR.user_id],
                    "block_platform_requests": blocked,
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                }
            }
        }
    }
    bindings = {
        ACTOR.workspace_id: {
            "visit_entry": {
                "enabled": True,
                "execution_mode": "filtered_facts",
                "agent_id": AGENT,
                "expected_snapshot_id": SNAPSHOT,
            }
        }
    }
    return replace(
        get_settings(),
        **{
            "agent_fde_base_url": "https://platform.invalid/v1",
            "agent_fde_visit_entry_id": AGENT,
            "agent_fde_visit_entry_api_key": KEY,
            "agent_fde_pilot_path": "",
            "agent_platform_bindings_json": json.dumps(bindings),
            "agent_fde_pilot_json": json.dumps(raw),
            "database_url": "",
            "senseaudio_api_key": "",
            "max_retries": 0,
            "agent_inference_platform_seconds": 0.03,
            "agent_inference_total_seconds": 3,
            **kwargs,
        },
    )


def test_visit_configuration_is_separate_from_other_agents(monkeypatch):
    monkeypatch.setenv("AGENT_FDE_VISIT_ENTRY_ID", AGENT)
    monkeypatch.setenv("AGENT_FDE_VISIT_ENTRY_API_KEY", KEY)
    get_settings.cache_clear()
    try:
        assert get_settings().agent_fde_visit_entry_id == AGENT
        assert get_settings().agent_fde_visit_entry_api_key == KEY
    finally:
        get_settings.cache_clear()
    config = configured()
    assert visit_entry_pilot_policy(config, ACTOR) is not None
    assert all(
        policy(config, ACTOR) is None
        for policy in (
            battle_map_pilot_policy,
            opportunity_pilot_policy,
            personal_risk_pilot_policy,
        )
    )
    assert chatbi_pilot_policy(config, ACTOR, "chatbi") is None
    raw = {
        ACTOR.workspace_id: {
            "capabilities": {
                cap: {"enabled": True, "rollout": "production"}
                for cap in ("battle_map_review", "opportunity_draft", "personal_risks")
            }
        }
    }
    assert visit_entry_pilot_policy(replace(config, agent_fde_pilot_json=json.dumps(raw)), ACTOR) is None
    assert (
        filtered_facts_runtime(
            None,
            replace(config, agent_fde_visit_entry_api_key="", agent_fde_personal_risks_api_key="other"),
            capability="visit_entry",
        )
        is None
    )
    assert KEY not in repr(config)


@pytest.mark.parametrize(
    "change",
    [
        {"enabled": False},
        {"user_ids": []},
        {"user_ids": ["88888888-8888-4888-8888-888888888888"]},
        {"expires_at": "2026-01-01T00:00:00Z"},
        {"rollout": "unknown"},
    ],
)
def test_visit_pilot_rejects_missing_or_expired_authority(change):
    config = configured()
    raw = json.loads(config.agent_fde_pilot_json)
    raw[ACTOR.workspace_id]["capabilities"]["visit_entry"].update(change)
    assert visit_entry_pilot_policy(replace(config, agent_fde_pilot_json=json.dumps(raw)), ACTOR) is None


def test_visit_production_and_exact_agent_mode_binding():
    raw = {
        ACTOR.workspace_id: {
            "capabilities": {
                "visit_entry": {
                    "enabled": True,
                    "rollout": "production",
                    "block_platform_requests": True,
                }
            }
        }
    }
    config = configured(agent_fde_pilot_json=json.dumps(raw))
    assert visit_entry_pilot_policy(config, ACTOR).block_platform_requests is False
    assert visit_entry_pilot_policy(config, ACTOR.model_copy(update={"workspace_id": SNAPSHOT})) is None
    runtime = filtered_facts_runtime(None, config, capability="visit_entry")
    assert runtime.config.api_key == KEY
    binding = binding_for(config.agent_platform_bindings_json, ACTOR.workspace_id, "visit_entry")
    request = PlatformRequest("operation", "visit_entry", binding, ACTOR, {"mode": "visit_entry"})
    assert runtime.operation(request)
    for invalid in (
        replace(request, capability="personal_risks"),
        replace(request, input={"mode": "chatbi"}),
        replace(request, binding=AgentBinding("other", SNAPSHOT, "filtered_facts")),
    ):
        with pytest.raises(ProviderUnavailable):
            runtime.operation(invalid)


@pytest.mark.asyncio
async def test_visit_facts_do_not_query_or_disclose_business_aggregates():
    @asynccontextmanager
    async def transaction(actor, readonly):
        assert actor == ACTOR and readonly is True

        async def fetchrow(sql, *args):
            if "config.rule_set" in sql:
                assert args == ("visit_admission",)
                return None
            assert "FROM platform.user_ref" in sql and "Asia/Shanghai" in sql
            assert args == (ACTOR.user_id, ACTOR.workspace_id)
            return {"display_name": "当前记录人", "created_date": "2026-09-13"}

        async def fetchval(sql, *args):
            assert sql == "SELECT security.customer_reference($1::uuid)"
            return {"name": "已选客户", "customer_type_code": "prospect"}

        yield SimpleNamespace(fetchrow=fetchrow, fetchval=fetchval)  # Reference only; no CRM aggregates.

    run = RunInput("run", "conversation", SOURCE_TEXT, "visit_entry", "selected", ACTOR)
    facts = await AgentFactsLoader(SimpleNamespace(transaction=transaction)).load(run)
    assert set(facts) == {"data_as_of", "visit_stage", "is_first_visit", "server_fields"}
    assert facts["server_fields"] == {
        "customer_name": "已选客户",
        "customer_type": "客户",
        "created_date": "2026-09-13",
        "recorder_user_id": "当前记录人",
    }
    assert facts["visit_stage"] == "structure"
    assert datetime.fromisoformat(facts["data_as_of"]).tzinfo is not None


def test_visit_server_fields_ignore_model_envelope_and_preserve_review_content():
    answer = deepcopy(ANSWER)
    answer["server_fields"] = {"customer_type": "伪造主档", "recorder_user_id": "其他人员"}
    run = RunInput("run", "conversation", review_text(FIELDS), "visit_entry", None, ACTOR)
    result = validate_run_result(run, answer, FACTS)
    assert "server_fields" not in result
    assert {key: result["fields"][key] for key in SERVER_FIELDS} == SERVER_FIELDS
    assert review_text(result["fields"]) == run.text
    assert answer["fields"] == FIELDS  # Provider evidence is not mutated in-place.
    assert result["quality_review"]["follow_up_score"] == 85
    unbound = {key: value for key, value in SERVER_FIELDS.items() if key != "customer_name"}
    unbound["customer_type"] = ""
    result = validate_run_result(run, answer, {**FACTS, "server_fields": unbound})
    assert result["fields"]["customer_name"] == FIELDS["customer_name"]
    assert result["fields"]["customer_type"] == ""


@pytest.mark.parametrize(
    "invalid",
    [
        {"customer_type": "伙伴"},
        {**SERVER_FIELDS, "follow_up_score": "100"},
        {**SERVER_FIELDS, "customer_name": {"name": "不应自由传入"}},
    ],
)
def test_visit_server_context_has_a_fixed_field_contract(invalid):
    with pytest.raises(ValueError, match="系统字段上下文"):
        ensure_visit_result(ANSWER, server_fields=invalid)


def answer_for(scenario):
    answer = deepcopy(ANSWER)
    if scenario in {"first_visit", "first_missing"}:
        answer["fields"].update(
            is_first_visit=True,
            customer_main_business="跨境客服服务",
            customer_needs="统一退款规则",
            customer_budget="客户预算尚未确定",
            contact_role="使用者",
        )
        if scenario == "first_missing":
            answer["fields"]["contact_role"] = ""
    if scenario in {"incomplete", "low_score", "next_rejected"}:
        answer["quality_review"]["suggestions"] = ["请补充客户反馈和下一步时间"]
    if scenario == "incomplete":
        answer["fields"]["next_action"] = ""
    if scenario in {"incomplete", "next_rejected"}:
        answer["quality_review"]["next_action"]["passed"] = False
        answer["quality_review"]["next_action"]["time_found"] = False
    if scenario == "low_score":
        answer["quality_review"]["follow_up_score"] = 60
    return answer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "normal",
        "first_visit",
        "first_missing",
        "incomplete",
        "low_score",
        "next_rejected",
        "review",
        "off",
        "blocked",
        "timeout",
        "bad_score",
        "boolean_score",
        "missing_review",
        "review_rewritten",
        "both_failed",
    ],
)
async def test_visit_handler_preserves_review_contract_and_fallback(monkeypatch, scenario):
    config = configured(enabled=scenario != "off", blocked=scenario in {"blocked", "both_failed"})
    answer = answer_for(scenario)
    text = review_text(answer["fields"]) if scenario in {"review", "review_rewritten"} else SOURCE_TEXT
    payload = deepcopy(answer)
    if scenario == "bad_score":
        payload["quality_review"]["follow_up_score"] = 101
    if scenario == "boolean_score":
        payload["quality_review"]["follow_up_score"] = True
    if scenario == "missing_review":
        payload.pop("quality_review")
    if scenario == "review_rewritten":
        payload["fields"]["follow_up_record"] = "模型改写了正在审核的正文"
    if scenario != "off":
        payload["inference_route"] = {"provider": "forged"}
    stream = (
        Chunks([], stall=True)
        if scenario == "timeout"
        else Chunks(
            [
                frame("message", answer=json.dumps(payload)),
                frame("message_end"),
            ]
        )
    )
    requests = []

    def wire(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    def runtime_factory(db, settings, **kwargs):
        runtime = filtered_facts_runtime(db, settings, **kwargs)
        runtime.client_factory = lambda c: FdeClient(c, transport=httpx.MockTransport(wire))
        runtime.observer_factory = lambda *a, **kw: SimpleNamespace(
            start=AsyncMock(return_value="attempt"),
            finish=AsyncMock(),
        )
        return runtime

    direct = SimpleNamespace(chat_json=AsyncMock(return_value=answer), close=AsyncMock())
    if scenario == "both_failed":
        direct.chat_json.side_effect = SenseAudioError("synthetic failure", retryable=True)

    def service(db, settings, *, platform):
        result = InferenceService(db, settings, platform=platform, direct_factory=lambda *a, **kw: direct)
        result.assert_actor_current = AsyncMock()
        return result

    persist = AsyncMock()
    monkeypatch.setattr(
        business,
        "load_runtime_configuration",
        AsyncMock(
            return_value=SimpleNamespace(
                settings=config,
                prompt_overrides={"visit_entry": "原拜访规则覆盖"},
            )
        ),
    )
    monkeypatch.setattr(business, "filtered_facts_runtime", runtime_factory)
    monkeypatch.setattr(business, "InferenceService", service)
    monkeypatch.setattr(business, "SenseAudioClient", lambda *a, **kw: direct)
    monkeypatch.setattr(business, "AgentRunStore", lambda *a, **kw: SimpleNamespace(persist_result=persist))
    handler = business.AgentRunHandler(None, config)
    run = RunInput("55555555-5555-4555-8555-555555555555", "conversation", text, "visit_entry", None, ACTOR)
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
    assert result == ensure_visit_result(answer, server_fields=SERVER_FIELDS)
    assert all(result["fields"][key] == value for key, value in SERVER_FIELDS.items())
    assert result["prompt_version"] == PROMPT_VERSION
    assert result["quality_review"]["grade"] == ("待完善" if scenario == "low_score" else "良好")
    if scenario == "first_visit":
        assert result["fields"]["customer_budget"] == "客户预算尚未确定"
    if scenario == "first_missing":
        assert "联系人角色" in result["missing_fields"]
    accepted = scenario in {
        "normal",
        "first_visit",
        "first_missing",
        "incomplete",
        "low_score",
        "next_rejected",
        "review",
    }
    assert direct.chat_json.await_count == (not accepted)
    if scenario == "off":
        trace = persist.call_args.kwargs["inference_trace"]
        assert trace["provider"] == "senseaudio" and trace["fallback_reason"] is None
        assert "platform_run" not in trace and not requests
    else:
        trace = persist.call_args.kwargs["inference_trace"]
        assert trace["provider"] == ("agent_platform" if accepted else "senseaudio")
        assert trace["platform_run"]["tool_authority_issued"] is False
        if scenario == "blocked":
            assert trace["fallback_reason"] == "ControlledPlatformBlock" and not requests
        else:
            query = json.loads(json.loads(requests[0].content)["query"])
            assert query["facts"] == FACTS and query["mode"] == "visit_entry" and query["user_text"] == text
            assert KEY not in json.dumps(query)
    if direct.chat_json.await_count:
        assert "原拜访规则覆盖" in direct.chat_json.call_args.kwargs["messages"][0].content
        direct.close.assert_awaited_once()
