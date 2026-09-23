from decimal import Decimal

import pytest

from sales_backend.domain.opportunities import normalize_forecasts, validate_forecast_completeness


@pytest.mark.parametrize("probability", [30, 50, 70, 90, 100])
@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"year": 2026, "quarter": 3, "recognized_amount": None, "collection_amount": None}],
        [{"year": 2026, "quarter": 3, "recognized_amount": 0, "collection_amount": None}],
    ],
)
def test_advanced_stage_cannot_bypass_quarter_plan(probability, rows):
    with pytest.raises(ValueError, match="季度"):
        validate_forecast_completeness({"status": "open", "probability": probability}, rows)


def test_zero_is_a_confirmed_plan_and_close_date_never_moves_quarters():
    old = [{"year": 2026, "quarter": 3, "recognized_amount": Decimal(0), "collection_amount": Decimal(0)}]
    merged = normalize_forecasts(
        old, [{"year": 2027, "quarter": 1, "recognized_amount": Decimal(30), "collection_amount": Decimal(50)}]
    )
    validate_forecast_completeness({"status": "won", "probability": 100}, merged)
    assert merged[0] == old[0] and len(merged) == 2


def test_early_stage_and_lost_do_not_force_fabricated_plans():
    validate_forecast_completeness({"status": "open", "probability": 10}, [])
    validate_forecast_completeness({"status": "lost", "probability": 90}, [])


def test_partial_other_quarter_cannot_hide_behind_a_complete_quarter():
    with pytest.raises(ValueError, match="2027 Q1"):
        validate_forecast_completeness(
            {"status": "open", "probability": 50},
            [
                {"year": 2026, "quarter": 4, "recognized_amount": 0, "collection_amount": 0},
                {"year": 2027, "quarter": 1, "recognized_amount": 100, "collection_amount": None},
            ],
        )
