"""Content-free report audit repair; no database, network, settings or model call."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.domain.model_contract import contract_failure_details
from sales_backend.services import work_report_providers as provider
from sales_backend.services.agent_platform.audit import InferenceAudit
from sales_backend.services.agent_platform.routing import (
    AgentRouter,
    AgentUnavailable,
    InvalidAgentResult,
    RoutingPolicy,
    WriteState,
)
from sales_backend.services.work_report_generation.content import validate_model_content

ACTOR = ActorContext(
    workspace_id="11111111-1111-4111-8111-111111111111",
    user_id="22222222-2222-4222-8222-222222222222",
    role=RoleCode.SALES,
    data_scope=DataScope.SELF,
)
SOURCE = "visit:33333333-3333-4333-8333-333333333333"


def request():
    return NS(
        sources=[NS(source_id=SOURCE, state_kind="occurred")],
        supplements=[],
        metrics=[],
        coverage=[],
        rewrite=None,
        sales_week_facts=None,
        scheme=NS(config=NS(sections=[NS(id="activity", title="Activity", enabled=True)])),
        model_dump=lambda **kwargs: {},
    )


def content():
    return {
        "title": "Report",
        "sections": [
            {
                "id": "activity",
                "title": "Activity",
                "items": [
                    {"kind": "summary", "text": "Synthetic material", "source_refs": [SOURCE], "metric_refs": []}
                ],
            }
        ],
        "missing_data": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["generated", "basic_summary", "failure", "cancelled", "validation_failure"])
async def test_validated_result_uses_existing_terminal_enum_and_preserves_fallback_trace(monkeypatch, outcome):
    trace = {
        "fallback_reason": "providers_unavailable" if outcome == "basic_summary" else None,
        "chunks_completed": 0 if outcome == "basic_summary" else 1,
        "elapsed_ms": 31381,
    }
    result = NS(
        status=outcome,
        provider="none" if outcome == "basic_summary" else "agent_platform",
        trace=NS(fallback_reason=trace["fallback_reason"], model_dump=lambda **kwargs: deepcopy(trace)),
    )
    generate = AsyncMock(return_value=result)
    if outcome == "failure":
        generate.side_effect = RuntimeError("synthetic")
    if outcome == "cancelled":
        generate.side_effect = asyncio.CancelledError()
    validate = NS(calls=0)

    def check(value, req):
        validate.calls += 1
        if outcome == "validation_failure":
            raise ValueError("synthetic")

    monkeypatch.setattr(provider, "generate_work_report", generate)
    monkeypatch.setattr(provider, "validate_generation_result", check)
    monkeypatch.setattr(
        provider,
        "load_runtime_configuration",
        AsyncMock(return_value=NS(settings=NS(llm_model="synthetic", agent_execution_policy={}))),
    )
    monkeypatch.setattr(provider, "filtered_facts_runtime", lambda *a, **k: None)
    monkeypatch.setattr(
        provider, "ReportTransports", lambda *a, **k: NS(policy=RoutingPolicy(), providers=None, reason=None)
    )
    writes = AsyncMock()
    monkeypatch.setattr(InferenceAudit, "_write", writes)
    if outcome in ("failure", "cancelled", "validation_failure"):
        error = {"failure": RuntimeError, "cancelled": asyncio.CancelledError, "validation_failure": ValueError}[
            outcome
        ]
        with pytest.raises(error):
            await provider.generate_report(
                request(), database=None, actor=ACTOR, settings=None, assert_active=AsyncMock()
            )
    else:
        assert (
            await provider.generate_report(
                request(), database=None, actor=ACTOR, settings=None, assert_active=AsyncMock()
            )
            is result
        )
    args = writes.await_args.args
    assert "security.finish_inference_operation" in args[0]
    assert args[2] == (
        "cancelled"
        if outcome == "cancelled"
        else "failed"
        if outcome in ("failure", "validation_failure")
        else "accepted"
    )
    if outcome in ("generated", "basic_summary"):
        assert validate.calls == 1 and args[3] == trace and args[5] is None
        assert result.status == outcome


@pytest.mark.parametrize(
    "case,code",
    [
        ("schema", "work_report.content_schema"),
        ("rules", "work_report.content_rules"),
        ("gaps", "work_report.coverage_gaps"),
        ("empty", "work_report.empty_or_oversized"),
        ("batch", "work_report.source_outside_batch"),
    ],
)
def test_existing_validation_still_rejects_with_fixed_noncontent_codes(case, code):
    body = content()
    allowed = {SOURCE}
    if case == "schema":
        body["sections"] = "PRIVATE_MODEL_TEXT"
    elif case == "rules":
        body["sections"][0]["title"] = "PRIVATE_MODEL_TEXT"
    elif case == "gaps":
        body["missing_data"] = ["PRIVATE_MODEL_TEXT"]
    elif case == "empty":
        body["sections"][0]["items"] = []
    else:
        allowed = set()
    with pytest.raises(InvalidAgentResult) as caught:
        validate_model_content(body, request(), allowed_refs=allowed)
    assert contract_failure_details(caught.value) == {"contract_code": code}
    assert str(caught.value) == "work_report.invalid_content"
    assert str(caught.value.__cause__) == code and caught.value.__cause__.__cause__ is None
    assert "PRIVATE_MODEL_TEXT" not in str(caught.value.__cause__)


def test_valid_content_is_identical_after_diagnostic_only_change():
    body = content()
    assert validate_model_content(body, request(), allowed_refs={SOURCE}) == body


@pytest.mark.asyncio
async def test_original_router_persists_contract_code_without_model_content():
    events = []

    async def invoke():
        return {"title": "PRIVATE_MODEL_TEXT"}

    async def seal():
        return WriteState.NONE

    def observe(event, provider, **details):
        events.append({"event": event, "provider": provider, **details})

    with pytest.raises(AgentUnavailable):
        await AgentRouter().evaluate(
            policy=RoutingPolicy(platform_enabled=False),
            platform=invoke,
            direct=invoke,
            validate=lambda value: validate_model_content(value, request(), allowed_refs={SOURCE}),
            write_state=seal,
            observe=observe,
        )
    failed = [e for e in events if e["event"] == "route_failed"]
    assert len(failed) == 1 and failed[0]["contract_code"] == "work_report.content_schema"
    assert failed[0]["error_code"] == "InvalidAgentResult"
    assert "PRIVATE_MODEL_TEXT" not in str(events)
