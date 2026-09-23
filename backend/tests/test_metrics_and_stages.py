import pytest

from sales_backend.domain.opportunities import stage_for_probability
from sales_backend.repositories.profile import evaluation_metrics


def aggregate(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "active_opportunity_amount": 1_200_000,
        "won_amount": 800_000,
        "won_count": 4,
        "opportunity_count": 16,
        "customer_count": 20,
        "a_customer_count": 5,
        "visit_count": 40,
        "week_visit_count": 3,
        "quarter_visit_count": 12,
        "year_visit_count": 38,
        "quarter_customer_count": 9,
        "visits_with_next_action": 30,
    }
    values.update(overrides)
    return values


def test_evaluation_metrics_compute_the_four_ratios() -> None:
    metrics = evaluation_metrics(aggregate())

    assert metrics["maturity"] == {
        "active_opportunity_amount": 1_200_000,
        "won_amount": 800_000,
        "win_rate": 25.0,
        "a_customer_share": 25.0,
    }
    assert metrics["efficiency"]["followup_closure_rate"] == 75.0
    assert metrics["efficiency"]["historical_visit_count"] == 40


def test_empty_scope_returns_zero_ratios_rather_than_dividing_by_zero() -> None:
    metrics = evaluation_metrics(
        aggregate(opportunity_count=0, customer_count=0, visit_count=0, won_count=0)
    )

    assert metrics["maturity"]["win_rate"] == 0
    assert metrics["maturity"]["a_customer_share"] == 0
    assert metrics["efficiency"]["followup_closure_rate"] == 0


def test_null_counts_from_sql_are_read_as_zero() -> None:
    metrics = evaluation_metrics(
        aggregate(
            active_opportunity_amount=None,
            won_amount=None,
            won_count=None,
            a_customer_count=None,
            week_visit_count=None,
            visits_with_next_action=None,
        )
    )

    assert metrics["maturity"]["active_opportunity_amount"] == 0
    assert metrics["maturity"]["win_rate"] == 0
    assert metrics["efficiency"]["week_visit_count"] == 0
    assert metrics["efficiency"]["followup_closure_rate"] == 0


def test_ratios_keep_one_decimal() -> None:
    metrics = evaluation_metrics(aggregate(won_count=1, opportunity_count=3))

    assert metrics["maturity"]["win_rate"] == 33.3


@pytest.mark.parametrize(
    ("probability", "stage"),
    [
        (10, "identified"),
        (30, "qualified"),
        (50, "solution"),
        (70, "proposal"),
        (90, "negotiation"),
        ("70", "proposal"),
    ],
)
def test_stage_follows_probability(probability: object, stage: str) -> None:
    assert stage_for_probability(probability) == stage


@pytest.mark.parametrize("probability", [0, 20, 60, 100, 95])
def test_probability_outside_the_five_steps_is_rejected(probability: int) -> None:
    with pytest.raises(ValueError, match="商机概率只能是"):
        stage_for_probability(probability)
