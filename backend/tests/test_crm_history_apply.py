from copy import deepcopy
from uuid import uuid4

import pytest

from sales_backend.services.crm_history_apply import CrmHistoryApply, ImportBlocked, intent_hash, validate_manifest
from sales_backend.services.crm_history_preflight import canonical_hash


def entry(kind, record, normalized, **overrides):
    raw = {"fixture": record}
    return {
        "kind": kind,
        "source_key": f"feishu:synthetic:{kind}:{record}",
        "source_record_id": record,
        "source_hash": canonical_hash(raw),
        "raw_fields": raw,
        "normalized": normalized,
        "action": "create",
        "can_execute": True,
        "target_id": None,
        "expected_target_version": None,
        "changes": {},
        "additive": {},
        "issues": [],
        "related_objects": {},
        **overrides,
    }


def manifest(workspace, form, records):
    return {
        "schema_version": 1,
        "source_snapshot_at": "2026-09-22T12:00:00+08:00",
        "plan": {"schema_version": 1, "workspace_id": str(workspace), "records": records},
        "execution_contract": {
            "version": "synthetic-v1",
            "data_kind": "test",
            "financial_year": 2026,
            "visit_form_version_id": str(form),
            "stage_map": {"Won": {"stage_code": "won", "status": "won", "probability": 100}},
            "allow_period_actual_snapshot_create": True,
            "allow_verified_legacy_forecast_update": True,
        },
    }


def test_manifest_requires_exact_approval_workspace_and_source_evidence():
    workspace = uuid4()
    doc = manifest(workspace, uuid4(), [entry("customers", "c", {"name": "Synthetic", "relations": {}})])
    validate_manifest(doc, workspace_id=workspace, approved_sha256=canonical_hash(doc))
    with pytest.raises(ImportBlocked, match="hash"):
        validate_manifest(doc, workspace_id=workspace, approved_sha256="0" * 64)
    with pytest.raises(ImportBlocked, match="workspace"):
        validate_manifest(doc, workspace_id=uuid4(), approved_sha256=canonical_hash(doc))
    doc["plan"]["records"][0]["raw_fields"]["unreviewed"] = True
    with pytest.raises(ImportBlocked, match="evidence_hash"):
        validate_manifest(doc, workspace_id=workspace, approved_sha256=canonical_hash(doc))


def test_intent_dedup_ignores_inventory_and_action_but_includes_handover_decision():
    original = entry("opportunities", "o", {"current_owner_account": "A"})
    copy = deepcopy(original)
    copy.update(
        action="noop", expected_target_version=9, changes={"current_owner_account": {"before": "B", "after": "A"}}
    )
    assert intent_hash(original, {"version": "v1"}) == intent_hash(copy, {"version": "v1"})
    copy["normalized"]["current_owner_account"] = "B"
    assert intent_hash(original, {"version": "v1"}) != intent_hash(copy, {"version": "v1"})


def test_quarter_children_keep_unknown_zero_tax_and_stable_source_items():
    service = CrmHistoryApply(None, workspace_id=uuid4(), actor_user_id=uuid4(), actor_role="operations")
    row = entry(
        "opportunities",
        "o",
        {
            "financials": [
                {
                    "record_kind": "forecast",
                    "year": 2026,
                    "quarter": 3,
                    "kind": "collection",
                    "source_field": "Q3",
                    "raw_amount": "0",
                    "amount_cny": "0",
                    "source_unit": "wan_cny",
                    "tax_basis": "unknown",
                },
                {
                    "record_kind": "period_actual_snapshot",
                    "year": 2026,
                    "quarter": 2,
                    "kind": "recognized",
                    "source_field": "Q2",
                    "raw_amount": "1.2",
                    "amount_cny": "12000",
                    "source_unit": "wan_cny",
                    "tax_basis": "unknown",
                },
            ],
            "collection_confidence": {"3": "low", "4": None},
        },
    )
    children = list(service._children(row, {"financial_year": 2026, "allow_period_actual_snapshot_create": True}))
    assert len(children) == 2
    assert children[0]["normalized"]["recognized_amount"] is None
    assert children[0]["normalized"]["collection_amount"] == "0"
    assert children[1]["normalized"]["tax_basis"] == "unknown"
    assert children[0]["target_id"] != children[1]["target_id"]
    row["action"] = "update"
    children = list(service._children(row, {"financial_year": 2026, "allow_period_actual_snapshot_create": True}))
    assert not children[0]["can_execute"] and children[1]["can_execute"]


def test_legacy_forecast_cannot_use_different_current_as_import_baseline():
    service = CrmHistoryApply(None, workspace_id=uuid4(), actor_user_id=uuid4(), actor_role="operations")
    row = entry("opportunities", "o", {"financials": [], "collection_confidence": {"3": "high"}}, action="update")
    row["related_objects"] = {
        "forecast_reconciliation": [
            {
                "year": 2026,
                "quarter": 3,
                "target_id": str(uuid4()),
                "current_snapshot": {"recognized_amount": 2, "collection_amount": 0},
                "last_imported_snapshot": {"recognized_amount": 1, "collection_amount": 0},
            }
        ]
    }
    with pytest.raises(ImportBlocked, match="baseline_mismatch"):
        list(service._children(row, {"financial_year": 2026, "allow_verified_legacy_forecast_update": True}))


def test_new_forecast_reconciliation_does_not_treat_null_snapshot_as_existing_baseline():
    service = CrmHistoryApply(None, workspace_id=uuid4(), actor_user_id=uuid4(), actor_role="operations")
    row = entry("opportunities", "o", {"financials": [], "collection_confidence": {"3": "high"}})
    row["related_objects"] = {
        "forecast_reconciliation": [
            {
                "year": 2026,
                "quarter": 3,
                "action": "create",
                "target_id": None,
                "current_snapshot": None,
                "last_imported_snapshot": None,
            }
        ]
    }
    children = list(service._children(row, {"financial_year": 2026}))
    assert len(children) == 1 and children[0]["expected_snapshot"] is None
    assert children[0]["target_id"] is not None and children[0]["can_execute"]
    row["can_execute_children"] = False
    assert not list(service._children(row, {"financial_year": 2026}))[0]["can_execute"]
