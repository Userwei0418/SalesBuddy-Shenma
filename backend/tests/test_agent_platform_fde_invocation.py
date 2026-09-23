import asyncio
import json
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock

import httpx
import pytest

from sales_backend.contracts.visit_flow import canonical_fields, validate_stage_result
from sales_backend.domain.company_rules import VisitAdmissionPolicy
from sales_backend.integrations.supreme_fde import FdeClient, FdeConfig
from sales_backend.services.agent_platform.fde_invocation import FdeJsonInvocation
from sales_backend.services.agent_platform.routing import (
    AgentRouter,
    InvalidAgentResult,
    ProviderUnavailable,
    ReconciliationRequired,
    RoutingPolicy,
    WriteState,
)


def fixture(handler):
    return FdeClient(
        FdeConfig("https://platform.invalid/v1", "app-synthetic-fixture", "agent_final"),
        transport=httpx.MockTransport(handler),
    )


def success(answer):
    ids = {"task_id": "task-1", "message_id": "message-1", "conversation_id": "conversation-1"}
    wire = "".join(
        "data: " + json.dumps(event) + "\n\n"
        for event in [{"event": "message", "answer": answer, **ids}, {"event": "message_end", **ids}]
    )
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=wire)


@pytest.mark.asyncio
async def test_wire_success_uses_fresh_conversation_and_cannot_be_replayed():
    requests = []

    def handle(request):
        requests.append(request)
        return success('{"value":"platform"}')

    async with fixture(handle) as client:
        invocation = FdeJsonInvocation(client, query="{}", user="crm:synthetic")
        assert await invocation() == {"value": "platform"}
        assert invocation.ids.task_id == "task-1"
        with pytest.raises(RuntimeError, match="cannot be replayed"):
            await invocation()
        with pytest.raises(FrozenInstanceError):
            invocation.user = "other"
    assert len(requests) == 1 and json.loads(requests[0].content)["conversation_id"] == ""


@pytest.mark.asyncio
async def test_real_client_http_error_maps_to_router_only_after_receipt_check():
    order = []

    def handle(request):
        order.append("fde_http")
        return httpx.Response(401, json={"message": "secret upstream body"})

    async def sealed():
        order.append("sealed")
        return WriteState.NONE

    async def direct():
        order.append("original_ai")
        return {"value": "original"}

    async with fixture(handle) as client:
        invocation = FdeJsonInvocation(client, query="{}", user="crm:synthetic")
        result = await AgentRouter().evaluate(
            policy=RoutingPolicy(platform_enabled=True),
            platform=invocation,
            direct=direct,
            validate=lambda value: value,
            write_state=sealed,
        )
    assert result.provider == "senseaudio" and result.payload == {"value": "original"}
    assert result.fallback_reason == "ProviderUnavailable"
    assert invocation.error_code == "http_error" and invocation.http_status == 401
    assert order == ["fde_http", "sealed", "original_ai"]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [WriteState.UNKNOWN, WriteState.COMMITTED])
async def test_wire_failure_does_not_fallback_when_receipt_is_not_empty(state):
    direct = AsyncMock()
    async with fixture(lambda request: httpx.Response(503)) as client:
        with pytest.raises(ReconciliationRequired):
            await AgentRouter().evaluate(
                policy=RoutingPolicy(platform_enabled=True),
                platform=FdeJsonInvocation(client, query="{}", user="crm:synthetic"),
                direct=direct,
                validate=lambda value: value,
                write_state=AsyncMock(return_value=state),
            )
    direct.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["not JSON", "[]", '{"value":NaN}'])
async def test_invalid_completed_output_maps_to_shared_contract_error(answer):
    async with fixture(lambda request: success(answer)) as client:
        invocation = FdeJsonInvocation(client, query="{}", user="crm:synthetic")
        with pytest.raises(InvalidAgentResult):
            await invocation()
        assert invocation.ids.task_id == "task-1"
        assert invocation.error_code in {"fde_invalid_json", "fde_expected_json_object"}


@pytest.mark.asyncio
@pytest.mark.parametrize("score,passed", [(45, False), (55, False), (60, True), (85, True)])
async def test_fenced_quality_result_is_accepted_without_rescoring_or_fallback(score, passed):
    # Regression for a completed platform answer wrapped in one JSON code block.
    # A valid negative assessment must reach the user just like a positive one.
    fields = canonical_fields({"follow_up_record": "客户确认还缺资料。", "next_action": "内部讨论"})
    facts = {"visit_stage": "quality", "fields": fields, "summary": "待补资料",
             "company_policy": {"definition": VisitAdmissionPolicy().model_dump()}}
    quality = {"follow_up_score": score, "suggestions": ["补齐明确日期"],
               "next_action": {"passed": passed, "time_found": passed,
                               "goal_or_plan_found": True, "suggestions": []}}
    payload = {"fields": fields, "summary": facts["summary"], "quality_review": quality}
    direct = AsyncMock()
    events = []
    async with fixture(lambda request: success("```json\n" + json.dumps(payload) + "\n```")) as client:
        result = await AgentRouter().evaluate(
            policy=RoutingPolicy(platform_enabled=True),
            platform=FdeJsonInvocation(client, query="{}", user="crm:synthetic"),
            direct=direct, validate=lambda value: validate_stage_result(value, facts),
            write_state=AsyncMock(return_value=WriteState.NONE),
            observe=lambda phase, provider, **details: events.append((phase, provider, details)),
        )
    assert result.provider == "agent_platform" and result.fallback_reason is None
    assert result.payload["quality_review"]["follow_up_score"] == score
    assert result.payload["quality_review"]["next_action"]["passed"] is passed
    assert result.payload["fields"] == fields
    assert result.payload["summary"] == facts["summary"]
    assert any(phase == "contract_accepted" and provider == "agent_platform" for phase, provider, _ in events)
    direct.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("answer,code", [("```json\n{broken}\n```", "fde_invalid_json"),
                                       ("```json\n[]\n```", "fde_expected_json_object")])
async def test_malformed_output_retains_safe_reason_in_fallback_audit(answer, code):
    events = []
    async with fixture(lambda request: success(answer)) as client:
        invocation = FdeJsonInvocation(client, query="{}", user="crm:synthetic")
        result = await AgentRouter().evaluate(
            policy=RoutingPolicy(platform_enabled=True), platform=invocation,
            direct=AsyncMock(return_value={"accepted": True}), validate=lambda value: value,
            write_state=AsyncMock(return_value=WriteState.NONE),
            observe=lambda phase, provider, **details: events.append({"phase": phase, **details}),
        )
    assert result.provider == "senseaudio"
    assert invocation.error_code == code
    rejected = next(event for event in events if event["phase"] == "route_failed")
    assert rejected["contract_code"] == code
    assert "broken" not in json.dumps(events)


@pytest.mark.asyncio
async def test_cancel_is_not_reclassified_as_retryable_provider_error():
    ready = asyncio.Event()

    async def handle(request):
        ready.set()
        await asyncio.Event().wait()

    async with fixture(handle) as client:
        invocation = FdeJsonInvocation(client, query="{}", user="crm:synthetic")
        task = asyncio.create_task(invocation())
        await asyncio.wait_for(ready.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await invocation.stop() is None  # Unknown ID is not a no-write proof.


@pytest.mark.asyncio
async def test_stop_uses_ids_captured_before_failure_and_the_original_user():
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("/stop"):
            return httpx.Response(200, json={"result": "success"})
        # A real timeout/stream error may still have delivered these IDs.
        wire = (
            'data: {"event":"error","task_id":"task-1","message_id":"message-1","conversation_id":"conversation-1"}\n\n'
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=wire)

    async with fixture(handle) as client:
        invocation = FdeJsonInvocation(client, query="{}", user="crm:original")
        with pytest.raises(ProviderUnavailable):
            await invocation()
        assert (await invocation.stop()).acknowledged
    assert json.loads(requests[1].content) == {"user": "crm:original"}
    assert requests[1].url.path == "/v1/chat-messages/task-1/stop"
