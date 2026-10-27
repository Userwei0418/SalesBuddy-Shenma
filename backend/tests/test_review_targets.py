from decimal import Decimal

import pytest
from pydantic import ValidationError

from sales_backend.contracts.review import ReviewMetricCreate, ReviewMetricUpdate, ReviewTargetSave
from sales_backend.contracts.targets import TargetSave
from sales_backend.domain.route_permissions import route_permission
from sales_backend.main import app
from sales_backend.services.review import money_strings
from sales_backend.services.targets import normalize_imported_target_amounts


def body(**values):
    return dict(anchor_date="2026-10-14", period_type="quarter", kind="collection",
                amount="1234567890123456.78", reason="季度计划", **values)


def test_legacy_target_body_is_byte_shape_unchanged_and_new_kinds_additive():
    assert ReviewTargetSave(**body()).model_dump(mode="json") == TargetSave(**body()).model_dump(mode="json")
    # PPL reserve is now a first-class target kind; only unknown review-only
    # metric codes remain rejected by the target-management contract.
    assert TargetSave(**{**body(), "kind": "collection_ppl"}).kind == "collection_ppl"
    for kind in ("forecast_recognized", "custom_events"):
        data = {**body(), "kind": kind}
        assert ReviewTargetSave(**data).kind == kind
        with pytest.raises(ValidationError):
            TargetSave(**data)


@pytest.mark.parametrize("extra", [
    {"amount": "NaN"}, {"amount": "-2"}, {"amount": "1.001"},
    {"version_no": 0}, {"workspace_id": "fake"}, {"approved": True},
    {"kind": "custom_sql;"}, {"reason": " "}, {"scope": "person"},
])
def test_review_target_cannot_inject_tenant_approval_or_invalid_money(extra):
    with pytest.raises(ValidationError):
        ReviewTargetSave(**{**body(), **extra})


def test_custom_dictionary_is_manual_and_system_definitions_not_mutable():
    data = dict(code="custom_events", name=" 活动 ", unit="次", decimal_places=0, definition="完成且已确认")
    assert ReviewMetricCreate(**data).name == "活动"
    for extra in ({"source_kind": "automatic"}, {"code": "recognized"}, {"name": " "}, {"decimal_places": 3}):
        with pytest.raises(ValidationError):
            ReviewMetricCreate(**{**data, **extra})
    with pytest.raises(ValidationError):
        ReviewMetricUpdate(active=True, version_no=1, unit="含税元")


def test_custom_precision_is_not_inferred_from_code_suffix():
    for code in ("custom_invoice_count", "custom_invoice_amount"):
        assert ReviewTargetSave(**{**body(), "kind": code, "amount": "123.45"}).amount == Decimal("123.45")
    for code in ("opportunity_count", "visit_count", "demo_count"):
        with pytest.raises(ValidationError):
            ReviewTargetSave(**{**body(), "kind": code, "amount": "123.45"})


def test_custom_metric_still_uses_legacy_scope_and_period_validation():
    for extra in ({"scope": "person"}, {"scope": "team"}, {"department_code": "fde"},
                  {"anchor_date": "1999-01-01"}):
        with pytest.raises(ValidationError):
            ReviewTargetSave(**{**body(), "kind": "custom_invoice_count", **extra})


def test_target_projection_keeps_null_pending_and_exact_decimal_separate():
    row = {"effective_amount": None, "pending_change": {"proposed_amount": "9999999999999999.99"},
           "another": Decimal("0")}
    value = money_strings(row)
    assert value["effective_amount"] is None
    assert value["pending_change"]["proposed_amount"] == "9999999999999999.99"
    assert value["another"] == "0"


def test_revision54_import_is_normalized_once_at_the_read_boundary():
    result = {"items": [
        {"source": "Q4目标表 revision54 导入。", "amount": "500", "amount_text": "500",
         "effective_amount": "500", "pending_change": {"proposed_amount": "600"}},
        {"source": "operations", "change_reason": "Q4目标表 revision54 导入。", "amount": "200"},
        {"source": "手工维护", "amount": "500"},
    ]}
    normalize_imported_target_amounts(result)
    assert result["items"][0]["amount"] == "5000000.00"
    assert result["items"][0]["amount_text"] == "5000000.00"
    assert result["items"][0]["effective_amount"] == "5000000.00"
    assert result["items"][0]["pending_change"]["proposed_amount"] == "6000000.00"
    assert result["items"][1]["amount"] == "2000000.00"
    assert result["items"][2]["amount"] == "500"


def test_review_routes_registered_and_have_explicit_permission_classification():
    paths = app.openapi()["paths"]
    schema = paths["/api/v1/review/targets"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("/ReviewTargetSave")
    for handler in ("metrics", "create_metric", "update_metric", "targets", "save_target", "target_options"):
        assert route_permission("review", handler)
    assert "/api/v1/review/targets/options" in paths


@pytest.mark.parametrize("kind", ["collection", "collection_ppl"])
def test_explicit_zero_requires_reason_and_remains_a_saved_value(kind):
    saved = ReviewTargetSave(**{**body(), "kind": kind, "amount": "0", "reason": "北区目标由东区承接"})
    assert saved.amount == Decimal(0)
    with pytest.raises(ValidationError):
        ReviewTargetSave(**{**body(), "kind": "custom_events", "amount": "0"})
