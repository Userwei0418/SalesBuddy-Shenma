"""Prompt wiring only: mocked providers cannot prove semantic fidelity."""

from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from sales_backend.services.agent_platform.routing import ProviderUnavailable, RoutingPolicy, WriteState
from sales_backend.services.work_report_generation import GenerationProviders, generate_work_report
from sales_backend.services.work_report_generation.prompts import SYSTEM_PROMPT
from sales_backend.services.work_report_generation.service import PROMPT_VERSION
from tests.test_work_report_metric_spacing import content, request

VERSION = "work_report.sales_week.v2_20261010"
SYNTHETIC = "【完全合成验收记录】本次目标是验证上传流程。我们已说明步骤，结果尚未确认。"
COMPLETED = "已完成验证，结果符合要求。"


def source_request(batched):
    req = request()
    req.sources[0].text = SYNTHETIC + ("原始材料。" * 2500 if batched else "")
    second = req.sources[0].model_copy(deep=True)
    second.record_id = UUID(int=4)
    second.source_id = "visit:" + str(second.record_id)
    second.text = COMPLETED + ("原始材料。" * 2500 if batched else "")
    req.sources.append(second)
    req.coverage[0].total = req.coverage[0].included = 2
    return req


def fixed_response(envelope, *, oversized=False):
    body = content("Synthetic provider response", refs=[])
    items = body["sections"][0]["items"]
    if envelope["generation"]["phase"] == "combine":
        items[:] = [deepcopy(item) for summary in envelope["input"]["summaries"]
                    for section in summary["sections"] for item in section["items"]]
    else:
        items[0]["kind"] = "summary"
        items[0]["source_refs"] = [row["source_id"] for row in envelope["input"]["sources"]]
        if oversized:
            limit = min(12000, envelope["input"]["output_text_limit"])
            items[0]["text"] = "x" * (limit + 1)
    return body


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["platform", "direct", "fallback"])
@pytest.mark.parametrize("batched", [False, True])
async def test_shared_prompt_version_and_source_scope_reach_each_actual_route_and_phase(route, batched):
    req = source_request(batched)
    original = req.model_dump(mode="json")
    observed = []

    async def platform(envelope):
        observed.append(("platform", deepcopy(envelope)))
        if route == "fallback":
            raise ProviderUnavailable("synthetic unavailable")
        return fixed_response(envelope)

    async def direct(envelope):
        observed.append(("direct", deepcopy(envelope)))
        return fixed_response(envelope)

    seal = AsyncMock(return_value=WriteState.NONE)
    result = await generate_work_report(
        req,
        providers=GenerationProviders(direct=direct, platform=platform, seal_platform=seal),
        assert_active=AsyncMock(),
        routing_policy=RoutingPolicy(platform_enabled=route != "direct", total_seconds=10, platform_seconds=2),
    )
    phases = ["summarize", "summarize", "combine"] if batched else ["compose"]
    selected = "platform" if route == "platform" else "direct"
    selected_envelopes = [envelope for provider, envelope in observed if provider == selected]
    assert result.status == "generated"
    assert result.provider == ("agent_platform" if route == "platform" else "direct")
    assert result.trace.prompt_version == PROMPT_VERSION == VERSION
    assert result.trace.chunks_completed == result.trace.chunks_total == len(phases)
    assert [envelope["generation"]["phase"] for envelope in selected_envelopes] == phases
    assert req.model_dump(mode="json") == original
    assert seal.await_count == (0 if route == "direct" else len(phases))
    for _, envelope in observed:
        assert envelope["prompt_version"] == VERSION
        assert envelope["system_prompt"] == SYSTEM_PROMPT
        assert "source.state_kind=occurred只说明来源记录的发生口径" in envelope["system_prompt"]
        assert "原文明示已完成的动作也\n不能一律降级" in envelope["system_prompt"]
        assert "合成、演示、假设等性质是事实限定" in envelope["system_prompt"]
        assert envelope["input"]["output_text_limit"] == (
            6000 if envelope["generation"]["phase"] == "summarize" else 64000
        )
        for source in envelope["input"]["sources"]:
            frozen = next(row for row in original["sources"] if row["source_id"] == source["source_id"])
            if envelope["generation"]["phase"] == "combine":
                assert source == {key: value for key, value in frozen.items() if key != "text"}
                assert envelope["input"]["summaries"]  # Existing provenance index is not expanded raw text.
            else:
                assert source == frozen  # Goal, completed claim and nature all reach the model unchanged.
    if route == "fallback":
        assert len(observed) == 2 * len(phases)
        for index in range(0, len(observed), 2):
            assert observed[index][1] == observed[index + 1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize("batched", [False, True])
async def test_prompt_update_keeps_item_and_batch_length_rejection(batched):
    received = []

    async def oversized(envelope):
        received.append(deepcopy(envelope))
        return fixed_response(envelope, oversized=True)

    result = await generate_work_report(
        source_request(batched), providers=GenerationProviders(direct=oversized),
        assert_active=AsyncMock(),
        routing_policy=RoutingPolicy(platform_enabled=False, total_seconds=10, platform_seconds=2),
    )
    assert len(received) == 1  # Rejected output does not enter a combine call.
    assert result.status == "basic_summary" and result.provider == "none"
    assert result.trace.prompt_version == VERSION
    assert result.trace.chunks_completed == 0
