"""Material selection and the same attributable contract on both inference routes."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from sales_backend.domain.agent import RoleCode
from sales_backend.domain.fde_coaching import (
    TASK_LIMIT,
    TEXT_LIMIT,
    VISIT_LIMIT,
    CoachingContractError,
    coaching_inputs,
    contract_failure_details,
    model_facts,
    validate_coaching,
)
from sales_backend.integrations.supreme_fde import FdeClient
from sales_backend.services.agent_platform.audit import InferenceAudit
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import InferenceService
from sales_backend.services.agent_run import handler as business
from sales_backend.services.agent_run.models import RunInput
from tests.test_fde_facts_runtime import Chunks, frame
from tests.test_fde_profile import NOW, facts, person, visit
from tests.test_operating_report_platform import configured


def material():
    return coaching_inputs(
        [
            {
                "id": "v1",
                "interaction_at": NOW,
                "communication": "客户尚未确认验收指标",
                "next_action": "等待客户反馈",
                "project_name": "项目一",
            }
        ],
        [],
    )


def answer():
    return {
        "action_plan": [
            {
                "title": "确认验收指标",
                "detail": "与客户核对待确认的验收指标，明确下一步验证范围。",
                "source_refs": ["visit:v1"],
            }
        ]
    }


def test_coaching_is_bounded_and_does_not_expose_radar_or_infer_material_gaps():
    rows = [
        {
            "id": str(i),
            "interaction_at": NOW,
            "communication": "正" * (TEXT_LIMIT + 1),
            "next_action": "已有明确计划",
            "follow_up_score": 90,
        }
        for i in range(25)
    ]
    tasks = [
        {"id": str(i), "title": "任务", "status": "pending_execution", "due_at": None, "can_execute": True}
        for i in range(35)
    ]
    inputs = coaching_inputs(rows, tasks)
    assert len(inputs["sources"]) == VISIT_LIMIT + TASK_LIMIT
    first = inputs["sources"][0]
    assert len(first["communication"]) == TEXT_LIMIT and first["truncated"]
    assert "follow_up_score" not in first
    small = facts(coaching=inputs)
    output = model_facts(small)
    assert not {"dimensions", "evidence_coverage", "sample_count", "configuration", "scope_note"} & output.keys()
    assert output["coaching_inputs"] == inputs
    assert coaching_inputs([], [{**tasks[0], "can_execute": False}])["sources"] == []


def test_coaching_contract_allows_empty_without_summary_and_keeps_only_attributable_actions():
    loaded = facts(coaching=material())
    empty = validate_coaching({"action_plan": []}, loaded)
    assert empty["action_plan"] == [] and empty["summary"] == ""
    accepted = validate_coaching({**answer(), "overall_score": 100, "summary": "不可信评价"}, loaded)
    assert accepted["action_plan"] == answer()["action_plan"] and accepted["summary"] == ""
    assert "overall_score" not in accepted


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"summary": "任意原文不要进入日志"}, "action_plan_missing"),
        ({"action_plan": answer()["action_plan"] * 4}, "too_many_actions"),
        ({"action_plan": [{"title": [], "detail": "说明"}]}, "invalid_item"),
        ({"action_plan": [{"title": "建议", "detail": "说明", "source_refs": []}]}, "invalid_sources"),
        ({"action_plan": [{"title": "建议", "detail": "说明", "source_refs": ["未知原文"]}]}, "unknown_source"),
    ],
)
def test_invalid_material_reference_has_a_controlled_content_free_error(payload, code):
    with pytest.raises(CoachingContractError) as raised:
        validate_coaching(payload, facts(coaching=material()))
    assert str(raised.value) == "fde_coaching." + code
    assert contract_failure_details(raised.value) == {"contract_code": "fde_coaching." + code}
    assert contract_failure_details(ValueError("客户原文及任意错误")) == {}


def test_material_content_and_versions_invalidate_cache_without_changing_radar():
    actor = person()
    inputs = material()
    before = facts(actor, visits=[visit()], coaching=inputs, coaching_versions=[{"id": "v1", "version": 2}])
    for kwargs in (
        {"coaching": {**inputs, "sources": [{**inputs["sources"][0], "next_action": "新安排"}]}},
        {"coaching_versions": [{"id": "v1", "version": 3}]},
    ):
        after = facts(
            actor, visits=[visit()], **{"coaching": inputs, "coaching_versions": [{"id": "v1", "version": 2}], **kwargs}
        )
        assert before["dimensions"] == after["dimensions"]
        assert before["facts_fingerprint"] != after["facts_fingerprint"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [RoleCode.FDE, RoleCode.FDE_LEAD])
@pytest.mark.parametrize("scenario", ["empty", "valid", "missing", "foreign_source"])
async def test_handler_passes_same_trusted_coaching_to_platform_and_fallback(monkeypatch, role, scenario):
    actor = person(role)
    settings = configured(actor, agent_inference_platform_seconds=1, agent_inference_total_seconds=5)
    loaded = model_facts(facts(actor, coaching=material()))
    valid = {"action_plan": []} if scenario == "empty" else answer()
    remote = valid
    if scenario == "missing":
        remote = {"summary": "历史经营总结结构"}
    if scenario == "foreign_source":
        remote = {"action_plan": [{**answer()["action_plan"][0], "source_refs": ["task:forged"]}]}
    requests, events = [], []

    def wire(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Chunks([frame("message", answer=json.dumps(remote)), frame("message_end")]),
        )

    def runtime_factory(db, config, **kwargs):
        runtime = filtered_facts_runtime(db, config, **kwargs)
        runtime.client_factory = lambda c: FdeClient(c, transport=httpx.MockTransport(wire))
        runtime.observer_factory = lambda *a, **kw: SimpleNamespace(
            start=AsyncMock(return_value="attempt"), finish=AsyncMock()
        )
        return runtime

    direct = SimpleNamespace(chat_json=AsyncMock(return_value=valid), close=AsyncMock())

    def service(db, config, *, platform):
        result = InferenceService(db, config, platform=platform, direct_factory=lambda *a, **kw: direct)
        result.assert_actor_current = AsyncMock()
        return result

    original_event = InferenceAudit.event

    def event(self, phase, provider, **metadata):
        events.append({"phase": phase, "provider": provider, **metadata})
        original_event(self, phase, provider, **metadata)

    persist = AsyncMock()
    monkeypatch.setattr(InferenceAudit, "event", event)
    monkeypatch.setattr(InferenceAudit, "_write", AsyncMock())
    monkeypatch.setattr(
        business,
        "load_runtime_configuration",
        AsyncMock(
            return_value=SimpleNamespace(settings=settings, prompt_overrides={"operating_report": "已有公司规则"})
        ),
    )
    monkeypatch.setattr(business, "filtered_facts_runtime", runtime_factory)
    monkeypatch.setattr(business, "InferenceService", service)
    monkeypatch.setattr(business, "AgentRunStore", lambda *a, **kw: SimpleNamespace(persist_result=persist))
    handler = business.AgentRunHandler(None, settings)
    run = RunInput(
        "55555555-5555-4555-8555-555555555555",
        "conversation",
        "范围仅本人",
        "operating_report",
        None,
        actor,
        surface="fde_profile",
        profile_days=30,
    )
    handler._load_and_start = AsyncMock(return_value=run)
    handler.facts_loader.load = AsyncMock(return_value=loaded)
    await handler.handle(run.run_id, actor)
    query = json.loads(json.loads(requests[0].content)["query"])
    assert query["facts"] == loaded and query["surface"] == "fde_profile"
    assert query["output_contract"]["version"] == "fde.coaching.v1"
    assert "允许零至三条" in query["backend_prompt"]
    assert not {"dimensions", "sample_count", "evidence_coverage"} & query["facts"].keys()
    assert persist.await_count == 1
    assert persist.call_args.args[1]["action_plan"] == valid["action_plan"]
    fallback = scenario in {"missing", "foreign_source"}
    assert direct.chat_json.await_count == int(fallback)
    if fallback:
        messages = direct.chat_json.call_args.kwargs["messages"]
        assert messages[0].content == query["backend_prompt"]
        assert json.loads(messages[1].content.split("facts：", 1)[1]) == query["facts"]
        failure = next(e for e in events if e["phase"] == "route_failed")
        assert failure["contract_code"] == "fde_coaching." + (
            "action_plan_missing" if scenario == "missing" else "unknown_source"
        )
        assert "forged" not in json.dumps(events)
