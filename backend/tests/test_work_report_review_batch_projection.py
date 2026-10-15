"""Review provenance must obey the same batch boundary as generated references."""

from copy import deepcopy

import pytest

from sales_backend.contracts.work_report import GenerationInput
from sales_backend.services.agent_platform.routing import InvalidAgentResult
from sales_backend.services.work_report_generation.review import validate_combined_review_wire
from sales_backend.services.work_report_generation.service import _envelope, _material
from tests.test_work_report_review_generation import answer, request


@pytest.mark.parametrize("mode", ["daily", "weekly"])
@pytest.mark.parametrize("phase", ["summarize", "combine"])
def test_model_batch_references_are_filtered_without_mutating_frozen_facts(mode, phase):
    req = request(mode)
    frozen = req.model_dump(mode="json")
    # The statistic's source is in another batch, just as in the failed live run.
    material = [row for row in _material(req) if row["source_id"] == "review:task"]
    if phase == "combine":
        material = [{key: value for key, value in row.items() if key != "text"} for row in material]
    envelope = _envelope(req, material=material, summaries=[], phase=phase, index=0, total=3, text_limit=6000)
    projected = envelope["input"]["review_facts"]
    assert projected["groups"][0]["source_refs"] == ["review:task"]
    assert all(row["source_refs"] == [] for row in projected["measures"])
    for key in ("groups", "measures"):
        for actual, original in zip(projected[key], frozen["review_facts"][key], strict=True):
            assert {k: v for k, v in actual.items() if k != "source_refs"} == {
                k: v for k, v in original.items() if k != "source_refs"
            }
    assert projected["missing_data"] == frozen["review_facts"]["missing_data"]
    assert req.model_dump(mode="json") == frozen
    constraints = envelope["input"]["generation_constraints"]
    assert constraints["metric_fragments"]["forecast.current"] == "{{metric:forecast.current}} 元"
    assert all(set(row["source_refs"]) <= {"review:task"} for row in constraints["section_references"].values())
    assert envelope["input"]["review_projection"]["coverage_basis"] == "full_frozen_snapshot"


def test_combine_can_use_all_accepted_sources_without_losing_frozen_provenance():
    req = request("weekly")
    material = [{key: value for key, value in row.items() if key != "text"} for row in _material(req)]
    envelope = _envelope(req, material=material, summaries=[], phase="combine", index=2, total=3, text_limit=64000)
    projected = envelope["input"]["review_facts"]
    frozen = req.review_facts.model_dump(mode="json")
    assert projected["groups"] == frozen["groups"]
    assert projected["measures"] == frozen["measures"]


@pytest.mark.parametrize("include_baseline", [False, True])
def test_baseline_reference_uses_batch_scope_without_erasing_historical_metadata(include_baseline):
    data = request("weekly").model_dump(mode="json")
    facts = data["review_facts"]
    facts["evidence"].append({**deepcopy(facts["evidence"][0]), "source_ref": "review:baseline", "kind": "baseline"})
    boundary = data["period"]["start"]
    facts["measures"][1].update(
        value="1.00", coverage="complete", missing_reasons=[],
        source_refs=["review:forecast", "review:baseline"],
        baseline={
            "source_ref": "review:baseline", "boundary_at": boundary, "captured_at": boundary,
            "definition_version": 1, "method": "boundary_snapshot", "continuous_coverage": True,
            "same_authorized_population": True,
        },
    )
    facts["missing_data"] = []
    req = GenerationInput.model_validate(data)
    frozen = req.model_dump(mode="json")
    material = _material(req) if include_baseline else [
        row for row in _material(req) if row["source_id"] != "review:baseline"
    ]
    envelope = _envelope(req, material=material, summaries=[], phase="combine", index=2, total=3, text_limit=64000)
    baseline = envelope["input"]["review_facts"]["measures"][1]["baseline"]
    expected = deepcopy(frozen["review_facts"]["measures"][1]["baseline"])
    if not include_baseline:
        expected.pop("source_ref")
    assert baseline == expected
    assert req.model_dump(mode="json") == frozen
    # Formal facts remain valid with their required nonempty provenance intact.
    assert GenerationInput.model_validate(frozen) == req


@pytest.mark.parametrize("fault", ["unreferenced_row", "outside_batch"])
def test_invalid_provider_rows_are_still_rejected_not_removed_or_repaired(fault):
    req = request("weekly")
    allowed = {row["source_id"] for row in _material(req)} - {"review:task"}
    material = [row for row in _material(req) if row["source_id"] in allowed]
    envelope = _envelope(req, material=material, summaries=[], phase="summarize", index=0, total=3, text_limit=6000)
    rules = envelope["input"]["generation_constraints"]["rules"]
    assert any("两者均空的条目禁止输出" in rule for rule in rules)
    assert any("保留items=[]" in rule and "missing_data" in rule for rule in rules)
    payload = answer(envelope)
    validate_combined_review_wire(payload, req, allowed_refs=allowed, text_limit=6000)
    section = next(row for row in payload["sections"] if row["id"] == "review_0_risk")
    section["items"][0]["source_refs"] = [] if fault == "unreferenced_row" else ["review:task"]
    section["items"][0]["metric_refs"] = []
    before = deepcopy(payload)
    with pytest.raises(InvalidAgentResult):
        validate_combined_review_wire(payload, req, allowed_refs=allowed, text_limit=6000)
    assert payload == before
