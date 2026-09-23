"""Real-pilot regression: integer metrics must not reject both successful providers."""

from types import SimpleNamespace

import pytest

from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.services.agent_platform.result_contracts import validate_run_result
from sales_backend.services.agent_platform.routing import AgentRouter, InvalidAgentResult, RoutingPolicy, WriteState

RUN = SimpleNamespace(
    mode="chatbi",
    actor=ActorContext(
        workspace_id="11111111-1111-4111-8111-111111111111",
        user_id="22222222-2222-4222-8222-222222222222",
        role=RoleCode.SALES,
        data_scope=DataScope.SELF,
    ),
)
FACTS = {"data_as_of": "2026-09-12T00:00:00Z"}


def answer(value):
    return {"summary": "合成答案", "metrics": [{"label": "未完成任务", "value": value}], "rows": []}


@pytest.mark.parametrize("value,expected", [(0, "0"), (25, "25"), (2500.25, "2500.25"), ("25项", "25项")])
def test_existing_number_or_string_metric_is_compatible(value, expected):
    original = answer(value)
    result = validate_run_result(RUN, original, FACTS)
    assert result["metrics"][0]["value"] == expected
    assert original["metrics"][0]["value"] == value


@pytest.mark.parametrize("value", [True, None, {}, [], float("inf"), float("nan")])
def test_invalid_metric_value_is_still_rejected(value):
    with pytest.raises(InvalidAgentResult):
        validate_run_result(RUN, answer(value), FACTS)


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_enabled", [True, False])
async def test_shared_router_accepts_numeric_result_on_each_provider(platform_enabled):
    async def numeric():
        return answer(25)

    async def no_writes():
        return WriteState.NONE

    result = await AgentRouter().evaluate(
        policy=RoutingPolicy(platform_enabled=platform_enabled),
        platform=numeric,
        direct=numeric,
        validate=lambda result: validate_run_result(RUN, result, FACTS),
        write_state=no_writes,
    )
    assert result.provider == ("agent_platform" if platform_enabled else "senseaudio")
    assert result.payload["metrics"][0]["value"] == "25"
