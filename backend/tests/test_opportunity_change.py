import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.domain.opportunity_change import rule_assessment, validate_assessment
from sales_backend.services.agent_platform.inference import InferenceService, PlatformOperation
from sales_backend.services.agent_platform.routing import ProviderUnavailable, WriteState
from tests.test_agent_platform_inference import ACTOR, SNAPSHOT_ID, settings

FACTS = {"evidence_refs": ["change", "visits:v1"], "change": {"fallback": {
    "tone": "green", "title": "商机有进展",
}}}


@pytest.mark.parametrize("color", ["green", "yellow", "red", "gray"])
def test_valid_colors_and_evidence(color):
    result = {"color": color, "summary": "阶段推进但预算缩减，需确认范围。", "evidence_refs": ["change"]}
    assert validate_assessment(result, FACTS) == result


@pytest.mark.parametrize("patch", [
    {"color": "blue"}, {"color": "#ff0000"}, {"summary": " "}, {"evidence_refs": ["visits:other"]},
    {"suggestions": []}, {"evidence_refs": []},
])
def test_invalid_color_fabricated_evidence_and_old_contract_rejected(patch):
    with pytest.raises(ValueError):
        validate_assessment({"color": "green", "summary": "有进展", "evidence_refs": ["change"], **patch}, FACTS)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["success", "unavailable", "invalid", "timeout", "disabled"])
async def test_agent_first_original_rules_fallback_never_calls_another_model(scenario):
    binding = {ACTOR.workspace_id: {"opportunity_draft": {
        "enabled": scenario != "disabled", "agent_id": "candidate-agent", "execution_mode": "filtered_facts",
        "expected_snapshot_id": SNAPSHOT_ID,
    }}}
    binding[ACTOR.workspace_id]["opportunity_advice"] = {
        "enabled": True, "agent_id": "wrong-advice-agent", "execution_mode": "filtered_facts",
        "expected_snapshot_id": SNAPSHOT_ID,
    }
    config = replace(settings(), agent_platform_bindings_json=json.dumps(binding),
                     agent_inference_platform_seconds=0.02, agent_inference_total_seconds=2.1)
    calls = []

    async def invoke():
        calls.append("platform")
        if scenario == "unavailable":
            raise ProviderUnavailable("synthetic")
        if scenario == "timeout":
            await asyncio.sleep(1)
        if scenario == "invalid":
            return {"summary": "old advice contract", "suggestions": []}
        return {"color": "yellow", "summary": "阶段推进但预算缩减", "evidence_refs": ["change"]}

    def operation(request):
        assert request.capability == request.input["mode"] == "opportunity_draft"
        assert "backend_prompt" in request.input
        assert request.binding.agent_id == "candidate-agent"
        return PlatformOperation(invoke, AsyncMock(return_value=WriteState.NONE))

    def no_model(*args, **kwargs):
        pytest.fail("rule fallback must never invoke the original model")

    inference = InferenceService(None, config, platform=SimpleNamespace(operation=operation), direct_factory=no_model)
    inference.assert_actor_current = AsyncMock()
    from sales_backend.domain.opportunity_change import assessment_messages

    result = await inference.evaluate(actor=ACTOR, mode="opportunity_change", facts=FACTS,
        messages=assessment_messages(FACTS), user_text="synthetic",
        validate=lambda value: validate_assessment(value, FACTS),
        rule_fallback=lambda: rule_assessment(FACTS))
    if scenario == "success":
        assert result.payload["color"] == "yellow"  # Agent overrides a green legacy rule.
        assert result.trace["provider"] == "agent_platform"
    else:
        assert result.payload == rule_assessment(FACTS)
        assert result.trace["provider"] == "rules"
        assert result.trace["fallback_reason"] == {
            "unavailable": "ProviderUnavailable", "invalid": "InvalidAgentResult", "timeout": "TimeoutError",
            "disabled": "PlatformNotSelected",
        }[scenario]
    assert calls == ([] if scenario == "disabled" else ["platform"])
