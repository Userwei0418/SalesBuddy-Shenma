"""Human amount confirmation is exact, tenant-configurable and independent of AI review."""

from copy import deepcopy
from decimal import Decimal

import pytest

from sales_backend.contracts.models import OpportunityCreate
from sales_backend.contracts.opportunity_candidate import validate_candidate
from sales_backend.contracts.visit_flow import archival_snapshot, reviewed_archival_snapshot
from sales_backend.domain.agent_management import management_metadata
from sales_backend.domain.company_rules import OpportunityAmountPolicy, policy_snapshot, validate_policy
from sales_backend.services.idempotency import receipt_payload


def mutation(**changes):
    return OpportunityCreate(name="合成大额商机", amount="10000000", probability=10,
                             expected_close_date="2026-12-01", **changes).model_dump(mode="json")


def test_default_threshold_and_program_owned_management_metadata():
    snapshot = policy_snapshot("opportunity_amount")
    assert snapshot["definition"] == {"schema_version": 1, "warning_threshold_wan": 1000}
    assert OpportunityAmountPolicy().threshold_yuan == Decimal("10000000")
    assert management_metadata("opportunity_amount", {})["related_agents"] == []


@pytest.mark.parametrize("value", [0, -1, 100_000_001, True, "1000", 1000.0, float("inf")])
def test_threshold_requires_a_bounded_positive_integer(value):
    with pytest.raises(ValueError):
        validate_policy("opportunity_amount", {"warning_threshold_wan": value})


@pytest.mark.parametrize("amount,confirmed", [
    ("9999999.99", None), ("10000000", "10000000.00"), ("1000000000", "1000000000"),
])
def test_confirmed_large_values_are_not_capped_or_rescaled(amount, confirmed):
    value = Decimal(amount)
    OpportunityAmountPolicy().require_confirmation(value, Decimal(confirmed) if confirmed is not None else None)
    assert value == Decimal(amount)


@pytest.mark.parametrize("amount,confirmed", [
    ("10000000", None), ("10000000", "1000"), ("10000001", "10000000"),
])
def test_unconfirmed_or_changed_large_amount_requires_explicit_exact_confirmation(amount, confirmed):
    with pytest.raises(ValueError, match="返回编辑页核对金额及单位"):
        OpportunityAmountPolicy().require_confirmation(Decimal(amount), Decimal(confirmed) if confirmed else None)


def test_legacy_client_amount_error_gives_safe_experience_upgrade_and_authorized_fallback():
    with pytest.raises(ValueError) as error:
        OpportunityAmountPolicy().require_confirmation(Decimal("10000000"), None)
    message = str(error.value)
    assert "10,000,000.00 元" in message
    assert "保留当前填写内容" in message and "新版体验版入口" in message
    assert "有权限的管理员" in message


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0", "10000000.001"])
def test_confirmation_has_same_money_validation_as_amount(value):
    with pytest.raises(ValueError):
        mutation(amount_confirmed_value=value)


def test_final_confirmation_preserves_old_review_compatibility_but_not_amount_changes():
    fields = {"_opportunity_mutation": mutation()}
    reviewed = archival_snapshot(fields)
    assert "amount_confirmed_value" not in reviewed["opportunity_mutation"]
    old_request = deepcopy(reviewed)
    new_request = deepcopy(reviewed)
    new_request["opportunity_mutation"]["amount_confirmed_value"] = "10000000.00"
    fields["_opportunity_mutation"]["amount_confirmed_value"] = "10000000.00"
    assert reviewed_archival_snapshot(old_request) == archival_snapshot(fields)
    assert reviewed_archival_snapshot(new_request) == archival_snapshot(fields)
    assert new_request["opportunity_mutation"]["amount_confirmed_value"] == "10000000.00"
    fields["_opportunity_mutation"]["amount"] = "10000001"
    assert reviewed_archival_snapshot(old_request) != archival_snapshot(fields)


def test_ai_candidate_cannot_create_a_human_confirmation():
    candidate = validate_candidate({"action": "create", "amount": 10000000,
                                    "amount_confirmed_value": 10000000}, {})
    assert "amount_confirmed_value" not in candidate


@pytest.mark.parametrize("operation", ["opportunities.save:customer-id", "opportunity.save:customer-id"])
def test_save_receipt_ignores_only_top_level_ack_without_changing_request(operation):
    old = {"amount": "10000000", "version_no": 3, "customer_id": "customer-id",
           "attributes": {"amount_confirmed_value": "business-data"}}
    incoming = {**deepcopy(old), "amount_confirmed_value": "10000000"}
    original = deepcopy(incoming)
    assert receipt_payload(operation, incoming) == old
    assert incoming == original
    for field, value in (("amount", "10000001"), ("version_no", 4), ("customer_id", "other")):
        assert receipt_payload(operation, {**incoming, field: value}) != old


def test_visit_receipt_ignores_only_nested_opportunity_ack_without_changing_request():
    old = {"customer_id": "customer-id", "amount_confirmed_value": "business-data",
           "fields": {"amount_confirmed_value": "business-data", "_opportunity_mutation": {"amount": "10000000"}}}
    incoming = deepcopy(old)
    incoming["fields"]["_opportunity_mutation"]["amount_confirmed_value"] = "10000000"
    original = deepcopy(incoming)
    assert receipt_payload("visits.archive", incoming) == old
    assert incoming == original


@pytest.mark.parametrize("operation", ["opportunity.quote:id", "visits.supplement:id", "tasks.create",
                                       "opportunities.save", "opportunity.save.extra:id"])
def test_other_receipt_operations_preserve_even_similarly_named_fields(operation):
    incoming = {"amount_confirmed_value": "business-data",
                "fields": {"_opportunity_mutation": {"amount_confirmed_value": "business-data"}}}
    assert receipt_payload(operation, incoming) == incoming
