from decimal import Decimal

import pytest

from sales_backend.domain.opportunities import differences
from sales_backend.domain.review_changes import event_changes, history_coverage, opportunity_changes


def event(identifier, changes, *, kind="business_change", version=2, hour=10):
    return {"id": identifier, "kind": kind, "changes": changes, "version_no": version,
            "occurred_at": f"2026-10-14T{hour:02}:00:00+08:00", "actor_id": None}


def rows(*events):
    return [{"id": "opportunity", "events": list(events)}]


def test_legacy_same_channel_and_reason_only_remove_empty_opportunity():
    raw = [{"label": "销售渠道", "before": "partner", "after": "partner"},
           {"field": "field_edit_reason", "before": None, "after": {"reason": "说明"}}]
    assert opportunity_changes(rows(event("a", raw))) == []


def test_round_trip_is_two_events_and_one_opportunity_with_compatibility_ids():
    result = opportunity_changes(rows(
        event("a", [{"label": "销售渠道", "before": "direct", "after": "partner"}]),
        event("b", [{"label": "销售渠道", "before": "partner", "after": "direct"}], version=3, hour=11),
    ))
    assert len(result) == 1 and result[0]["event_count"] == 2
    assert [e["event_id"] for e in result[0]["events"]] == ["a", "b"]
    assert all(e["id"] == e["event_id"] and e["kind"] == "business_change" for e in result[0]["events"])


def test_business_event_does_not_hide_additional_state_fields():
    result = opportunity_changes(rows(
        event("b", [{"field": "probability", "before": "30", "after": "70"}]),
        event("s", {"before": {"probability": 30, "state": {"amount": "10.00"}},
                    "after": {"probability": 70, "state": {"amount": "20.00"}}}, kind="opportunity_state"),
    ))[0]
    assert result["event_count"] == 2
    b, s = result["events"]
    assert [c["field"] for c in b["changes"]] == ["probability"]
    assert [c["field"] for c in s["changes"]] == ["amount"]
    assert {r["id"] for r in b["source_refs"]} == {"b", "s"}


def test_identical_state_field_is_merged_only_at_same_version():
    business = event("b", [{"field": "status", "before": "open", "after": "won"}])
    state = event("s", {"before": {"status": "open"}, "after": {"status": "won"}}, kind="opportunity_state")
    result = opportunity_changes(rows(business, state))[0]
    assert result["event_count"] == 1 and len(result["source_refs"]) == 2
    state["version_no"] = 4
    assert opportunity_changes(rows(business, state))[0]["event_count"] == 2


def test_forecast_event_not_collapsed_by_matching_values_or_time():
    result = opportunity_changes(rows(
        event("b", [{"field": "collection_amount", "before": "1", "after": "2", "year": 2026, "quarter": 4}]),
        event("f", {"before": {"collection_amount": "1"}, "after": {"collection_amount": "2"},
                    "year": 2026, "quarter": 4}, kind="opportunity_forecast", version=None),
    ))[0]
    assert result["event_count"] == 2


def test_no_numeric_drift_or_missing_side_invents_a_change():
    value = event("a", [{"field": "amount", "before": "1.00", "after": "1"},
                        {"field": "collection_amount", "after": "99"},
                        {"field": "collection_amount", "before": None, "after": "0"}])
    changes = event_changes(value)
    assert len(changes) == 1 and changes[0]["before"] is None and changes[0]["after"] == "0"
    assert changes[0]["before_known"] is True


def test_legacy_finance_units_and_nonfinancial_unknown_labels_preserved():
    changes = event_changes(event("a", [
        {"label": "2026 Q4 预测回款（万元）", "before": "1", "after": "2"},
        {"label": "旧业务自定义字段", "before": "A", "after": "B"},
        {"field": "participants", "before": {"a": "sales"}, "after": {"b": "fde"}},
    ]))
    assert len(changes) == 3
    assert changes[0]["field"] == "collection_amount" and changes[0]["unit"] == "万元"
    assert (changes[0]["year"], changes[0]["quarter"]) == (2026, 4)
    assert changes[1]["field"] is None and changes[1]["label"] == "旧业务自定义字段"


def test_order_uses_remaining_real_event_not_latest_empty_event():
    result = opportunity_changes([
        {"id": "a", "events": [event("real", [{"field": "name", "before": "A", "after": "B"}], hour=8),
                                 event("empty", [{"field": "name", "before": "B", "after": "B"}], hour=13)]},
        {"id": "b", "events": [event("other", [{"field": "name", "before": "C", "after": "D"}], hour=9)]},
    ])
    assert [row["id"] for row in result] == ["b", "a"]


@pytest.mark.parametrize("has_records, status", [(True, "partial"), (False, "unavailable")])
def test_no_fabricated_full_history_from_capture_install_time(has_records, status):
    coverage = history_coverage(has_records=has_records)
    assert coverage["status"] == status and coverage["complete_from"] is None
    assert "history_field_coverage_unverified" in coverage["reason_codes"]


def test_partner_identity_change_does_not_emit_unchanged_channel():
    before = {"name": "N", "amount": Decimal(100), "probability": 30, "status": "open",
              "sales_channel": "partner", "partner_id": "old", "partner_name": "同名伙伴"}
    changes = differences(before, {**before, "partner_id": "new"}, [], [])
    assert changes == [{"field": "partner_id", "label": "所属伙伴标识", "before": "old", "after": "new"}]
    both = differences(before, {**before, "partner_id": None, "sales_channel": "direct"}, [], [])
    assert {c["field"] for c in both} == {"sales_channel", "partner_id"}


def test_unknown_image_fields_and_empty_events_are_excluded_but_proven_null_changes_remain():
    for kind in ("opportunity_state", "opportunity_forecast"):
        missing = event_changes(event("missing", {"before": {}, "after": {"amount": None}}, kind=kind))
        assert missing == []
        only_unknown = event("only-unknown", {"before": {}, "after": {"amount": "1"}}, kind=kind)
        assert opportunity_changes(rows(only_unknown)) == []
        explicit = event_changes(event("null", {"before": {"amount": None}, "after": {"amount": "1"}}, kind=kind))
        assert explicit[0]["before_known"] is True and explicit[0]["after_known"] is True
        reverse = event_changes(event("reverse", {"before": {"amount": "1"}, "after": {}}, kind=kind))
        assert reverse == []
        mixed = event_changes(event("mixed", {"before": {"status": "open"},
                                             "after": {"status": "won", "amount": "1"}}, kind=kind))
        assert [change["field"] for change in mixed] == ["status"]
    for before, after in ((None, {"collection_amount": "1"}), ({"collection_amount": "1"}, None)):
        absent = event_changes(event("absent", {"before": before, "after": after}, kind="opportunity_forecast"))
        assert len(absent) == 1 and absent[0]["field"] == "collection_amount"
        assert absent[0]["before_known"] is True and absent[0]["after_known"] is True
    missing_image = event_changes(event("no-image", {"after": {"collection_amount": "1"}}, kind="opportunity_forecast"))
    assert missing_image == []
    distinct = opportunity_changes(rows(
        event("business", [{"field": "amount", "before": None, "after": "1"}]),
        event("state", {"before": {}, "after": {"amount": "1"}}, kind="opportunity_state"),
    ))[0]
    assert distinct["event_count"] == 1 and distinct["events"][0]["event_id"] == "business"
