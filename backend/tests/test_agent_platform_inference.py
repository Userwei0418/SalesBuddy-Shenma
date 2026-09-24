import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.config import get_settings
from sales_backend.domain.agent import ActorContext, ChatMessage, DataScope, RoleCode
from sales_backend.domain.customer_risk import (
    CUSTOMER_RISK_OUTPUT_CONTRACT,
    customer_risk_messages,
    validate_customer_risk_result,
)
from sales_backend.services.agent_platform.inference import InferenceService, PlatformOperation, binding_for
from sales_backend.services.agent_platform.result_contracts import valid_map_score, validate_run_result
from sales_backend.services.agent_platform.routing import (
    AgentRouter,
    InvalidAgentResult,
    ProviderUnavailable,
    ReconciliationRequired,
    RoutingPolicy,
    WriteState,
)

ACTOR = ActorContext(
    workspace_id="11111111-1111-4111-8111-111111111111",
    user_id="22222222-2222-4222-8222-222222222222",
    role=RoleCode.SALES,
    data_scope=DataScope.SELF,
)
SNAPSHOT_ID = "33333333-3333-4333-8333-333333333333"


def settings(**kwargs):
    binding = {
        ACTOR.workspace_id: {
            "chatbi": {
                "enabled": True, "agent_id": "synthetic-agent",
                "snapshot_id": SNAPSHOT_ID, "approved_snapshot_id": SNAPSHOT_ID,
            }
        }
    }
    return replace(
        get_settings(),
        senseaudio_api_key="",
        database_url="",
        agent_platform_bindings_json=json.dumps(binding),
        **kwargs,
    )


def service(platform=None):
    client = SimpleNamespace(chat_json=AsyncMock(return_value={"value": "direct"}), close=AsyncMock())
    calls = []

    def factory(*args, **kwargs):
        calls.append(kwargs)
        return client

    inference = InferenceService(None, settings(), platform=platform, direct_factory=factory)
    inference.assert_actor_current = AsyncMock()
    return inference, calls, client


async def evaluate(inference, **kwargs):
    return await inference.evaluate(
        actor=ACTOR,
        mode="chatbi",
        facts={"data_as_of": "2026-09-11T00:00:00Z"},
        messages=[],
        user_text="合成测试",
        validate=lambda value: value,
        **kwargs,
    )


def runtime(invoke, seal):
    return SimpleNamespace(operation=lambda request: PlatformOperation(invoke, seal))


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["customer_risk", None])
async def test_customer_risk_surface_adds_contract_without_changing_personal_risks_binding(surface):
    captured = []
    seal = AsyncMock(return_value=WriteState.NONE)

    def operation(request):
        captured.append(request)
        return PlatformOperation(AsyncMock(return_value={"value": "platform"}), seal)

    inference, calls, _ = service(SimpleNamespace(operation=operation))
    config = {ACTOR.workspace_id: {"personal_risks": {
        "enabled": True, "agent_id": "existing-risk-agent", "execution_mode": "filtered_facts",
        "expected_snapshot_id": SNAPSHOT_ID,
    }}}
    inference.settings = replace(inference.settings, agent_platform_bindings_json=json.dumps(config))
    facts = {"visits": [], "coverage": {"complete": True}}
    messages = [ChatMessage(**message) for message in customer_risk_messages(facts)]
    await inference.evaluate(actor=ACTOR, mode="personal_risks", surface=surface, facts=facts,
                             messages=messages, user_text="客户风险评估", validate=lambda value: value)
    request = captured[0]
    assert request.capability == "personal_risks" and request.binding.agent_id == "existing-risk-agent"
    assert request.input["mode"] == "personal_risks" and request.input["facts"] == facts
    if surface:
        assert request.input["surface"] == "customer_risk"
        assert request.input["backend_prompt"] == messages[0].content
        assert request.input["output_contract"] == CUSTOMER_RISK_OUTPUT_CONTRACT
    else:
        assert not {"surface", "backend_prompt", "output_contract"}.intersection(request.input)
    assert not calls


@pytest.mark.asyncio
async def test_customer_risk_legacy_empty_result_falls_back_to_the_same_strict_contract():
    inference, calls, client = service(runtime(
        AsyncMock(return_value={"risks": []}), AsyncMock(return_value=WriteState.NONE),
    ))
    binding = json.loads(inference.settings.agent_platform_bindings_json)[ACTOR.workspace_id]["chatbi"]
    inference.settings = replace(inference.settings, agent_platform_bindings_json=json.dumps({
        ACTOR.workspace_id: {"personal_risks": binding},
    }))
    facts = {"visits": [{"id": "v1", "content": "双方已确认试点安排并指定负责人。"}], "coverage": {"complete": True}}
    expected = {
        "outcome": "no_risk_identified", "reviewed_visit_ids": ["v1"], "risks": [], "reason": "已审查输入拜访。",
    }
    client.chat_json.return_value = expected
    messages = [ChatMessage(**message) for message in customer_risk_messages(facts)]
    result = await inference.evaluate(
        actor=ACTOR, mode="personal_risks", surface="customer_risk", facts=facts,
        messages=messages, user_text="客户风险评估", validate=lambda value: validate_customer_risk_result(value, facts),
    )
    assert result.payload == expected and result.trace["provider"] == "senseaudio"
    assert result.trace["fallback_reason"] == "InvalidAgentResult" and len(calls) == 1
    assert client.chat_json.call_args.kwargs["messages"] == messages


@pytest.mark.asyncio
async def test_customer_risk_surface_rejects_other_capabilities_before_dispatch():
    inference, calls, _ = service()
    with pytest.raises(PermissionError, match="personal_risks"):
        await evaluate(inference, surface="customer_risk")
    inference.assert_actor_current.assert_not_awaited()
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [RoleCode.FDE, RoleCode.FDE_LEAD, RoleCode.ADMINISTRATOR])
async def test_customer_risk_surface_checks_configured_permission_before_dispatch(role):
    inference, calls, _ = service()
    inference.assert_actor_current.side_effect=PermissionError("risk.auto_review revoked")
    with pytest.raises(PermissionError):
        await inference.evaluate(
            actor=ACTOR.model_copy(update={"role": role}), mode="personal_risks", surface="customer_risk",
            facts={"visits": []}, messages=[ChatMessage(role="system", content="客户风险评估")],
            user_text="评估", validate=lambda value: value,
        )
    assert inference.assert_actor_current.await_args.args[1] == "risk.auto_review"
    assert not calls


def test_only_explicit_workspace_capability_and_approved_snapshot_enable_platform():
    raw = settings().agent_platform_bindings_json
    assert binding_for(raw, ACTOR.workspace_id, "chatbi").snapshot_id == SNAPSHOT_ID
    assert binding_for(raw, "different-workspace", "chatbi") is None
    assert binding_for(raw, ACTOR.workspace_id, "today_tasks") is None
    unapproved = raw.replace('"approved_snapshot_id": "3333', '"approved_snapshot_id": "4444')
    assert binding_for(unapproved, ACTOR.workspace_id, "chatbi") is None
    for broken in ("null", "[]", "broken", json.dumps({ACTOR.workspace_id: True})):
        assert binding_for(broken, ACTOR.workspace_id, "chatbi") is None


@pytest.mark.parametrize("snapshot", ["a" * 64, "v1", 1, True, None, "not-a-snapshot"])
def test_management_revision_or_non_snapshot_values_cannot_enable_runtime(snapshot):
    config = json.loads(settings().agent_platform_bindings_json)
    config[ACTOR.workspace_id]["chatbi"].update(snapshot_id=snapshot, approved_snapshot_id=snapshot)
    assert binding_for(json.dumps(config), ACTOR.workspace_id, "chatbi") is None


@pytest.mark.asyncio
async def test_legacy_revision_settings_keep_direct_path_and_never_dispatch_platform():
    inference, calls, _ = service()
    config = {ACTOR.workspace_id: {"chatbi": {
        "enabled": True, "agent_id": "synthetic-agent", "revision": "a" * 64, "approved_revision": "a" * 64,
    }}}
    inference.settings = replace(inference.settings, agent_platform_bindings_json=json.dumps(config))
    operation = AsyncMock()
    inference.platform = SimpleNamespace(operation=operation)
    result = await evaluate(inference)
    assert result.trace["provider"] == "senseaudio" and len(calls) == 1
    operation.assert_not_called()


def test_snapshot_metadata_never_appears_in_business_result():
    result = validate_run_result(
        SimpleNamespace(mode="chatbi", actor=ACTOR),
        {"summary": "合成", "metrics": [], "rows": [], "snapshot_id": SNAPSHOT_ID,
         "approved_snapshot_id": SNAPSHOT_ID, "revision": "management-hash"},
        {"data_as_of": "2026-09-11"},
    )
    assert result == {"summary": "合成", "metrics": [], "rows": [], "scope": "本人", "data_as_of": "2026-09-11"}


@pytest.mark.asyncio
async def test_missing_adapter_falls_back_without_minting_or_counting_a_platform_request():
    inference, calls, client = service()
    result = await evaluate(inference)
    assert result.payload == {"value": "direct"}
    assert result.trace["provider"] == "senseaudio"
    assert result.trace["fallback_reason"] == "ProviderUnavailable"
    assert len(calls) == 1
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_is_sealed_before_returning_and_does_not_require_direct_api_key():
    seal = AsyncMock(return_value=WriteState.NONE)
    inference, calls, _ = service(runtime(AsyncMock(return_value={"value": "platform"}), seal))
    result = await evaluate(inference)
    assert result.payload == {"value": "platform"}
    assert result.trace["model_ref"] == f"agent-platform:synthetic-agent@{SNAPSHOT_ID}"
    assert not calls
    seal.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [WriteState.COMMITTED, TimeoutError("receipt delayed")])
async def test_success_with_unresolved_receipt_neither_persists_nor_falls_back(outcome):
    seal = AsyncMock(side_effect=outcome) if isinstance(outcome, Exception) else AsyncMock(return_value=outcome)
    inference, calls, _ = service(runtime(AsyncMock(return_value={"value": "platform"}), seal))
    with pytest.raises(ReconciliationRequired):
        await evaluate(inference)
    assert not calls
    seal.assert_awaited_once()


@pytest.mark.asyncio
async def test_permission_denial_is_cleaned_up_without_provider_fallback():
    seal = AsyncMock(return_value=WriteState.NONE)
    inference, calls, _ = service(runtime(AsyncMock(side_effect=PermissionError("revoked")), seal))
    with pytest.raises(PermissionError):
        await evaluate(inference)
    assert not calls
    seal.assert_awaited_once()


@pytest.mark.asyncio
async def test_bad_platform_contract_uses_same_validator_for_direct_result():
    seal = AsyncMock(return_value=WriteState.NONE)
    inference, calls, _ = service(runtime(AsyncMock(return_value={"value": "invalid"}), seal))

    def validator(value):
        if value["value"] == "invalid":
            raise ValueError("bad business result")
        return {"validated": value["value"]}

    result = await inference.evaluate(
        actor=ACTOR, mode="chatbi", facts={}, messages=[], user_text="测试", validate=validator
    )
    assert result.payload == {"validated": "direct"} and len(calls) == 1
    seal.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_suppressing_provider_cannot_consume_fallback_budget_or_replace_result():
    release, finished = asyncio.Event(), asyncio.Event()

    async def stubborn():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await release.wait()
            finished.set()
            return {"value": "too late"}

    try:
        result = await asyncio.wait_for(
            AgentRouter().evaluate(
                policy=RoutingPolicy(
                    platform_enabled=True, total_seconds=0.3, platform_seconds=0.01, receipt_seconds=0.01
                ),
                platform=stubborn,
                direct=AsyncMock(return_value={"value": "direct"}),
                validate=lambda value: value,
                write_state=AsyncMock(return_value=WriteState.NONE),
            ),
            0.2,
        )
        assert result.payload == {"value": "direct"} and not finished.is_set()
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), 0.2)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -1, 101, True])
def test_map_score_rejects_invalid_values_instead_of_manufacturing_a_valid_score(score):
    with pytest.raises(ValueError):
        valid_map_score(score)


def test_risk_and_todo_references_cannot_escape_the_server_facts():
    with pytest.raises(InvalidAgentResult):
        validate_run_result(
            SimpleNamespace(mode="personal_risks"), {"risks": [{"source_visit_id": "foreign"}]}, {"visits": []}
        )
    with pytest.raises(InvalidAgentResult):
        validate_run_result(SimpleNamespace(mode="today_tasks"), {"ordered_items": [{"source_id": "foreign"}]}, {})


@pytest.mark.asyncio
async def test_platform_constructor_failure_before_dispatch_keeps_direct_path_available():
    def unavailable(request):
        raise ProviderUnavailable("adapter unavailable before dispatch")

    inference, calls, _ = service(SimpleNamespace(operation=unavailable))
    assert (await evaluate(inference)).trace["provider"] == "senseaudio"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_exhausted_provider_routes_do_not_requeue_billed_calls():
    from sales_backend.integrations.senseaudio import SenseAudioError

    invoked = AsyncMock(side_effect=ProviderUnavailable("platform unavailable"))
    sealed = AsyncMock(return_value=WriteState.NONE)
    inference, _, client = service(runtime(invoked, sealed))
    client.chat_json.side_effect = SenseAudioError("direct unavailable", retryable=True)
    with pytest.raises(SenseAudioError) as caught:
        await evaluate(inference)
    assert caught.value.retryable is False
    invoked.assert_awaited_once()
    client.chat_json.assert_awaited_once()
    client.close.assert_awaited_once()
