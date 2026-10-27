"""Synthetic report contract regression: no model, credentials or database."""

from copy import deepcopy
from datetime import datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from sales_backend.contracts.work_report import GenerationInput, PeriodSelection, ReportContent
from sales_backend.domain.model_contract import contract_failure_details
from sales_backend.domain.work_report import canonical_hash, default_scheme, render_content, resolve_period
from sales_backend.services.agent_platform.routing import InvalidAgentResult, RoutingPolicy
from sales_backend.services.work_report_generation import GenerationProviders, generate_work_report
from sales_backend.services.work_report_generation.content import metric_fragment, validate_model_content

TOKEN = "{{metric:visits}}"
U1, U2, U3 = (str(UUID(int=i)) for i in (1, 2, 3))
SOURCE = "visit:" + U2
NOW = datetime.fromisoformat("2026-10-09T13:00:00+08:00")


def request(*, display="1", unit="次"):
    config = default_scheme()
    return GenerationInput(
        snapshot_id=U1,
        snapshot_hash="a" * 64,
        period=resolve_period(PeriodSelection(), now=NOW),
        scope=dict(kind="self", label="本人", member_ids=[U1]),
        permission_version="v1",
        data_as_of=NOW,
        coverage=[
            dict(
                source_type=k,
                selected=k == "visit",
                status="complete" if k == "visit" else "not_selected",
                total=1 if k == "visit" else None,
                included=1 if k == "visit" else 0,
                statistics_complete=k == "visit",
            )
            for k in ("visit", "opportunity", "task", "risk")
        ],
        sources=[
            dict(
                source_id=SOURCE,
                source_type="visit",
                record_id=U2,
                business_at=NOW,
                observed_at=NOW,
                time_basis="interaction_at",
                state_kind="occurred",
                title="Synthetic",
                text="Synthetic source",
            )
        ],
        metrics=[
            dict(
                metric_id="visits",
                label="Synthetic count",
                kind="count",
                value="1",
                unit=unit,
                display_value=display,
                definition="Backend count",
                period_basis="occurred_in_period",
                complete=True,
            )
        ],
        supplements=[],
        scheme=dict(scheme_id=U3, version_no=1, name="Scheme", config=config, config_hash=canonical_hash(config)),
        rules_version="v1",
    )


def content(text, *, refs=None):
    return dict(
        title="Synthetic report",
        sections=[
            dict(
                id=s.id,
                title=s.title,
                items=[
                    dict(kind="fact", text=text, source_refs=[SOURCE], metric_refs=["visits"] if refs is None else refs)
                ]
                if i == 0
                else [],
            )
            for i, s in enumerate(default_scheme().sections)
        ],
        missing_data=[],
    )


def validate(body, req, **kwargs):
    return validate_model_content(body, req, allowed_refs={SOURCE}, **kwargs)


@pytest.mark.parametrize("spacing", ["", " ", "  ", "\t", "\u00a0", "\u3000", " \t\u2009"])
def test_same_unit_horizontal_spacing_has_identical_canonical_render(spacing):
    req = request()
    raw = content("Synthetic " + TOKEN + spacing + "次。")
    before = deepcopy(raw)
    expected = content("Synthetic " + metric_fragment(req.metrics[0]) + "。")
    normalized = validate(raw, req)
    assert normalized == expected
    assert raw == before
    assert validate(normalized, req) == normalized
    assert render_content(ReportContent(**normalized), req) == render_content(ReportContent(**expected), req)
    assert "1 次" in render_content(ReportContent(**normalized), req)
    assert req.metrics[0].value == req.metrics[0].display_value == "1"


@pytest.mark.parametrize(
    "suffix,reason",
    [
        (" 件", "mismatched_suffix"),
        ("百次", "scale_prefix"),
        (" 万次", "scale_prefix"),
        (" 100次", "numeric_prefix"),
        ("\n次", "nonhorizontal_separator"),
        ("\r次", "nonhorizontal_separator"),
        ("\u2028次", "nonhorizontal_separator"),
    ],
)
def test_wrong_scaled_or_nonhorizontal_unit_is_not_repaired(suffix, reason):
    with pytest.raises(InvalidAgentResult) as failure:
        validate(content(TOKEN + suffix + "。"), request())
    assert contract_failure_details(failure.value) == {"contract_code": "work_report.metric_unit." + reason}


@pytest.mark.parametrize("suffix", ["", "。", "！", "？", "!", "?", "；", ";", "，后续", ", next", "、后续"])
@pytest.mark.parametrize("spacing", ["", " \t\u3000"])
def test_clear_omission_uses_only_backend_unit_and_preserves_remaining_text(suffix, spacing):
    req = request(unit="原币", display="1.25")
    req.metrics[0].value = "1.25"
    raw = content("Synthetic " + TOKEN + spacing + suffix)
    before = deepcopy(raw)
    expected = content("Synthetic " + metric_fragment(req.metrics[0]) + suffix)
    normalized = validate(raw, req)
    assert normalized == expected
    assert raw == before
    assert validate(normalized, req) == normalized
    assert render_content(ReportContent(**normalized), req) == render_content(ReportContent(**expected), req)
    assert "1.25 原币" in render_content(ReportContent(**normalized), req)
    assert req.metrics[0].value == req.metrics[0].display_value == "1.25"


@pytest.mark.parametrize(
    "suffix",
    [".", ".5", ":", "：", ":30", ")", "）", "]", "】", "》", '"', "”", "’", "'",
     ",000", "，000", "，１２", "(次)", "**次**", "后续", "\n", TOKEN + " 次"],
)
def test_ambiguous_omission_still_rejects_instead_of_supplying_unit(suffix):
    raw = content(TOKEN + suffix)
    before = deepcopy(raw)
    with pytest.raises(InvalidAgentResult):
        validate(raw, request())
    assert raw == before


def test_literal_multi_character_unit_is_not_converted_or_guessed():
    req = request(unit="原币")
    assert validate(content(TOKEN + "原币。"), req)["sections"][0]["items"][0]["text"] == TOKEN + " 原币。"
    for other in ("元", "万元", "美元"):
        with pytest.raises(InvalidAgentResult):
            validate(content(TOKEN + other + "。"), req)


@pytest.mark.parametrize(
    "text,accepted",
    [
        (TOKEN + "次；" + TOKEN + "\t次。", True),
        (TOKEN + "次；" + TOKEN + " 件。", False),
        (TOKEN + "；" + TOKEN + "次。", True),
        (TOKEN + "；" + TOKEN + "。", True),
        (TOKEN + "；" + TOKEN + " 万次。", False),
        (TOKEN + "；" + TOKEN + ".5次。", False),
    ],
)
def test_every_occurrence_is_checked_without_offset_loss(text, accepted):
    if accepted:
        result = validate(content(text), request())
        assert result["sections"][0]["items"][0]["text"] == TOKEN + " 次；" + TOKEN + " 次。"
    else:
        with pytest.raises(InvalidAgentResult):
            validate(content(text), request())


@pytest.mark.parametrize(
    "text,refs",
    [("{{metric:unknown}}次", ["unknown"]), ("{{metric:unknown}}。", ["unknown"]),
     (TOKEN + "次", []), (TOKEN + "。", []), ("1次", ["visits"])],
)
def test_unknown_metric_literal_number_and_reference_mismatch_still_fail(text, refs):
    with pytest.raises(InvalidAgentResult):
        validate(content(text, refs=refs), request())


@pytest.mark.parametrize("case", ["incomplete", "unknown_value", "planned_as_fact", "unknown_source", "outside_batch"])
def test_unit_completion_cannot_bypass_snapshot_or_source_guards(case):
    req = request()
    raw = content(TOKEN + "。")
    allowed = {SOURCE}
    if case == "incomplete":
        req.metrics[0].complete = False
    elif case == "unknown_value":
        req.metrics[0].value = None
    elif case == "planned_as_fact":
        req.metrics[0].period_basis = "planned_in_period"
    elif case == "unknown_source":
        raw["sections"][0]["items"][0]["source_refs"] = ["visit:" + U3]
    else:
        allowed = set()
    original = deepcopy(raw)
    with pytest.raises(InvalidAgentResult):
        validate_model_content(raw, req, allowed_refs=allowed)
    assert raw == original


@pytest.mark.parametrize("suffix", ["", "。", "，后续"])
def test_backend_display_unit_branch_is_unchanged_and_does_not_duplicate_unit(suffix):
    req = request(display="1次")
    raw = content(TOKEN + suffix)
    assert validate(raw, req) == raw
    assert "1次" + suffix in render_content(ReportContent(**raw), req)
    assert metric_fragment(req.metrics[0]) == TOKEN


@pytest.mark.parametrize("boundary", ["item", "batch", "content"])
@pytest.mark.parametrize("suffix", ["次。", "。"])
def test_normalization_growth_cannot_bypass_existing_length_limits(boundary, suffix):
    text = TOKEN + suffix
    raw = content(text)
    kwargs = {}
    if boundary == "item":
        raw["sections"][0]["items"][0]["text"] = "x" * (12000 - len(text)) + text
    elif boundary == "batch":
        kwargs["text_limit"] = len(text)
    else:
        item = raw["sections"][0]["items"][0]
        item["text"] = "x" * (8000 - len(text)) + text
        raw["sections"][0]["items"] = [deepcopy(item) for _ in range(8)]
    ReportContent.model_validate(raw)  # The original input is within schema bounds.
    with pytest.raises(InvalidAgentResult):
        validate(raw, request(), **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["次。", "。", ""])
async def test_real_generator_accepts_format_alias_then_backend_renders_frozen_value(suffix):
    req = request()
    direct = AsyncMock(return_value=content(TOKEN + suffix))
    result = await generate_work_report(
        req,
        providers=GenerationProviders(direct=direct),
        assert_active=AsyncMock(),
        routing_policy=RoutingPolicy(
            platform_enabled=False, total_seconds=2, platform_seconds=0.5, receipt_seconds=0.1
        ),
    )
    assert result.status == "generated" and result.provider == "direct"
    assert result.trace.chunks_completed == result.trace.chunks_total == 1
    assert "1 次" + ("。" if suffix else "") in render_content(result.content, req)
    assert result.snapshot_hash == req.snapshot_hash
    direct.assert_awaited_once()
