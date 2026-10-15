from copy import deepcopy
from decimal import Decimal

import pytest
from sales_backend.domain.opportunities import normalize_forecasts, validate_forecast_completeness
from sales_backend.domain.opportunity_finance import financial_projection, financial_overview, quarter_field


def source(**overrides):
    return {**dict(id="o", workspace_id="w", finance_actuals=[], finance_history=[], finance_decisions=[]), **overrides}


def actual(amount, **overrides):
    return {**dict(id="a", opportunity_id="o", kind="recognized", amount=amount, occurred_on="2026-09-30"), **overrides}


def test_explicit_exclusive_basis_units_zero_and_void_clear():
    key = "quarter_2026_q3_recognized_exclusive"
    r = source(finance_actuals=[actual("12345.67", tax_basis="exclusive")])
    assert financial_projection(r, [key])[key] == "1.234567"
    r["finance_actuals"][0]["amount"] = "0"
    assert financial_projection(r, [key])[key] == "0"
    r["finance_actuals"][0]["voided_at"] = "2026-09-30T01:00:00Z"
    assert financial_projection(r, [key])[key] is None


@pytest.mark.parametrize("basis", [None, "unknown", "inclusive"])
def test_unconfirmed_or_inclusive_tax_does_not_become_exclusive(basis):
    r = source(finance_actuals=[actual("100", tax_basis=basis)])
    values = financial_projection(r, ["quarter_2026_q3_recognized_exclusive", "quarter_2026_q3_quality"])
    assert values["quarter_2026_q3_recognized_exclusive"] is None
    assert "税口径待核对" in values["quarter_2026_q3_quality"]
    assert values["actual_recognized_total"] == "100"


def test_year_boundary_collection_and_mixed_tax_never_blend():
    r = source(finance_actuals=[actual("100", tax_basis="exclusive"),
        actual("200", id="b", tax_basis="inclusive"),
        actual("0", id="c", kind="collection", occurred_on="2027-01-01", tax_basis="not_applicable")])
    keys = ["quarter_2026_q3_recognized_exclusive", "quarter_2026_q4_collection", "quarter_2027_q1_collection"]
    values = financial_projection(r, keys)
    assert [values[k] for k in keys] == [None, None, "0"]


def test_unknown_default_keeps_historical_confirmation_fingerprint():
    r = source(finance_actuals=[actual("100")])
    old = financial_overview(r)["periods"][0]["source_fingerprint"]
    r["finance_actuals"][0]["tax_basis"] = "unknown"
    assert financial_overview(r)["periods"][0]["source_fingerprint"] == old
    r["finance_actuals"][0]["tax_basis"] = "exclusive"
    assert financial_overview(r)["periods"][0]["source_fingerprint"] != old


def test_legacy_confidence_preserved_but_v2_changed_plan_requires_it():
    old = [dict(year=2026, quarter=3, recognized_amount=Decimal(0), collection_amount=Decimal(0), collection_confidence=None)]
    unchanged = normalize_forecasts(old, [dict(year=2026, quarter=3, recognized_amount=Decimal(0), collection_amount=Decimal(0))])
    validate_forecast_completeness(dict(status="open", probability=30), unchanged, existing=old, schema_version=2)
    changed = deepcopy(unchanged); changed[0]["collection_amount"] = Decimal(1)
    with pytest.raises(ValueError, match="信心度"):
        validate_forecast_completeness(dict(status="open", probability=30), changed, existing=old, schema_version=2)
    changed[0]["collection_confidence"] = "low"
    validate_forecast_completeness(dict(status="open", probability=30), changed, existing=old, schema_version=2)
    old[0]["collection_confidence"] = "high"
    assert normalize_forecasts(old, [dict(year=2026, quarter=3)])[0]["collection_confidence"] == "high"


@pytest.mark.parametrize("key", ["quarter_1999_q1_collection", "quarter_2101_q1_collection", "quarter_2026_q5_collection", "quarter_2026_q1_arbitrary_sql"])
def test_quarter_mapping_is_a_bounded_whitelist(key):
    assert quarter_field(key) is None
