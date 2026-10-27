"""New Review arithmetic/selection counterexamples; no network/model calls."""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sales_backend.contracts.review_overview import ReviewMetric, ReviewSelection
from sales_backend.domain.work_report import SHANGHAI
from sales_backend.services.review_overview import contribution, metric_cards, pool_rows, quarter_info


def row(**changes):
    return {
        "id": str(uuid4()),
        "version_no": 1,
        "status": "open",
        "currency": "CNY",
        "probability": "70",
        "forecast_id": str(uuid4()),
        "collection_confidence": "high",
        "collection_amount": "100.01",
        "recognized_amount": "150.02",
        "updated_at": datetime(2026, 10, 13, 12, tzinfo=SHANGHAI),
        **changes,
    }


def cards(rows, targets=None, allowed=True):
    result = metric_cards(rows, targets or {}, allowed, year=2026, quarter=4)
    for item in result:
        ReviewMetric.model_validate(item)
    return {r["metric_code"]: r for r in result}


def test_three_distinct_denominators_and_no_probability_weighting():
    targets = {
        k: dict(id=str(uuid4()), kind=k, value=v, version_no=2)
        for k, v in [
            ("collection", "1000.10"),
            ("collection_ppl", "2000.20"),
            ("forecast_recognized", "3000.30"),
            ("recognized", "1.00"),
        ]
    }
    data = cards([row(probability="30"), row(probability="70", collection_confidence="low")], targets)
    assert data["forecast_collection"]["value"] == "100.01"
    assert data["collection_ppl"]["value"] == "200.02"
    assert data["forecast_recognized"]["value"] == "150.02"
    assert data["forecast_recognized"]["target"]["kind"] == "forecast_recognized"
    assert data["forecast_recognized"]["tax_basis"] == "tax_inclusive"
    assert data["forecast_collection"]["rate"] == "0.10000000"
    assert data["forecast_collection"]["gap"] == "900.09"


@pytest.mark.parametrize("status", ["lost", "cancelled"])
def test_closed_lost_retained_probability_never_counts(status):
    assert all(r["value"] == "0.00" for r in cards([row(status=status, probability="100")]).values())


def test_won_counts_in_forecast_but_not_active_pool():
    r = row(status="won", probability="100")
    assert cards([r])["collection_ppl"]["value"] == "100.01"
    selection = ReviewSelection(anchor_date="2026-10-14")
    assert pool_rows([r], selection, "all") == []
    assert pool_rows([r], selection, "all", metric_code="collection_ppl") == [r]


def test_missing_forecast_keeps_unknown_eligibility_but_rate_uses_known_subtotal():
    missing = row(forecast_id=None, collection_amount=None, recognized_amount=None, collection_confidence=None)
    target = {"collection_ppl": dict(kind="collection_ppl", value="10.00", id=str(uuid4()), version_no=1)}
    result = cards([row(), missing], target)
    assert result["collection_ppl"]["value"] is None
    assert result["collection_ppl"]["known_subtotal"] == "100.01"
    assert result["collection_ppl"]["coverage"] == "partial"
    assert result["collection_ppl"]["missing_amount_count"] == 1
    assert result["collection_ppl"]["rate"] == "10.00100000"
    assert result["collection_ppl"]["gap"] == "-90.01"
    assert result["collection_ppl"]["rate_basis"] == "known_subtotals"
    assert result["forecast_collection"]["unknown_eligibility_count"] == 1


def test_explicit_zero_and_empty_are_complete_but_all_missing_is_not_zero():
    assert cards([row(collection_amount="0.00")])["collection_ppl"]["value"] == "0.00"
    assert cards([])["collection_ppl"]["value"] == "0.00"
    value = cards([row(collection_amount=None)])["collection_ppl"]
    assert value["value"] is None and value["known_subtotal"] is None and value["coverage"] == "unavailable"


def test_low_confidence_is_excluded_not_missing():
    value = cards([row(collection_confidence="low", collection_amount=None)])["forecast_collection"]
    assert value["value"] == "0.00" and value["missing_amount_count"] == 0


def test_unknown_currency_and_qualification_fail_closed():
    assert contribution(row(currency="USD"), "collection_ppl") == (True, None, "currency_unavailable")
    assert cards([row(probability=None)])["collection_ppl"]["unknown_eligibility_count"] == 1


def test_exact_decimal_large_sum_no_binary_floats_and_task_multiplication():
    r = row(collection_amount="9999999999999999.99")
    result = cards([r, r, row(collection_amount="0.02")])["collection_ppl"]
    assert result["value"] == "10000000000000000.01"
    assert len(result["source_refs"]) == 2


def test_conflicting_duplicate_row_cannot_choose_arbitrary_value():
    r = row()
    with pytest.raises(ValueError, match="事实不一致"):
        cards([r, {**r, "collection_amount": "999"}])


def test_no_target_denied_target_and_no_baseline_are_distinct():
    value = cards([row()])["collection_ppl"]
    assert value["target"]["status"] == "no_target" and value["gap"] is None and value["rate"] is None
    denied = cards([row()], allowed=False)["collection_ppl"]
    assert denied["status"] == "target_unavailable" and denied["target"]["status"] == "permission_denied"
    assert denied["baseline"]["status"] == "no_baseline" and denied["day_change"] is None


@pytest.mark.parametrize(
    "value",
    [
        {"scope": "self", "team_id": str(uuid4())},
        {"scope": "member"},
        {"scope": "team", "team_id": str(uuid4()), "member_id": str(uuid4())},
    ],
)
def test_scope_selector_cannot_silently_expand(value):
    with pytest.raises(ValidationError):
        ReviewSelection(**value)


def test_one_quarter_progress_calendar_not_card_specific():
    now = datetime(2026, 10, 14, 12, tzinfo=SHANGHAI)
    result = quarter_info(now.date(), now)
    assert result["quarter"] == 4 and result["start"] == "2026-10-01T00:00:00+08:00"
    assert result["end_exclusive"] == "2027-01-01T00:00:00+08:00"
    assert abs(Decimal(result["time_progress"]) - Decimal("14") / 92) < Decimal("0.00000001")


def test_not_updated_is_field_update_not_followup_or_unconfirmed_threshold():
    selection = ReviewSelection(anchor_date="2026-10-14")
    old = row(last_follow_up_at=datetime(2026, 10, 14, 14, tzinfo=SHANGHAI), updated_in_selected_day=False)
    current = row(forecast_updated_at=datetime(2026, 10, 14, 14, tzinfo=SHANGHAI), updated_in_selected_day=True)
    assert pool_rows([old, current], selection, "not_updated") == [old]
    assert pool_rows([old, current], selection, "stale") == []


def test_stale_seven_calendar_day_boundary_never_fabricates_unknown_creation():
    selection = ReviewSelection(anchor_date="2026-10-14")
    at_seven = row(last_follow_up_at=datetime(2026, 10, 7, 23, 59, tzinfo=SHANGHAI))
    at_six = row(last_follow_up_at=datetime(2026, 10, 8, 0, 0, tzinfo=SHANGHAI))
    new_without_visit = row(business_created_on=datetime(2026, 10, 7).date())
    unknown = row(business_created_on=None)
    assert pool_rows([at_seven, at_six, new_without_visit, unknown], selection, "stale") == [
        at_seven,
        new_without_visit,
    ]


def test_key_collection_includes_won_only_positive_high_same_forecast():
    selection = ReviewSelection(anchor_date="2026-10-14")
    won = row(status="won", probability="100")
    bad = [
        row(status="lost"),
        row(collection_amount="0"),
        row(collection_confidence="low"),
        row(collection_amount=None),
        row(forecast_id=None),
    ]
    assert pool_rows([won, *bad], selection, "key_collection") == [won]


@pytest.mark.parametrize("value, expected", [("100", "expected_achieved"), ("75", "on_track"), ("1", "behind")])
def test_expected_status_uses_one_calendar_progress_and_effective_positive_target(value, expected):
    targets = {"collection": dict(kind="collection", value="100.00", id=str(uuid4()), version_no=1)}
    metric = metric_cards(
        [row(collection_amount=value)], targets, True, year=2026, quarter=4, time_progress="0.50000000"
    )[0]
    assert metric["status"] == expected


def test_zero_target_keeps_real_forecast_without_dividing_by_zero():
    targets = {
        "collection_ppl": dict(
            kind="collection_ppl", value="0.00", id=str(uuid4()), version_no=1, note="北区目标由东区承接"
        )
    }
    card = cards([row(collection_amount="300000.00")], targets)["collection_ppl"]
    assert card["value"] == "300000.00"
    assert card["target"]["status"] == "effective" and card["target"]["value"] == "0.00"
    assert card["target"]["note"] == "北区目标由东区承接"
    assert card["rate"] is None and card["gap"] is None
