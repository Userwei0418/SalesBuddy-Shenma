from copy import deepcopy
from datetime import date

import pytest

from sales_backend.services.crm_history_preflight import (
    KINDS,
    SnapshotError,
    build_preflight,
    calendar_month_start,
    canonical_hash,
    source_key,
)


def frozen(rows=None):
    rows = rows or {
        "customers": [{"record_id": "cust1", "fields": {"客户名称": "Example Customer", "商机列表": []}}],
        "partners": [],
        "opportunities": [{"record_id": "opp1", "fields": {
            "商机名称": "Example Project", "商机状态": ["In progress"], "商机销售": [{"name": "Alice"}],
            "客户名称": [{"id": "cust1"}], "所属伙伴": [], "跟进记录": [],
            "预计关单日期": ["Q4"], "ACV（万元）": "2.5", "Q2真实确收": "0",
            "Q1含税确收（万元）": "1.25", "Q3 预测含税确收（万元）": "",
            "Q4预测回款（万元）": "3", "Q4可回款信心度": ["高(>=70%)"],
        }}],
        "visits": [{"record_id": "visit1", "fields": {
            "跟进人": [{"name": "Former Person"}], "商机名称": [{"id": "opp1"}],
            "伙伴名称": [], "跟进日期": "2025-12-31", "沟通内容": "Original follow-up",
            "创建时间": "2026-09-22", "创建人": [{"name": "Importer"}],
        }}],
    }
    result = {
        "schema_version": 1, "workspace_id": "workspace-one", "base_token": "base-one",
        "as_of": "2026-09-22", "financial_year": 2026,
        "accounts": {"Alice": "ALICE", "Bob": "BOB"}, "tables": {},
        "execution_contract": {"stage_map": {"In progress": {
            "stage_code": "identified", "status": "open", "probability": 10,
        }}},
    }
    for kind in KINDS:
        values = rows.get(kind, [])
        result["tables"][kind] = {
            "table_id": kind, "complete": True, "expected_count": len(values),
            "visible_fields": list(dict.fromkeys(key for row in values for key in row["fields"])) or ["伙伴名称"],
            "records": values, "records_sha256": canonical_hash(values),
        }
    return result


def refresh(snapshot, kind):
    table = snapshot["tables"][kind]
    table["expected_count"] = len(table["records"])
    table["records_sha256"] = canonical_hash(table["records"])


def by_id(plan, record_id):
    return next(row for row in plan["records"] if row["source_record_id"] == record_id)


def empty_inventory():
    return {"workspace_id": "workspace-one", "complete": True, "objects": {}}


def existing(snapshot, baseline=None, current=None, direct=True):
    row = by_id(build_preflight(snapshot, empty_inventory()), "cust1")
    values = deepcopy(current if current is not None else row["normalized"])
    obj = {"id": "system-customer", "version": 8, "values": values,
           "source_keys": [row["source_key"]] if direct else [],
           "last_imported_values": deepcopy(values if baseline is None else baseline)}
    return {"workspace_id": "workspace-one", "complete": True, "objects": {"customers": [obj]}}


def test_original_values_dates_unknown_tax_and_zero_are_preserved():
    plan = build_preflight(frozen(), empty_inventory())
    assert plan["business_data_written"] is False
    opportunity = by_id(plan, "opp1")["normalized"]
    assert opportunity["amount"] == "25000.0"
    assert opportunity["expected_close_date"] is None
    assert opportunity["expected_close_quarter"] == 4
    assert opportunity["collection_confidence"]["4"] == "high"
    financials = {r["source_field"]: r for r in opportunity["financials"]}
    assert "Q3 预测含税确收（万元）" not in financials
    assert financials["Q2真实确收"]["raw_amount"] == "0"
    assert financials["Q2真实确收"]["tax_basis"] == "unknown"
    assert financials["Q2真实确收"]["record_kind"] == "period_actual_snapshot"
    assert financials["Q1含税确收（万元）"]["tax_basis"] == "inclusive"
    visit = by_id(plan, "visit1")["normalized"]
    assert visit["interaction_on"] == "2025-12-31"
    assert visit["recorded_on"] == "2026-09-22"
    assert visit["original_recorder_name"] == "Former Person"
    assert visit["original_recorder_account"] is None
    assert visit["transfer_personal_counts"] is False


def test_provisional_owner_is_not_rewritten_as_original_source_owner():
    snapshot = frozen()
    snapshot["tables"]["opportunities"]["records"][0]["fields"]["商机销售"] = []
    refresh(snapshot, "opportunities")
    snapshot["owner_decisions"] = {"opp1": {
        "current_owner_account": "BOB", "is_provisional": True, "evidence": "user-confirmed temporary handover",
    }}
    normalized = by_id(build_preflight(snapshot, empty_inventory()), "opp1")["normalized"]
    assert normalized["current_owner_account"] == "BOB"
    assert normalized["original_owner_name"] is None
    assert normalized["source_owner_field_empty"] is True
    assert normalized["ownership_resolution"] == "provisional"


@pytest.mark.parametrize("corruption", ["count", "checksum", "duplicate_id", "incomplete"])
def test_incomplete_or_tampered_snapshot_fails_closed(corruption):
    snapshot = frozen()
    table = snapshot["tables"]["customers"]
    if corruption == "count":
        table["expected_count"] = 17852
    elif corruption == "checksum":
        table["records"][0]["fields"]["客户名称"] = "Changed"
    elif corruption == "duplicate_id":
        table["records"].append(deepcopy(table["records"][0]))
        refresh(snapshot, "customers")
    else:
        table["complete"] = False
    with pytest.raises(SnapshotError):
        build_preflight(snapshot, empty_inventory())


def test_hidden_fields_do_not_enter_business_snapshot():
    snapshot = frozen()
    snapshot["tables"]["customers"]["records"][0]["fields"]["隐藏财务公式"] = "sensitive hidden computation"
    refresh(snapshot, "customers")
    row = by_id(build_preflight(snapshot, empty_inventory()), "cust1")
    assert "隐藏财务公式" not in row["raw_fields"]


def test_duplicate_names_do_not_merge_or_duplicate_existing_business_data():
    snapshot = frozen()
    table = snapshot["tables"]["customers"]
    duplicate = deepcopy(table["records"][0])
    duplicate["record_id"] = "cust2"
    table["records"].append(duplicate)
    refresh(snapshot, "customers")
    plan = build_preflight(snapshot, empty_inventory())
    assert len([r for r in plan["records"] if r["kind"] == "customers"]) == 2
    assert by_id(plan, "cust1")["action"] == "quarantine"
    assert by_id(plan, "cust2")["action"] == "quarantine"
    assert by_id(plan, "opp1")["action"] == "quarantine"
    assert by_id(plan, "visit1")["action"] == "quarantine"


def test_existing_same_name_without_bridge_is_reviewed_not_created():
    snapshot = frozen()
    plan = build_preflight(snapshot, existing(snapshot, direct=False))
    row = by_id(plan, "cust1")
    assert row["action"] == "quarantine"
    assert row["candidate_target_ids"] == ["system-customer"]


def test_exact_existing_source_is_noop():
    snapshot = frozen()
    row = by_id(build_preflight(snapshot, existing(snapshot)), "cust1")
    assert row["action"] == "noop"
    assert row["expected_target_version"] == 8


def test_source_change_updates_only_when_current_value_matches_import_baseline():
    snapshot = frozen()
    inventory = existing(snapshot)
    snapshot["tables"]["customers"]["records"][0]["fields"]["客户名称"] = "Updated Source Customer"
    refresh(snapshot, "customers")
    row = by_id(build_preflight(snapshot, inventory), "cust1")
    assert row["action"] == "update"
    assert row["changes"]["name"] == {"before": "Example Customer", "after": "Updated Source Customer"}
    inventory["objects"]["customers"][0]["values"]["name"] = "User Edited Name"
    row = by_id(build_preflight(snapshot, inventory), "cust1")
    assert row["action"] == "conflict"
    assert row["can_execute"] is False


def test_unknown_baseline_never_overwrites_even_current_empty_value():
    snapshot = frozen()
    inventory = existing(snapshot, baseline={}, current={"name": None})
    row = by_id(build_preflight(snapshot, inventory), "cust1")
    assert row["action"] == "conflict"
    assert "current_business_value_protected:name" in row["issues"]


def test_legacy_alias_requires_evidence_and_locks_target_version():
    snapshot = frozen()
    inventory = existing(snapshot, direct=False)
    snapshot["legacy_aliases"] = [{
        "source_key": source_key("feishu", "base-one", "customers", "cust1"),
        "kind": "customers", "target_id": "system-customer", "expected_target_version": 8,
        "approved": True, "evidence": "old source hash and exact immutable record crosswalk",
    }]
    assert by_id(build_preflight(snapshot, inventory), "cust1")["action"] == "noop"
    snapshot["legacy_aliases"][0]["expected_target_version"] = 7
    assert by_id(build_preflight(snapshot, inventory), "cust1")["action"] == "conflict"
    snapshot["legacy_aliases"][0]["approved"] = False
    with pytest.raises(SnapshotError):
        build_preflight(snapshot, inventory)


def test_cross_tenant_inventory_is_rejected():
    inventory = empty_inventory()
    inventory["workspace_id"] = "other-company"
    with pytest.raises(SnapshotError):
        build_preflight(frozen(), inventory)


def test_unknown_owner_and_unresolved_subject_stay_in_quarantine():
    snapshot = frozen()
    fields = snapshot["tables"]["opportunities"]["records"][0]["fields"]
    fields["商机销售"] = [{"name": "Unknown"}]
    fields["客户名称"] = [{"id": "missing-customer"}]
    refresh(snapshot, "opportunities")
    row = by_id(build_preflight(snapshot, empty_inventory()), "opp1")
    assert row["action"] == "quarantine"
    assert "unresolved_current_owner" in row["issues"]
    assert any("missing_source_relation" in issue for issue in row["issues"])


def test_known_subject_mismatch_is_preserved_without_silently_repairing_it():
    snapshot = frozen()
    snapshot["quarantine"] = {"visit1": ["operator_review_subject_mismatch"]}
    row = by_id(build_preflight(snapshot, empty_inventory()), "visit1")
    assert row["action"] == "quarantine"
    assert row["raw_fields"]["商机名称"] == [{"id": "opp1"}]
    assert row["normalized"]["follow_up_record"] == "Original follow-up"


def test_multiple_opportunities_keep_one_followup():
    snapshot = frozen()
    opp = deepcopy(snapshot["tables"]["opportunities"]["records"][0])
    opp["record_id"] = "opp2"
    snapshot["tables"]["opportunities"]["records"].append(opp)
    refresh(snapshot, "opportunities")
    snapshot["tables"]["visits"]["records"][0]["fields"]["商机名称"] = [{"id": "opp1"}, {"id": "opp2"}]
    refresh(snapshot, "visits")
    plan = build_preflight(snapshot, empty_inventory())
    assert len([row for row in plan["records"] if row["kind"] == "visits"]) == 1
    assert by_id(plan, "visit1")["normalized"]["relations"]["opportunities"] == ["opp1", "opp2"]


def test_calendar_month_window_clamps_month_end():
    assert calendar_month_start(date(2026, 9, 22)) == date(2026, 3, 22)
    assert calendar_month_start(date(2026, 8, 31)) == date(2026, 2, 28)
    assert calendar_month_start(date(2024, 8, 31)) == date(2024, 2, 29)


@pytest.mark.parametrize("inventory", [None, {"workspace_id": "workspace-one", "objects": {}}])
def test_missing_or_partial_inventory_cannot_authorize_new_records(inventory):
    with pytest.raises(SnapshotError):
        build_preflight(frozen(), inventory)


def test_visit_management_uses_current_opportunity_owner_without_changing_author():
    row = by_id(build_preflight(frozen(), empty_inventory()), "visit1")["normalized"]
    assert row["manager_account"] == "ALICE"
    assert row["original_recorder_name"] == "Former Person"
    assert row["relations"]["customers"] == ["cust1"]


def test_future_followup_is_quarantined_instead_of_activating_customer():
    snapshot = frozen()
    snapshot["tables"]["visits"]["records"][0]["fields"]["跟进日期"] = "2026-09-23"
    refresh(snapshot, "visits")
    row = by_id(build_preflight(snapshot, empty_inventory()), "visit1")
    assert row["action"] == "quarantine"
    assert "followup_date_after_snapshot" in row["issues"]


def test_two_source_records_cannot_cover_the_same_existing_record():
    snapshot = frozen()
    inventory = existing(snapshot)
    copied = deepcopy(snapshot["tables"]["customers"]["records"][0])
    copied["record_id"] = "cust2"
    copied["fields"]["客户名称"] = "Other Name"
    snapshot["tables"]["customers"]["records"].append(copied)
    refresh(snapshot, "customers")
    snapshot["legacy_aliases"] = [{
        "source_key": source_key("feishu", "base-one", "customers", "cust2"),
        "kind": "customers", "target_id": "system-customer", "expected_target_version": 8,
        "approved": True, "evidence": "mistaken crosswalk caught by preflight",
    }]
    plan = build_preflight(snapshot, inventory)
    for record_id in ("cust1", "cust2"):
        assert by_id(plan, record_id)["action"] == "conflict"
        assert "multiple_source_records_target_one_entity" in by_id(plan, record_id)["issues"]


def test_management_change_is_part_of_the_protected_three_way_comparison():
    snapshot = frozen()
    normalized = by_id(build_preflight(snapshot, empty_inventory()), "visit1")["normalized"]
    inventory = empty_inventory()
    inventory["objects"]["visits"] = [{
        "id": "system-visit", "version": 2,
        "source_keys": [source_key("feishu", "base-one", "visits", "visit1")],
        "values": deepcopy(normalized), "last_imported_values": deepcopy(normalized),
    }]
    snapshot["owner_decisions"] = {"opp1": {
        "current_owner_account": "BOB", "is_provisional": True, "evidence": "approved handover",
    }}
    row = by_id(build_preflight(snapshot, inventory), "visit1")
    assert row["action"] == "update"
    assert row["changes"]["manager_account"] == {"before": "ALICE", "after": "BOB"}
    inventory["objects"]["visits"][0]["values"]["manager_account"] = "SOMEONE_ELSE"
    row = by_id(build_preflight(snapshot, inventory), "visit1")
    assert row["action"] == "conflict"


def test_legacy_short_name_is_a_review_hint_when_current_business_name_is_full_name():
    snapshot = frozen()
    inventory = existing(snapshot, direct=False)
    inventory["objects"]["customers"][0]["values"]["name"] = "Example Customer Incorporated"
    inventory["objects"]["customers"][0]["candidate_names"] = ["Example Customer"]
    row = by_id(build_preflight(snapshot, inventory), "cust1")
    assert row["action"] == "quarantine"
    assert row["candidate_target_ids"] == ["system-customer"]


def test_reverse_relations_are_evidence_not_a_customer_overwrite():
    snapshot = frozen()
    inventory = existing(snapshot, current={"name": "Example Customer"}, baseline={"name": "Example Customer"})
    row = by_id(build_preflight(snapshot, inventory), "cust1")
    assert row["action"] == "noop"
    assert row["evidence_only"]["relations.opportunities"] == []
    assert "relations" not in row["changes"]


def test_schema_proven_additive_metadata_does_not_require_fabricated_business_baseline():
    snapshot = frozen()
    normalized = by_id(build_preflight(snapshot, empty_inventory()), "opp1")["normalized"]
    new_fields = ["original_owner_name", "ownership_resolution", "ownership_resolution_evidence",
                  "expected_close_year", "expected_close_quarter"]
    values = {field: value for field, value in normalized.items() if field not in new_fields}
    inventory = empty_inventory()
    inventory["schema"] = {
        "evidence_sha256": "schema-evidence", "verified_absent_fields": {"opportunities": new_fields},
    }
    inventory["objects"]["opportunities"] = [{
        "id": "system-opp", "version": 1,
        "source_keys": [source_key("feishu", "base-one", "opportunities", "opp1")],
        "values": values, "last_imported_values": {}, "absent_fields": new_fields,
    }]
    row = by_id(build_preflight(snapshot, inventory), "opp1")
    assert row["action"] == "update"
    assert set(row["additive"]) == set(new_fields)
    assert row["changes"] == {}
    assert row["related_objects"]["financials"]
    assert row["can_execute_children"] is False
    inventory["objects"]["opportunities"][0]["absent_fields"].append("amount")
    inventory["schema"]["verified_absent_fields"]["opportunities"].append("amount")
    with pytest.raises(SnapshotError):
        build_preflight(snapshot, inventory)


def test_archived_body_change_needs_review_even_when_current_equals_old_import():
    snapshot = frozen()
    normalized = by_id(build_preflight(snapshot, empty_inventory()), "visit1")["normalized"]
    inventory = empty_inventory()
    inventory["objects"]["visits"] = [{
        "id": "system-visit", "version": 1,
        "source_keys": [source_key("feishu", "base-one", "visits", "visit1")],
        "values": deepcopy(normalized), "last_imported_values": deepcopy(normalized),
    }]
    snapshot["tables"]["visits"]["records"][0]["fields"]["沟通内容"] = "Revised old story"
    refresh(snapshot, "visits")
    row = by_id(build_preflight(snapshot, inventory), "visit1")
    assert row["action"] == "conflict"
    assert "historical_record_requires_correction_review:follow_up_record" in row["issues"]
    assert "follow_up_record" not in row["changes"]


def test_historical_contact_snapshot_preserves_outer_spaces_and_blocks_changed_person():
    snapshot = frozen()
    snapshot["tables"]["visits"]["records"][0]["fields"]["对接人"] = "Contact Person"
    snapshot["tables"]["visits"]["visible_fields"].append("对接人")
    refresh(snapshot, "visits")
    normalized = by_id(build_preflight(snapshot, empty_inventory()), "visit1")["normalized"]
    values = {**deepcopy(normalized), "contact_name_snapshot": " Contact Person "}
    inventory = empty_inventory()
    inventory["objects"]["visits"] = [{
        "id": "system-visit", "version": 1,
        "source_keys": [source_key("feishu", "base-one", "visits", "visit1")],
        "values": values, "last_imported_values": deepcopy(values),
    }]
    row = by_id(build_preflight(snapshot, inventory), "visit1")
    assert row["preserve"]["contact_name_snapshot"] == "boundary_whitespace_preserve_existing"
    assert "contact_name_snapshot" not in row["changes"]
    snapshot["tables"]["visits"]["records"][0]["fields"]["对接人"] = "Another Person"
    refresh(snapshot, "visits")
    row = by_id(build_preflight(snapshot, inventory), "visit1")
    assert "historical_record_requires_correction_review:contact_name_snapshot" in row["issues"]


def test_empty_source_partner_does_not_clear_existing_relation():
    snapshot = frozen()
    normalized = by_id(build_preflight(snapshot, empty_inventory()), "opp1")["normalized"]
    values = deepcopy(normalized)
    values["relations"]["partners"] = ["existing-source-partner"]
    inventory = empty_inventory()
    inventory["objects"]["opportunities"] = [{
        "id": "system-opp", "version": 1,
        "source_keys": [source_key("feishu", "base-one", "opportunities", "opp1")],
        "values": values, "last_imported_values": deepcopy(values),
    }]
    row = by_id(build_preflight(snapshot, inventory), "opp1")
    assert row["related_objects"]["associated_partner_source_ids"] == []
    assert "relations.partners" not in row["changes"]


@pytest.mark.parametrize("names,expected", [
    ([{"name": "Alice"}], "ALICE"),
    ([{"name": "Alice"}, {"name": "Former Person"}], None),
    ([{"name": "Former Person"}], None),
    ([], None),
])
def test_customer_owner_restoration_requires_one_explicit_current_name(names, expected):
    snapshot = frozen()
    snapshot["tables"]["customers"]["records"][0]["fields"]["客户所属销售"] = names
    snapshot["tables"]["customers"]["visible_fields"].append("客户所属销售")
    refresh(snapshot, "customers")
    row = by_id(build_preflight(snapshot, empty_inventory()), "cust1")
    assert row["normalized"]["current_owner_account"] == expected
    assert row["normalized"]["source_owner_names"] == [n["name"] for n in names]
    assert row["action"] == "create"


def test_customer_owner_restoration_never_reassigns_current_claimed_owner():
    snapshot = frozen()
    snapshot["tables"]["customers"]["records"][0]["fields"]["客户所属销售"] = [{"name": "Alice"}]
    snapshot["tables"]["customers"]["visible_fields"].append("客户所属销售")
    refresh(snapshot, "customers")
    inventory = existing(snapshot, current={"name": "Example Customer", "current_owner_account": "BOB"})
    target = inventory["objects"]["customers"][0]
    target["ownership"] = {"is_assigned": True, "owner_user_ref_id": "existing-owner"}
    row = by_id(build_preflight(snapshot, inventory), "cust1")
    assert row["action"] == "noop"
    assert row["preserve"]["current_owner_account"] == "existing_customer_owner_preserved"
    target["ownership"] = {"is_assigned": False, "owner_user_ref_id": None}
    target["values"]["current_owner_account"] = None
    row = by_id(build_preflight(snapshot, inventory), "cust1")
    assert row["changes"]["current_owner_account"] == {"before": None, "after": "ALICE"}
    assert "restore_source_customer_owner_without_claim_approval" in row["warnings"]


def test_source_partner_association_does_not_imply_reseller_channel():
    snapshot = frozen()
    snapshot["tables"]["opportunities"]["records"][0]["fields"].update(
        {"所属伙伴": [{"id": "partner1"}], "签单方式": ["Direct customer"]},
    )
    snapshot["tables"]["opportunities"]["visible_fields"].append("签单方式")
    snapshot["execution_contract"]["sales_channel_map"] = {"Direct customer": "direct"}
    snapshot["tables"]["partners"]["records"] = [{"record_id": "partner1", "fields": {"伙伴名称": "Partner"}}]
    snapshot["tables"]["partners"]["visible_fields"] = ["伙伴名称"]
    refresh(snapshot, "partners")
    refresh(snapshot, "opportunities")
    row = by_id(build_preflight(snapshot, empty_inventory()), "opp1")
    assert row["action"] == "create"
    assert row["normalized"]["sales_channel"] == "direct"
    assert row["related_objects"]["associated_partner_source_ids"] == ["partner1"]


def test_same_customer_normalized_opportunity_name_quarantines_whole_source_group():
    snapshot = frozen()
    other = deepcopy(snapshot["tables"]["opportunities"]["records"][0])
    other["record_id"] = "opp2"
    other["fields"]["商机名称"] = " example\tproject "
    snapshot["tables"]["opportunities"]["records"].append(other)
    refresh(snapshot, "opportunities")
    plan = build_preflight(snapshot, empty_inventory())
    for record_id in ("opp1", "opp2"):
        row = by_id(plan, record_id)
        assert row["action"] == "quarantine"
        assert "duplicate_customer_opportunity_name_requires_review" in row["issues"]
    assert by_id(plan, "visit1")["action"] == "quarantine"


def test_source_name_collision_with_different_existing_opportunity_is_not_a_merge():
    snapshot = frozen()
    normalized = by_id(build_preflight(snapshot, empty_inventory()), "opp1")["normalized"]
    inventory = empty_inventory()
    inventory["objects"]["opportunities"] = [{
        "id": "different-existing-opportunity", "version": 1, "source_keys": [],
        "values": {**normalized, "name": " EXAMPLE PROJECT "},
        "last_imported_values": {},
    }]
    row = by_id(build_preflight(snapshot, inventory), "opp1")
    assert row["action"] == "conflict"
    assert row["target_id"] is None
    assert "existing_customer_opportunity_name_conflict" in row["issues"]


def test_missing_actual_author_keeps_full_record_in_quarantine():
    snapshot = frozen()
    snapshot["tables"]["visits"]["records"][0]["fields"]["跟进人"] = []
    refresh(snapshot, "visits")
    row = by_id(build_preflight(snapshot, empty_inventory()), "visit1")
    assert row["action"] == "quarantine"
    assert "original_author_unknown" in row["issues"]
    assert row["raw_fields"]["沟通内容"] == "Original follow-up"


@pytest.mark.parametrize("source_stage", [[], ["No opportunity"], ["Invented stage"]])
def test_unknown_stage_does_not_default_to_open(source_stage):
    snapshot = frozen()
    snapshot["tables"]["opportunities"]["records"][0]["fields"]["商机状态"] = source_stage
    refresh(snapshot, "opportunities")
    row = by_id(build_preflight(snapshot, empty_inventory()), "opp1")
    assert row["action"] == "quarantine"
    assert "status" not in row["normalized"]


def test_native_recording_day_and_business_followup_day_are_independent():
    snapshot = frozen()
    source = snapshot["tables"]["visits"]["records"][0]["fields"]
    source["跟进日期"] = "2026-03-03T00:00:00.000+08:00"
    source["创建时间"] = "2026-03-06T07:37:57.352+08:00"
    refresh(snapshot, "visits")
    row = by_id(build_preflight(snapshot, empty_inventory()), "visit1")["normalized"]
    assert row["interaction_on"] == "2026-03-03"
    assert row["recorded_on"] == "2026-03-06"


def test_existing_close_date_is_preserved_and_a_different_source_quarter_is_blocked():
    snapshot = frozen()
    normalized = by_id(build_preflight(snapshot, empty_inventory()), "opp1")["normalized"]
    values = deepcopy(normalized)
    values["expected_close_date"] = "2026-06-30"
    inventory = empty_inventory()
    inventory["objects"]["opportunities"] = [{
        "id": "system-opp", "version": 1,
        "source_keys": [source_key("feishu", "base-one", "opportunities", "opp1")],
        "values": values, "last_imported_values": deepcopy(values),
    }]
    row = by_id(build_preflight(snapshot, inventory), "opp1")
    assert row["action"] == "conflict"
    assert "existing_close_date_differs_from_source_quarter" in row["issues"]
    assert "expected_close_date" not in row["changes"]


def test_forecast_children_require_real_baseline_and_preserve_missing_source_amounts():
    snapshot = frozen()
    normalized = by_id(build_preflight(snapshot, empty_inventory()), "opp1")["normalized"]
    inventory = empty_inventory()
    inventory["objects"]["opportunities"] = [{
        "id": "system-opp", "version": 1,
        "source_keys": [source_key("feishu", "base-one", "opportunities", "opp1")],
        "values": deepcopy(normalized), "last_imported_values": deepcopy(normalized),
        "forecasts_complete": True,
        "forecast_reconciliation": [{
            "year": 2026, "quarter": 4,
            "current": {"id": "forecast-id", "year": 2026, "quarter": 4,
                        "recognized_amount": 500, "collection_amount": 20000,
                        "updated_at": "2026-09-18T00:00:00+08:00"},
            "last_imported_snapshot": {"recognized_amount": 500, "collection_amount": 20000},
        }],
    }]
    row = by_id(build_preflight(snapshot, inventory), "opp1")
    child = row["related_objects"]["forecast_reconciliation"][0]
    assert child["action"] == "update"
    assert child["target_id"] == "forecast-id"
    assert child["source_values"] == {"collection_amount": "30000", "collection_confidence": "high"}
    assert child["current_snapshot"]["recognized_amount"] == 500
    assert row["can_execute_children"] is True
    inventory["objects"]["opportunities"][0]["forecast_reconciliation"][0]["current"]["collection_amount"] = 25000
    row = by_id(build_preflight(snapshot, inventory), "opp1")
    assert row["related_objects"]["forecast_reconciliation"][0]["action"] == "conflict"
    assert row["can_execute_children"] is False


def test_child_create_and_parent_quarantine_never_sneak_through_parent_boundary():
    snapshot = frozen()
    assert by_id(build_preflight(snapshot, empty_inventory()), "opp1")["can_execute_children"] is True
    snapshot["quarantine"] = {"opp1": ["unconfirmed_subject"]}
    row = by_id(build_preflight(snapshot, empty_inventory()), "opp1")
    assert row["can_execute"] is False
    assert row["can_execute_children"] is False


@pytest.mark.parametrize("segments,current", [
    ([{"type": "text", "text": "Original follow-up\n"}], "Original follow-up\n"),
    ([{"type": "text", "text": " Original follow-up"}], " Original follow-up"),
    ([{"type": "url", "text": "2.HR", "link": "http://2.HR"},
      {"type": "text", "text": " Ops: ask about permissions"}], "2.HR Ops: ask about permissions"),
    ([{"type": "mention", "text": "@Former Person", "link": "https://example.test/avatar"},
      {"type": "text", "text": "\nOriginal follow-up"}], "@Former Person\nOriginal follow-up"),
])
def test_exact_native_format_proof_preserves_existing_body_bytes(segments, current):
    snapshot = frozen()
    rendered = "".join(s["text"] if s["type"] == "text" else f"[{s['text']}]({s['link']})" for s in segments)
    snapshot["tables"]["visits"]["records"][0]["fields"]["沟通内容"] = rendered
    refresh(snapshot, "visits")
    normalized = by_id(build_preflight(snapshot, empty_inventory()), "visit1")["normalized"]
    source = normalized["follow_up_record"]
    values = {**deepcopy(normalized), "follow_up_record": current}
    inventory = empty_inventory()
    inventory["objects"]["visits"] = [{
        "id": "system-visit", "version": 3,
        "source_keys": [source_key("feishu", "base-one", "visits", "visit1")],
        "values": values, "last_imported_values": deepcopy(values),
    }]
    proof = {
        "target_id": "system-visit", "expected_target_version": 3,
        "source_value_sha256": canonical_hash(source), "current_value_sha256": canonical_hash(current),
        "native_segments": segments, "native_segments_sha256": canonical_hash(segments),
        "evidence_sha256": "native-export-sha", "evidence": "Reviewed native rich text segments",
    }
    snapshot["format_preservation"] = {"visit1": {"follow_up_record": proof}}
    row = by_id(build_preflight(snapshot, inventory), "visit1")
    assert row["action"] == "noop"
    assert row["preserve"]["follow_up_record"] == "format_only_preserve_existing"
    assert "follow_up_record" not in row["changes"]
    assert inventory["objects"]["visits"][0]["values"]["follow_up_record"] == current
    # A stale target or modified source invalidates the exact proof.
    proof["expected_target_version"] = 2
    assert by_id(build_preflight(snapshot, inventory), "visit1")["action"] == "conflict"
    proof["expected_target_version"] = 3
    proof["native_segments"] = [{"type": "text", "text": "A different business fact"}]
    proof["native_segments_sha256"] = canonical_hash(proof["native_segments"])
    assert by_id(build_preflight(snapshot, inventory), "visit1")["action"] == "conflict"
