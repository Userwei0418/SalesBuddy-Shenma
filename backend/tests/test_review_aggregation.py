from copy import deepcopy

import pytest

from sales_backend.domain.review_aggregation import aggregate_targets


def subject(identifier, value, *, kind="collection", scope_type="team", allowed=True):
    targets = {} if value is None else {
        kind: dict(id="target-" + identifier + "-" + kind, version_no=1, value=value)
    }
    return dict(scope_type=scope_type, subject_id=identifier, allowed=allowed, targets=targets)


def test_distinct_subject_denominator_is_sum_and_is_order_independent():
    a, b = subject("a", "100.00"), subject("b", "300.00")
    result = aggregate_targets([b, a, a])
    assert result == aggregate_targets([a, b])
    assert result["collection"]["value"] == "400.00"
    assert result["collection"]["id"] is None
    assert len(result["collection"]["components"]) == 2


def test_separate_ppl_denominator_never_uses_collection():
    a, b = subject("a", "100"), subject("b", "300")
    a["targets"]["collection_ppl"] = subject("a", "900", kind="collection_ppl")["targets"]["collection_ppl"]
    b["targets"]["collection_ppl"] = subject("b", "100", kind="collection_ppl")["targets"]["collection_ppl"]
    result = aggregate_targets([a, b])
    assert result["collection"]["value"] == "400.00"
    assert result["collection_ppl"]["value"] == "1000.00"
    assert result["forecast_recognized"]["value"] is None


def test_missing_target_is_partial_without_promoting_known_subtotal():
    result = aggregate_targets([subject("a", "100"), subject("b", None)])["collection"]
    assert result["value"] is None and result["known_subtotal"] == "100.00"
    assert result["coverage"] == "partial" and result["status"] == "no_target"


def test_denied_target_presence_and_values_never_leak():
    allowed = subject("a", "100")
    denied = subject("b", "987654321", allowed=False)
    empty_denied = subject("b", None, allowed=False)
    assert aggregate_targets([allowed, denied]) == aggregate_targets([allowed, empty_denied])
    target = aggregate_targets([allowed, denied])["collection"]
    assert target["status"] == "permission_denied" and target["value"] is None
    assert target["components"][1]["target_id"] is None


def test_exact_decimal_sum_and_explicit_zero_remain_distinct_from_missing():
    result = aggregate_targets([subject("a", "9999999999999999.99"), subject("b", "0.01")])["collection"]
    assert result["value"] == "10000000000000000.00"
    zero = aggregate_targets([subject("a", "0.00")])["collection"]
    missing = aggregate_targets([subject("a", None)])["collection"]
    assert zero["value"] == "0.00" and zero["coverage"] == "complete"
    assert missing["value"] is None and missing["known_subtotal"] is None


def test_mixed_types_conflicting_duplicate_and_reused_target_rejected():
    a = subject("a", "100")
    for entries in ([], [a, subject("b", "200", scope_type="person")],
                    [a, subject("a", "101")]):
        with pytest.raises(ValueError):
            aggregate_targets(entries)
    b = deepcopy(a)
    b["subject_id"] = "b"
    with pytest.raises(ValueError, match="复用"):
        aggregate_targets([a, b])


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1"])
def test_invalid_effective_target_amount_is_not_aggregated(bad):
    with pytest.raises(ValueError):
        aggregate_targets([subject("a", bad)])
