"""Fixed syntax diagnostics through the real router/audit; never model text."""

import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from sales_backend.domain.model_contract import contract_failure_details
from sales_backend.services.agent_platform.audit import InferenceAudit
from sales_backend.services.agent_platform.routing import (
    AgentRouter,
    AgentUnavailable,
    InvalidAgentResult,
    RoutingPolicy,
)
from sales_backend.services.work_report_generation.content import validate_model_content
from tests.test_work_report_audit_hotfix import ACTOR
from tests.test_work_report_metric_spacing import SOURCE, TOKEN, content, request, validate

CASES = [
    (".5 PRIVATE_BODY", "missing_before_punctuation"),
    (":PRIVATE_BODY", "missing_before_punctuation"),
    (",000 PRIVATE_BODY", "missing_before_punctuation"),
    (TOKEN + " 次。", "missing_before_metric"),
    ("\n次", "nonhorizontal_separator"),
    (" \t\u2028", "nonhorizontal_separator"),
    ("(次)", "wrapped_exact_unit"),
    ("（ 次 ）", "wrapped_exact_unit"),
    ("[次]", "wrapped_exact_unit"),
    ("【 次 】", "wrapped_exact_unit"),
    ("**次**", "markdown_exact_unit"),
    ("__ 次 __", "markdown_exact_unit"),
    ("*次*", "markdown_exact_unit"),
    ("_次_", "markdown_exact_unit"),
    ("`次`", "markdown_exact_unit"),
    ("-100次", "numeric_prefix"),
    ("+1次", "numeric_prefix"),
    ("100次", "numeric_prefix"),
    ("百次", "scale_prefix"),
    (" 万次", "scale_prefix"),
    ("一次PRIVATE_BODY", "mismatched_suffix"),
    (" 件", "mismatched_suffix"),
    ("（件）", "mismatched_suffix"),
    ("**件**", "mismatched_suffix"),
    ("（次PRIVATE_BODY）", "mismatched_suffix"),
    ("**次PRIVATE_BODY**", "mismatched_suffix"),
    ("\u200b次", "mismatched_suffix"),
]


@pytest.mark.parametrize("suffix,reason", CASES)
def test_fixed_local_syntax_codes_keep_every_case_rejected(suffix, reason):
    raw = content(TOKEN + suffix)
    original = deepcopy(raw)
    with pytest.raises(InvalidAgentResult) as failure:
        validate(raw, request())
    assert contract_failure_details(failure.value) == {"contract_code": "work_report.metric_unit." + reason}
    assert raw == original
    assert str(failure.value) == "work_report.invalid_content"
    assert "PRIVATE_BODY" not in str(failure.value.__cause__)
    assert failure.value.__cause__.__cause__ is None


def test_first_failed_occurrence_does_not_report_a_later_valid_or_failed_token():
    raw = content(TOKEN + "次；" + TOKEN + "（次）；" + TOKEN + "百次")
    with pytest.raises(InvalidAgentResult) as failure:
        validate(raw, request())
    assert contract_failure_details(failure.value) == {"contract_code": "work_report.metric_unit.wrapped_exact_unit"}


def test_display_includes_unit_branch_keeps_original_token_only_contract():
    req = request(display="1次")
    raw = content(TOKEN + "。")
    assert validate(raw, req) == raw


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix,reason",
    [
        (".PRIVATE_BODY", "missing_before_punctuation"),
        ("\n次 PRIVATE_BODY", "nonhorizontal_separator"),
        ("（次） PRIVATE_BODY", "wrapped_exact_unit"),
        ("**次** PRIVATE_BODY", "markdown_exact_unit"),
        ("-100次 PRIVATE_BODY", "numeric_prefix"),
        ("百次 PRIVATE_BODY", "scale_prefix"),
        (" PRIVATE_BODY", "mismatched_suffix"),
    ],
)
async def test_original_router_and_audit_finish_save_only_fixed_diagnostic(suffix, reason):
    audit = InferenceAudit(None, ACTOR, "synthetic-operation", mode="work_report", run_id=None, facts={}, config={})
    audit._write = AsyncMock()
    invoke = AsyncMock(return_value=content(TOKEN + suffix))
    with pytest.raises(AgentUnavailable):
        await AgentRouter().evaluate(
            policy=RoutingPolicy(platform_enabled=False),
            platform=invoke,
            direct=invoke,
            validate=lambda value: validate_model_content(value, request(), allowed_refs={SOURCE}),
            write_state=AsyncMock(),
            observe=audit.event,
        )
    await audit.finish("failed", trace={}, error_code="AgentUnavailable")
    events = audit._write.await_args.args[4]
    failed = [event for event in events if event["phase"] == "route_failed"]
    assert len(failed) == 1
    assert failed[0]["contract_code"] == "work_report.metric_unit." + reason
    assert failed[0]["error_code"] == "InvalidAgentResult"
    assert "PRIVATE_BODY" not in json.dumps(events, ensure_ascii=False)
    assert TOKEN not in json.dumps(events, ensure_ascii=False)
    assert "metric_id" not in failed[0] and "expected_unit" not in failed[0]
    invoke.assert_awaited_once()
