"""Deterministic Review contract checks; no model, database or credentials."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from sales_backend.contracts.work_report import GenerationInput
from sales_backend.contracts.work_report_review import ReviewFacts
from sales_backend.services.agent_platform.routing import InvalidAgentResult
from sales_backend.services.work_report_generation.review import (
    review_model_input,
    validate_review_content,
)
from tests.test_work_report_contract import request_data


def facts_data():
    request = request_data()
    return {
        "period": request["period"],
        "scope": request["scope"],
        "permission_version": request["permission_version"],
        "rules_version": request["rules_version"],
        "data_as_of": request["data_as_of"],
        "evidence": [
            {
                "source_ref": "review:forecast",
                "kind": "metric_query",
                "record_id": "forecast_recognized",
                "record_version": "1",
                "observed_at": request["data_as_of"],
                "state_kind": "current",
                "text": "合成样例：本次授权范围的预测确收。",
            },
            {
                "source_ref": "review:task",
                "kind": "task",
                "record_id": "task-1",
                "record_version": "2",
                "observed_at": request["data_as_of"],
                "state_kind": "planned",
                "text": "合成样例：客户沟通任务尚未执行。",
            },
        ],
        "measures": [
            {
                "measure_id": "forecast.current",
                "metric_code": "forecast_recognized",
                "definition_version": 1,
                "role": "forecast",
                "value": "120.00",
                "unit": "元",
                "currency": "CNY",
                "tax_basis": "tax_inclusive",
                "coverage": "complete",
                "source_refs": ["review:forecast"],
            },
            {
                "measure_id": "forecast.week_change",
                "metric_code": "forecast_recognized",
                "definition_version": 1,
                "role": "week_change",
                "value": None,
                "unit": "元",
                "currency": "CNY",
                "tax_basis": "tax_inclusive",
                "coverage": "unavailable",
                "source_refs": ["review:forecast"],
                "comparison_boundary": request["period"]["start"],
                "missing_reasons": ["no_baseline"],
            },
        ],
        "groups": [
            {
                "group_id": "metric:forecast_recognized",
                "metric_code": "forecast_recognized",
                "title": "预测确收",
                "source_refs": ["review:forecast", "review:task"],
                "measure_refs": ["forecast.current", "forecast.week_change"],
            }
        ],
        "missing_data": ["no_baseline"],
    }


def statement(text="合成样例：当前预测 {{metric:forecast.current}} 元。"):
    return {"text": text, "source_refs": ["review:forecast"], "measure_refs": ["forecast.current"]}


def suggestion():
    return {
        "text": "建议核实客户安排，待人工确认。",
        "source_refs": ["review:task"],
        "measure_refs": [],
        "kind": "suggestion",
        "requires_confirmation": True,
    }


def daily():
    return {
        "items": [{"fact": statement(), "impact_or_risk": statement(), "next_step": suggestion()}],
        "missing_data": ["no_baseline"],
    }


def weekly():
    return {
        "groups": [
            {
                "group_id": "metric:forecast_recognized",
                "progress": [{**statement(), "kind": "current"}],
                "risks": [],
                "next_week_tasks": [suggestion()],
            }
        ],
        "missing_data": ["no_baseline"],
    }


def test_legacy_serialization_and_explicit_null_are_unchanged():
    old = GenerationInput.model_validate(request_data())
    before = old.model_dump(mode="json")
    assert "review_facts" not in before
    with_null = GenerationInput.model_validate({**request_data(), "review_facts": None})
    assert with_null.model_dump(mode="json") == before
    assert with_null.model_dump_json() == old.model_dump_json()


def test_versioned_facts_roundtrip_and_model_projection_are_deterministic():
    request = GenerationInput.model_validate({**request_data(), "review_mode": "weekly", "review_facts": facts_data()})
    assert GenerationInput.model_validate_json(request.model_dump_json()) == request
    projection = review_model_input(request.review_facts, mode="weekly")
    assert projection["constraints"]["metric_fragments"] == {"forecast.current": "{{metric:forecast.current}} 元"}
    assert projection["constraints"]["required_missing_data"] == ["no_baseline"]
    assert projection["facts"]["measures"][1]["value"] is None
    assert "generation_binding" not in projection
    assert validate_review_content(daily(), request.review_facts, mode="daily").items[0].next_step.requires_confirmation
    assert (
        validate_review_content(weekly(), request.review_facts, mode="weekly").groups[0].group_id
        == "metric:forecast_recognized"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("permission_version", "different"),
        ("rules_version", "different"),
        ("data_as_of", "2099-01-01T00:00:00+08:00"),
    ],
)
def test_snapshot_identity_drift_is_rejected(field, value):
    facts = facts_data()
    facts[field] = value
    with pytest.raises(ValidationError):
        GenerationInput.model_validate({**request_data(), "review_mode": "weekly", "review_facts": facts})


def test_scope_and_period_drift_are_rejected():
    for field in ("scope", "period"):
        facts = facts_data()
        facts[field]["label"] = "其他范围或周期"
        with pytest.raises(ValidationError):
            GenerationInput.model_validate({**request_data(), "review_mode": "weekly", "review_facts": facts})


@pytest.mark.parametrize(
    "fault",
    [
        "partial_number",
        "unknown_zero",
        "without_lineage",
        "task_as_progress",
        "pending_as_effective",
        "wrong_tax",
        "missing_gap",
        "unknown_group_ref",
        "cross_metric",
        "duplicate_source",
        "extra",
        "float_amount",
        "change_without_baseline",
        "future_observation",
    ],
)
def test_untrusted_or_inconsistent_facts_fail_before_dispatch(fault):
    data = facts_data()
    metric = data["measures"][0]
    if fault == "partial_number":
        metric.update(coverage="partial", missing_reasons=["no_baseline"])
    elif fault == "unknown_zero":
        data["measures"][1]["value"] = "0"
    elif fault == "without_lineage":
        metric["source_refs"] = []
    elif fault == "task_as_progress":
        metric["source_refs"] = ["review:task"]
    elif fault == "pending_as_effective":
        metric["role"] = "effective_target"
    elif fault == "wrong_tax":
        metric["tax_basis"] = "tax_exclusive"
    elif fault == "missing_gap":
        data["missing_data"] = []
    elif fault == "unknown_group_ref":
        data["groups"][0]["source_refs"] = ["review:forged"]
    elif fault == "cross_metric":
        metric["metric_code"] = "collection"
    elif fault == "duplicate_source":
        data["evidence"].append(deepcopy(data["evidence"][0]))
    elif fault == "extra":
        data["client_supplied"] = True
    elif fault == "float_amount":
        metric["value"] = 120.0
    elif fault == "change_without_baseline":
        metric["role"] = "day_change"
    else:
        data["evidence"][0]["observed_at"] = "2099-01-01T00:00:00+08:00"
    with pytest.raises(ValidationError):
        ReviewFacts.model_validate(data)


@pytest.mark.parametrize(
    "fault",
    [
        "four_items",
        "invented_ref",
        "planned_fact",
        "claim_fact",
        "unknown_number",
        "unit_drift",
        "hidden_gap",
        "invented_gap",
        "auto_task",
        "confirmed_task",
        "invented_owner",
        "no_evidence",
    ],
)
def test_daily_semantic_boundaries(fault):
    data = facts_data()
    payload = daily()
    item = payload["items"][0]
    if fault == "four_items":
        payload["items"] *= 4
    elif fault == "invented_ref":
        item["fact"]["source_refs"] = ["review:forged"]
    elif fault in {"planned_fact", "claim_fact"}:
        data["evidence"][1]["state_kind"] = "planned" if fault == "planned_fact" else "declaration"
        item["fact"] = {"text": "已完成客户沟通。", "source_refs": ["review:task"], "measure_refs": []}
    elif fault == "unknown_number":
        item["fact"] = {
            "text": "本周变化 {{metric:forecast.week_change}} 元。",
            "source_refs": [],
            "measure_refs": ["forecast.week_change"],
        }
    elif fault == "unit_drift":
        item["fact"]["text"] = "预测 {{metric:forecast.current}} 万元。"
    elif fault == "hidden_gap":
        payload["missing_data"] = []
    elif fault == "invented_gap":
        payload["missing_data"].append("本周无任何工作。")
    elif fault == "auto_task":
        item["next_step"]["kind"] = "created"
    elif fault == "confirmed_task":
        item["next_step"]["requires_confirmation"] = False
    elif fault == "invented_owner":
        item["next_step"]["owner_id"] = "made-up"
    else:
        item["impact_or_risk"] = {"text": "无依据内容", "source_refs": [], "measure_refs": []}
    with pytest.raises(InvalidAgentResult, match="^work_report.review.invalid_content$"):
        validate_review_content(payload, ReviewFacts.model_validate(data), mode="daily")


@pytest.mark.parametrize("fault", ["missing_group", "duplicate_group", "changed_id", "cross_group_ref"])
def test_weekly_group_identity_and_membership(fault):
    facts = facts_data()
    payload = weekly()
    if fault == "missing_group":
        payload["groups"] = []
    elif fault == "duplicate_group":
        payload["groups"] *= 2
    elif fault == "changed_id":
        payload["groups"][0]["group_id"] = "metric:collection"
    else:
        facts["groups"][0]["source_refs"].remove("review:task")
    with pytest.raises(InvalidAgentResult):
        validate_review_content(payload, ReviewFacts.model_validate(facts), mode="weekly")


def test_three_daily_items_are_preserved_not_silently_truncated():
    payload = daily()
    payload["items"] *= 3
    assert len(validate_review_content(payload, ReviewFacts.model_validate(facts_data()), mode="daily").items) == 3


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "late_capture",
        "different_definition",
        "future_boundary",
        "wrong_source",
        "wrong_boundary",
    ],
)
def test_baseline_requires_traceable_matching_boundary(fault):
    data = facts_data()
    boundary = data["period"]["start"]
    data["evidence"].append({**deepcopy(data["evidence"][0]), "source_ref": "review:baseline", "kind": "baseline"})
    measure = data["measures"][1]
    measure.update(
        value="1.00",
        coverage="complete",
        missing_reasons=[],
        source_refs=["review:forecast", "review:baseline"],
        baseline={
            "source_ref": "review:baseline",
            "boundary_at": boundary,
            "captured_at": boundary,
            "definition_version": 1,
            "method": "boundary_snapshot",
            "continuous_coverage": True,
            "same_authorized_population": True,
        },
    )
    data["missing_data"] = []
    if fault == "late_capture":
        measure["baseline"]["captured_at"] = data["data_as_of"]
    elif fault == "different_definition":
        measure["baseline"]["definition_version"] = 2
    elif fault == "future_boundary":
        measure["baseline"].update(boundary_at="2099-01-01T00:00:00+08:00", captured_at="2099-01-01T00:00:00+08:00")
    elif fault == "wrong_source":
        data["evidence"][-1]["kind"] = "task"
    elif fault == "wrong_boundary":
        measure["comparison_boundary"] = data["data_as_of"]
    if fault:
        with pytest.raises(ValidationError):
            ReviewFacts.model_validate(data)
    else:
        assert ReviewFacts.model_validate(data).measures[1].value == "1.00"


@pytest.mark.parametrize("status", ["effective", "pending"])
def test_pending_target_cannot_be_used_as_effective_denominator(status):
    data = facts_data()
    data["evidence"][0].update(kind="target", target_status=status)
    data["measures"][0]["role"] = "effective_target"
    if status == "pending":
        with pytest.raises(ValidationError):
            ReviewFacts.model_validate(data)
    else:
        assert ReviewFacts.model_validate(data).measures[0].value == "120.00"


@pytest.mark.parametrize(
    "state,kind,accepted",
    [
        ("planned", "fact", False),
        ("planned", "current", False),
        ("planned", "plan", True),
        ("declaration", "fact", False),
        ("declaration", "current", False),
        ("declaration", "declaration", True),
    ],
)
def test_task_plan_or_contribution_preserves_its_claim_status(state, kind, accepted):
    data = facts_data()
    data["evidence"][1]["state_kind"] = state
    payload = weekly()
    payload["groups"][0]["progress"] = [
        {"kind": kind, "text": "合成任务材料。", "source_refs": ["review:task"], "measure_refs": []}
    ]
    facts = ReviewFacts.model_validate(data)
    if accepted:
        assert validate_review_content(payload, facts, mode="weekly").groups[0].progress[0].kind == kind
    else:
        with pytest.raises(InvalidAgentResult):
            validate_review_content(payload, facts, mode="weekly")
