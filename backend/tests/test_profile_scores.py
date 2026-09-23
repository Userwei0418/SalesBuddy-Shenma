from decimal import Decimal

import pytest

from sales_backend.domain.company_rules import policy_snapshot, validate_policy
from sales_backend.domain.profile_scores import attainment, weighted_score


def test_default_scores_use_percentages_not_amounts():
    values = {"collection": attainment(50, 100), "recognized": attainment(80, 200), "retention": 80}
    score = weighted_score(values, policy_snapshot("score.maturity"))
    assert score["value"] == 52 and score["coverage_percent"] == 100


@pytest.mark.parametrize(
    ("values", "text", "coverage"),
    [
        ({"followup": 90, "customers": 80, "opportunities": 60}, "78", 100),
        ({"followup": 0, "customers": 100}, "43", 70),
        ({"followup": "85.7"}, "86", 40),
        ({"followup": -20, "customers": float("inf"), "opportunities": "bad"}, "0", 40),
        ({"followup": None, "customers": "", "opportunities": False}, "--", 0),
    ],
)
def test_missing_zero_and_out_of_range_evidence(values, text, coverage):
    score = weighted_score(values, policy_snapshot("score.efficiency"))
    assert score["text"] == text and score["coverage_percent"] == coverage


def test_zero_target_disabled_dimension_and_rounding():
    assert attainment(10, 0) is None and attainment(None, 100) is None
    rule = policy_snapshot("score.efficiency")
    rule["definition"].update(followup=100, customers=0, opportunities=0)
    score = weighted_score({"followup": Decimal("60.5"), "customers": 90}, rule)
    assert score["text"] == "61" and score["count"] == score["total"] == 1
    assert weighted_score({"customers": 90}, rule)["text"] == "--"


@pytest.mark.parametrize(
    "definition",
    [
        {"followup": 20},
        {"followup": True},
        {"followup": -1},
        {"followup": "40"},
        {"followup": 1000},
        {"unknown": 1},
    ],
)
def test_invalid_rule_cannot_be_published(definition):
    with pytest.raises(ValueError):
        validate_policy("score.efficiency", definition)
