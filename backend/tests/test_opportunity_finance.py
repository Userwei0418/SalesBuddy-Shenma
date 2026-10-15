import json
from copy import deepcopy
from decimal import Decimal

import pytest
from pydantic import ValidationError

from sales_backend.api.opportunity_finance import SourceConfirmation
from sales_backend.domain.feishu_sync.projection import encode_scalar, project
from sales_backend.domain.opportunity_finance import financial_overview, financial_projection


def raw():
    return {"id": "opp", "workspace_id": "ws", "finance_actuals": [], "finance_history": [], "finance_decisions": []}


def entry(id="a", amount="100", **kwargs):
    return dict(id=id, opportunity_id="opp", kind="recognized", amount=amount, occurred_on="2026-09-29", **kwargs)


def history(id="h", amount="0.01", **kwargs):
    return dict(
        id=id,
        opportunity_id="opp",
        year=2026,
        quarter=3,
        kind="recognized",
        raw_amount=amount,
        source_unit="wan_cny",
        tax_basis="unknown",
        **kwargs,
    )


def decide(r, source="historical", selected="h"):
    period = financial_overview(r)["periods"][0]
    r["finance_decisions"] = [
        {
            "year": 2026,
            "quarter": 3,
            "kind": "recognized",
            "source": source,
            "selected_history_id": selected,
            "source_fingerprint": period["source_fingerprint"],
        }
    ]


def test_missing_and_explicit_zero_are_distinct():
    r = raw()
    assert financial_overview(r)["totals"]["recognized"]["amount"] is None
    r["finance_history"] = [history(amount="0")]
    result = financial_overview(r)
    assert result["totals"]["recognized"]["amount"] == "0"
    assert result["totals"]["collection"]["amount"] is None
    assert result["quality"].startswith("来源明确")


def test_entries_are_independent_events_not_deduped_by_amount():
    r = raw()
    r["finance_actuals"] = [entry(), entry("b"), entry("void", voided_at="2026-09-29T10:00:00Z")]
    result = financial_overview(r)
    assert result["totals"]["recognized"]["amount"] == "200"
    assert result["periods"][0]["status"] == "single_source"


def test_equal_entries_and_history_still_require_confirmation():
    r = raw()
    r["finance_actuals"], r["finance_history"] = [entry()], [history()]
    result = financial_overview(r)
    assert result["totals"]["recognized"]["amount"] is None
    assert result["conflict_period_count"] == 1
    decide(r)
    assert financial_overview(r)["totals"]["recognized"]["amount"] == "100"
    decide(r, "entries", None)
    assert financial_overview(r)["totals"]["recognized"]["amount"] == "100"


def test_multiple_historical_totals_require_one_explicit_record():
    r = raw()
    r["finance_history"] = [history(), history("h2", "9")]
    assert financial_overview(r)["periods"][0]["amount"] is None
    decide(r, selected="h2")
    assert financial_overview(r)["periods"][0]["amount"] == "90000"


@pytest.mark.parametrize("mutation", ["insert", "void", "amount", "unit", "tax", "reference"])
def test_confirmations_expire_for_any_source_evidence_change(mutation):
    r = raw()
    r["finance_actuals"], r["finance_history"] = [entry()], [history()]
    decide(r)
    if mutation == "insert":
        r["finance_actuals"].append(entry("new"))
    elif mutation == "void":
        r["finance_actuals"][0]["voided_at"] = "2026-09-29T10:00:00Z"
    elif mutation == "amount":
        r["finance_actuals"][0]["amount"] = "101"
    elif mutation == "unit":
        r["finance_history"][0]["source_unit"] = "cny"
    elif mutation == "tax":
        r["finance_history"][0]["tax_basis"] = "inclusive"
    else:
        r["finance_actuals"][0]["source_ref"] = "new evidence"
    result = financial_overview(r)
    assert result["periods"][0]["status"] == "stale_confirmation"
    assert result["totals"]["recognized"]["amount"] is None


def test_conflicting_quarter_hides_total_but_retains_separate_known_subtotal():
    r = raw()
    r["finance_actuals"] = [entry(), {**entry("last", "20"), "occurred_on": "2025-12-31"}]
    r["finance_history"] = [history()]
    result = financial_overview(r)
    assert result["totals"]["recognized"]["amount"] is None
    assert result["totals"]["recognized"]["confirmed_subtotal"] == "20"
    assert result["totals"]["collection"]["amount"] is None


def test_customer_only_and_other_opportunity_entries_are_never_distributed():
    r = raw()
    r["finance_actuals"] = [{**entry(), "opportunity_id": None}, {**entry("b"), "opportunity_id": "other"}]
    assert financial_overview(r)["periods"] == []


def test_void_only_does_not_override_history_with_zero():
    r = raw()
    r["finance_actuals"] = [entry(voided_at="2026-09-29T10:00:00Z")]
    r["finance_history"] = [history()]
    assert financial_overview(r)["periods"][0]["amount"] == "100"
    decide(r, "entries", None)
    assert financial_overview(r)["periods"][0]["status"] == "stale_confirmation"


def test_fingerprint_stable_across_decimal_scale_order_timezone_and_json():
    r = raw()
    r["finance_actuals"] = [
        entry(amount=Decimal("99999999999999.99"), created_at="2026-09-29T08:00:00+08:00"),
        entry("b", "0.0100"),
    ]
    r["finance_history"] = [history(amount=Decimal("987654321.123456"))]
    alternate = deepcopy(r)
    alternate["finance_actuals"].reverse()
    alternate["finance_actuals"][1]["created_at"] = "2026-09-29T00:00:00Z"
    alternate["finance_actuals"][0]["amount"] = "0.01"
    alternate = json.loads(json.dumps(alternate, default=str))
    a, b = financial_overview(r), financial_overview(alternate)
    assert a["periods"][0]["source_fingerprint"] == b["periods"][0]["source_fingerprint"]
    assert a["periods"][0]["entry_amount"] == "100000000000000"
    assert a["periods"][0]["history"][0]["amount_yuan"] == "9876543211234.56"


def test_projection_keeps_clear_null_and_serializable_exact_money():
    r = raw()
    r["finance_history"] = [history()]
    projected = project("opportunity", r, set(financial_projection(r)))
    assert projected["actual_recognized_total"] == "100"
    json.dumps(projected)
    r["finance_actuals"] = [entry()]
    projected = project("opportunity", r, set(financial_projection(r)))
    assert projected["actual_recognized_total"] is None
    assert encode_scalar(projected["actual_recognized_total"], 2) is None


def test_not_applicable_collection_tax_is_not_unknown():
    r = raw()
    r["finance_history"] = [{**history(), "kind": "collection", "tax_basis": "not_applicable"}]
    assert "税" not in financial_overview(r)["quality"]


def test_confirmation_accepts_choice_only_not_client_money_or_actor():
    body = dict(
        year=2026,
        quarter=3,
        kind="recognized",
        source="entries",
        expected_fingerprint="a" * 64,
        expected_version=0,
        reason="checked",
    )
    SourceConfirmation(**body)
    for additional in (
        {"amount": 100},
        {"workspace_id": "other"},
        {"decided_by_user_ref_id": "other"},
        {"reason": "  "},
    ):
        with pytest.raises(ValidationError):
            SourceConfirmation(**(body | additional))
    with pytest.raises(ValidationError):
        SourceConfirmation(**(body | {"source": "historical"}))
