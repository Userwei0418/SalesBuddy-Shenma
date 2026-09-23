import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from sales_backend.services.agent_platform.audit import result_shape
from sales_backend.services.agent_platform.routing import AgentRouter, AgentUnavailable, RoutingPolicy, WriteState


def test_diagnostic_shape_preserves_failure_types_without_model_text():
    payload = {
        "summary": "客户秘密",
        "action_plan": [{"title": "秘密", "detail": {"secret": "密钥"}}],
        "untrusted_field": "不可落日志",
    }
    shape = result_shape(payload)
    assert shape["action_plan"]["sample_shapes"][0]["detail"] == {}
    assert not any(value in json.dumps(shape, ensure_ascii=False) for value in ["客户秘密", "密钥", "untrusted_field"])


@pytest.mark.asyncio
async def test_audit_retains_both_contract_failures_and_no_false_acceptance():
    events = []

    def observe(phase, provider, **metadata):
        events.append({"phase": phase, "provider": provider, **metadata})

    def validate(value):
        raise ValueError("sensitive invalid content")

    with pytest.raises(AgentUnavailable):
        await AgentRouter().evaluate(
            policy=RoutingPolicy(platform_enabled=True),
            platform=AsyncMock(return_value={}),
            direct=AsyncMock(return_value={}),
            validate=validate,
            write_state=AsyncMock(return_value=WriteState.NONE),
            observe=observe,
        )
    assert events[-1]["error_code"] == "InvalidAgentResult"
    assert [e["provider"] for e in events if e["phase"] == "route_failed"] == ["agent_platform", "senseaudio"]
    assert not any(e["phase"] == "contract_accepted" for e in events)
    assert "sensitive" not in json.dumps(events)


@pytest.mark.asyncio
async def test_timeout_and_fallback_have_separate_evidence_and_late_output_is_ignored():
    release, finished = asyncio.Event(), asyncio.Event()
    events = []

    def observe(phase, provider, **metadata):
        events.append({"phase": phase, "provider": provider, **metadata})

    async def late():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await release.wait()
            finished.set()
            return {"summary": "late"}

    try:
        result = await AgentRouter().evaluate(
            policy=RoutingPolicy(platform_enabled=True, platform_seconds=0.01, total_seconds=0.3, receipt_seconds=0.01),
            platform=late,
            direct=AsyncMock(return_value={"summary": "fallback"}),
            validate=lambda x: x,
            write_state=AsyncMock(return_value=WriteState.NONE),
            observe=observe,
        )
        assert result.provider == "senseaudio" and result.fallback_reason == "TimeoutError"
        release.set()
        await asyncio.wait_for(finished.wait(), 0.2)
        await asyncio.sleep(0)
        accepted = [e["provider"] for e in events if e["phase"] == "contract_accepted"]
        assert accepted == ["senseaudio"]
        assert any(e.get("error_code") == "TimeoutError" for e in events)
    finally:
        release.set()
