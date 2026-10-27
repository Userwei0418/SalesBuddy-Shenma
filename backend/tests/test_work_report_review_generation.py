"""Exercise the real router/generator with fake transports, no live inference."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from sales_backend.contracts.work_report import GenerationInput, PeriodSelection, SnapshotSelection
from sales_backend.domain.work_report import (
    WorkReportError,
    render_content,
    render_report_text,
    resolve_period,
    validate_generation_result,
    validate_source_note_revision,
)
from sales_backend.services.agent_platform.routing import (
    AgentUnavailable,
    ProviderUnavailable,
    ReconciliationRequired,
    RoutingPolicy,
    WriteState,
)
from sales_backend.services.work_report_generation import GenerationProviders, generate_work_report, service
from sales_backend.services.work_report_generation.content import model_output_schema
from tests.test_work_report_contract import NOW, request_data
from tests.test_work_report_review import facts_data

POLICY = RoutingPolicy(platform_enabled=True, total_seconds=3, platform_seconds=0.5, receipt_seconds=0.1)


def request(mode):
    data, facts = request_data(), facts_data()
    if mode == "daily":
        data["period"] = resolve_period(PeriodSelection(kind="day"), now=NOW).model_dump(mode="json")
        facts["period"] = data["period"]
        facts["measures"][1].update(role="day_change", comparison_boundary=data["period"]["start"])
    return GenerationInput.model_validate({**data, "review_mode": mode, "review_facts": facts})


def wire_item(kind, text, refs, metrics=()):
    return {"kind": kind, "text": text, "source_refs": refs, "metric_refs": list(metrics)}


def answer(envelope):
    result = deepcopy(envelope["output_template"])
    data = envelope["input"]
    sections = {row["id"]: row for row in result["sections"]}
    old = [row for row in result["sections"] if not row["id"].startswith("review_")]
    if old:
        refs = [row["source_id"] for row in data["sources"]]
        if refs:
            old[0]["items"].append(wire_item("summary", "原有完整正文，不能丢弃或裁成日总结。", refs))
        for row in data["supplements"]:
            old[0]["items"].append(wire_item("user_supplement", "用户补充内容。", [row["source_id"]]))
        # A review-only batch has no ordinary material; leave the legacy
        # sections empty instead of inventing an unrelated metric sentence.
    evidence = data["review_facts"]["evidence"]
    allowed_refs = set(data["generation_constraints"]["allowed_source_refs"])
    safe = [
        row["source_ref"]
        for row in evidence
        if row["source_ref"] in allowed_refs and row["state_kind"] in {"current", "occurred"}
    ]
    refs = [row["source_ref"] for row in evidence if row["source_ref"] in allowed_refs]
    fact = wire_item("fact", "合成样例：预测 {{metric:forecast.current}} 元。", safe, ["forecast.current"])
    impact = wire_item("summary", "合成样例：仍需核对来源限定。", refs)
    next_step = wire_item("suggestion", "建议核实客户安排，等待人工确认。", refs)
    if data["review_mode"] == "daily":
        sections["review_0_fact"]["items"] = [fact]
        sections["review_0_impact"]["items"] = [impact]
        sections["review_0_next"]["items"] = [next_step]
    else:
        # Empty per-batch Review sections are valid when this batch contains only old prose.
        if evidence:
            sections["review_0_progress"]["items"] = [fact]
            sections["review_0_risk"]["items"] = [impact]
            sections["review_0_next"]["items"] = [next_step]
    return result


async def generate(mode="weekly", *, platform=None, direct=None, active=None, seal=None):
    platform = platform or AsyncMock(side_effect=answer)
    direct = direct or AsyncMock(side_effect=answer)
    seal = seal or AsyncMock(return_value=WriteState.NONE)
    req = request(mode)
    result = await generate_work_report(
        req,
        providers=GenerationProviders(direct=direct, platform=platform, seal_platform=seal),
        assert_active=active or AsyncMock(),
        routing_policy=POLICY,
    )
    validate_generation_result(result, req)
    return result, platform, direct, seal


@pytest.mark.asyncio
async def test_weekly_old_prose_and_review_share_one_model_response():
    result, platform, direct, seal = await generate()
    assert result.provider == "agent_platform" and result.status == "generated"
    assert result.trace.chunks_total == result.trace.chunks_completed == 1
    platform.assert_awaited_once()
    direct.assert_not_awaited()
    seal.assert_awaited_once()
    assert result.content.sections[0].items[0].text == "原有完整正文，不能丢弃或裁成日总结。"
    assert result.content.review_content.groups[0].next_week_tasks[0].requires_confirmation
    assert result.coverage_status == "partial"  # Honest missing historical baseline.
    envelope = platform.await_args.args[0]
    assert len(envelope["output_template"]["sections"]) == len(result.content.sections) + 3
    assert envelope["input"]["review_projection"]["coverage_basis"] == "full_frozen_snapshot"


@pytest.mark.asyncio
async def test_daily_requests_only_complete_review_slots_and_no_old_prose():
    result, platform, direct, _ = await generate("daily")
    assert len(result.content.review_content.items) == 1
    assert all(not row.items for row in result.content.sections)
    envelope = platform.await_args.args[0]
    assert len(envelope["output_template"]["sections"]) == 9
    assert all(row["id"].startswith("review_") for row in envelope["output_template"]["sections"])
    assert envelope["input"]["sources"] == [] and envelope["input"]["supplements"] == []
    assert result.trace.chunks_total == 1
    direct.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "partial",
        "extra",
        "wrong_kind",
        "unknown_ref",
        "old_only",
        "wrong_group",
        "blank_old",
        "blank_review",
    ],
)
async def test_invalid_platform_format_uses_same_snapshot_safe_direct_fallback(fault):
    async def invalid(envelope):
        result = answer(envelope)
        rows = result["sections"]
        if fault == "partial":
            rows[-2]["items"] = []
            rows[-1]["items"] = []
        elif fault == "extra":
            rows.append(deepcopy(rows[-1]))
        elif fault == "wrong_kind":
            rows[-1]["items"][0]["kind"] = "plan"
        elif fault == "unknown_ref":
            rows[-1]["items"][0]["source_refs"] = ["review:forged"]
        elif fault == "old_only":
            result["sections"] = rows[:-3]
        elif fault == "blank_old":
            for row in rows[:-3]:
                row["items"] = []
        elif fault == "blank_review":
            for row in rows[-3:]:
                row["items"] = []
        else:
            rows[-1]["id"] = "review_other_next"
        return result

    # Weekly empty risk/next is legitimate; use the strict daily triple for the partial case.
    mode = "daily" if fault == "partial" else "weekly"
    if fault == "partial":

        async def invalid(envelope):
            result = answer(envelope)
            result["sections"][1]["items"] = []
            return result

    result, platform, direct, seal = await generate(mode, platform=AsyncMock(side_effect=invalid))
    assert result.provider == "direct" and result.trace.fallback_reason == "invalid_agent_result"
    assert direct.await_args.args[0] == platform.await_args.args[0]
    seal.assert_awaited_once()


@pytest.mark.asyncio
async def test_daily_unavailable_model_is_failure_not_basic_summary():
    with pytest.raises(AgentUnavailable):
        await generate(
            "daily",
            platform=AsyncMock(side_effect=ProviderUnavailable("synthetic")),
            direct=AsyncMock(side_effect=ProviderUnavailable("synthetic")),
        )


@pytest.mark.asyncio
async def test_weekly_unavailable_model_retains_explicit_legacy_basic_summary_without_review_success():
    result, _, _, _ = await generate(
        platform=AsyncMock(side_effect=ProviderUnavailable("synthetic")),
        direct=AsyncMock(side_effect=ProviderUnavailable("synthetic")),
    )
    assert result.status == "basic_summary" and result.provider == "none"
    assert result.content.review_content is None
    assert "no_baseline" in result.content.missing_data


@pytest.mark.asyncio
async def test_unknown_platform_write_state_never_dispatches_direct():
    direct = AsyncMock(side_effect=answer)
    with pytest.raises(ReconciliationRequired):
        await generate(direct=direct, seal=AsyncMock(return_value=WriteState.UNKNOWN))
    direct.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_authority_never_dispatches_any_provider():
    platform, direct = AsyncMock(side_effect=answer), AsyncMock(side_effect=answer)
    with pytest.raises(WorkReportError):
        await generate(
            platform=platform,
            direct=direct,
            active=AsyncMock(side_effect=WorkReportError("WORK_REPORT_PERMISSION_CHANGED", "revoked")),
        )
    platform.assert_not_awaited()
    direct.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["daily", "weekly"])
async def test_existing_batch_and_merge_pipeline_has_no_extra_review_stage(monkeypatch, mode):
    monkeypatch.setattr(service, "BATCH_CHARACTERS", 1)
    result, platform, direct, seal = await generate(mode)
    expected = 3 if mode == "daily" else 5
    assert result.trace.chunks_completed == result.trace.chunks_total == platform.await_count == expected
    assert seal.await_count == expected
    direct.assert_not_awaited()
    assert result.content.review_content is not None


@pytest.mark.asyncio
async def test_rendering_uses_frozen_values_and_does_not_silently_omit_review():
    result, _, _, _ = await generate()
    text = render_content(result.content, request("weekly"))
    assert "原有完整正文" in text and "120.00 元" in text
    assert "待人工确认" in text and "review:forecast" in text
    with pytest.raises(WorkReportError, match="原授权事实"):
        render_report_text(result.content, {"visits": "1"})
    edited = result.content.model_copy(deep=True)
    edited.review_content = None
    with pytest.raises(WorkReportError):
        validate_source_note_revision(edited, result.content)


def test_legacy_wire_schema_has_no_review_types_or_backend_extensions():
    schema = model_output_schema()
    assert "review_content" not in schema["properties"]
    assert not any("Review" in name for name in schema["$defs"])
    assert "source_notes" not in schema["$defs"]["ReportItem"]["properties"]


@pytest.mark.parametrize("mode,kind", [("daily", "day"), ("weekly", "week")])
def test_review_selection_allows_server_sources_but_rejects_client_facts_or_legacy_binding(mode, kind):
    data = {"review_mode": mode, "period": {"kind": kind}, "sources": []}
    assert SnapshotSelection.model_validate(data).sources == []
    for extra in ({"review_facts": facts_data()}, {"overview_binding": "legacy"}, {"period": {"kind": "month"}}):
        with pytest.raises(ValidationError):
            SnapshotSelection.model_validate({**data, **extra})
    with pytest.raises(ValidationError):
        SnapshotSelection(sources=[])


@pytest.mark.asyncio
async def test_sales_six_sections_and_ppl_source_notes_survive_joint_review_generation():
    from tests.test_work_report_retained_facts import weekly_content, weekly_request

    old_request = weekly_request()
    data = old_request.model_dump(mode="python")
    facts = facts_data()
    for key in ("period", "scope", "data_as_of", "permission_version", "rules_version"):
        facts[key] = data[key]
    for row in facts["evidence"]:
        row["observed_at"] = data["data_as_of"]
    facts["measures"][1]["comparison_boundary"] = data["period"]["start"]
    req = GenerationInput.model_validate({**data, "review_facts": facts, "review_mode": "weekly"})

    async def six_section_answer(envelope):
        result = answer(envelope)
        result["sections"][:6] = weekly_content().model_dump(mode="json")["sections"]
        return result

    platform = AsyncMock(side_effect=six_section_answer)
    direct = AsyncMock(side_effect=six_section_answer)
    result = await generate_work_report(
        req,
        providers=GenerationProviders(
            direct=direct, platform=platform, seal_platform=AsyncMock(return_value=WriteState.NONE)
        ),
        assert_active=AsyncMock(),
        routing_policy=POLICY,
    )
    assert result.status == "generated"
    assert [row.id for row in result.content.sections] == [row.id for row in old_request.scheme.config.sections]
    assert len(result.content.sections) == 6 and result.content.review_content is not None
    assert result.content.sections[-1].items[-1].source_notes
    platform.assert_awaited_once()
    direct.assert_not_awaited()
