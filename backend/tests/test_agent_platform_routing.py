import asyncio
from unittest.mock import AsyncMock

import pytest

from sales_backend.services.agent_platform.routing import (
    AgentRouter,
    AgentUnavailable,
    InvalidAgentResult,
    ProviderUnavailable,
    ReconciliationRequired,
    RoutingPolicy,
    WriteState,
)


def validate(payload):
    if payload.get("action") not in {"none", "create", "update"}:
        raise InvalidAgentResult("unknown opportunity action")
    return payload


async def run(platform, direct, *, state=WriteState.NONE, policy=None):
    return await AgentRouter().evaluate(
        policy=policy or RoutingPolicy(platform_enabled=True),
        platform=platform,
        direct=direct,
        validate=validate,
        write_state=AsyncMock(return_value=state),
    )


@pytest.mark.asyncio
async def test_platform_payload_is_preserved_without_calling_direct_provider():
    direct = AsyncMock()
    payload = {"action": "none", "summary": "本次不关联商机"}
    result = await run(AsyncMock(return_value=payload), direct)
    assert result.payload == payload
    assert result.provider == "agent_platform"
    direct.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_platform_keeps_original_route_and_contract():
    platform = AsyncMock()
    result = await run(platform, AsyncMock(return_value={"action": "none"}), policy=RoutingPolicy())
    assert result.payload == {"action": "none"}
    assert result.fallback_reason is None
    platform.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ProviderUnavailable("503"), InvalidAgentResult("bad schema")])
async def test_safe_fallback_uses_same_validator_and_returns_only_original_business_fields(error):
    direct = AsyncMock(return_value={"action": "update", "opportunity_id": "known-id"})
    result = await run(AsyncMock(side_effect=error), direct)
    assert result.payload == {"action": "update", "opportunity_id": "known-id"}
    assert result.fallback_reason == type(error).__name__
    direct.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_platform_result_is_rejected_before_fallback():
    result = await run(AsyncMock(return_value={"action": "delete"}), AsyncMock(return_value={"action": "none"}))
    assert result.provider == "senseaudio"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [WriteState.DISPATCHED, WriteState.COMMITTED, WriteState.UNKNOWN])
async def test_fallback_cannot_replay_a_possibly_written_operation(state):
    direct = AsyncMock()
    with pytest.raises(ReconciliationRequired):
        await run(AsyncMock(side_effect=ProviderUnavailable("lost response")), direct, state=state)
    direct.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorization_denial_is_not_bypassed_by_old_provider():
    direct = AsyncMock()
    with pytest.raises(PermissionError):
        await run(AsyncMock(side_effect=PermissionError("role no longer allowed")), direct)
    direct.assert_not_awaited()


@pytest.mark.asyncio
async def test_both_providers_fail_without_synthesizing_a_success_result():
    with pytest.raises(AgentUnavailable):
        await run(AsyncMock(side_effect=ProviderUnavailable()), AsyncMock(return_value={"action": "delete"}))


@pytest.mark.asyncio
async def test_platform_timeout_reserves_time_for_direct_provider():
    async def slow():
        await asyncio.sleep(1)
        return {"action": "none"}

    result = await run(
        slow,
        AsyncMock(return_value={"action": "none"}),
        policy=RoutingPolicy(platform_enabled=True, total_seconds=0.3, platform_seconds=0.01, receipt_seconds=0.01),
    )
    assert result.fallback_reason == "TimeoutError"


@pytest.mark.asyncio
async def test_failed_receipt_read_withholds_fallback():
    direct = AsyncMock()
    with pytest.raises(ReconciliationRequired):
        await AgentRouter().evaluate(
            policy=RoutingPolicy(platform_enabled=True),
            platform=AsyncMock(side_effect=ProviderUnavailable()),
            direct=direct,
            validate=validate,
            write_state=AsyncMock(side_effect=OSError("receipt unavailable")),
        )
    direct.assert_not_awaited()


def test_policy_cannot_spend_the_entire_budget_on_the_platform():
    with pytest.raises(ValueError):
        RoutingPolicy(total_seconds=10, platform_seconds=9, receipt_seconds=2)
