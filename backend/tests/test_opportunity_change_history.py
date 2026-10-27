import json
from pathlib import Path
from uuid import uuid4

import pytest

from sales_backend.contracts.opportunity_change_history import HistoryEvent, OpportunityChangeHistory
from sales_backend.domain.opportunity_change_history import change_document


def event(changes, **kwargs):
    value = change_document(
        dict(
            id=str(uuid4()),
            version_no=2,
            created_at="2026-10-20T07:30:00Z",
            actor_id=str(uuid4()),
            actor_name="现在的姓名",
            changes=changes,
        ),
        **kwargs,
    )
    HistoryEvent.model_validate(value)
    return value


def test_shared_wire_fixture_and_current_actor_name_are_explicit():
    fixture = Path(__file__).with_name("fixtures") / "opportunity_change_history.v1.json"
    document = OpportunityChangeHistory.model_validate_json(fixture.read_text())
    assert document.changes.items[0].actor.name_status == "current"
    assert event([])["actor"]["name_status"] == "current"


@pytest.mark.parametrize(
    "label,key",
    [
        ("商机阶段", "stage"),
        ("ACV（万元）", "amount"),
        ("预计关单日期", "expected_close_date"),
        ("2026 Q4 预测含税确收（万元）", "forecast.2026.q4.recognized"),
        ("2026 Q4 预测回款（万元）", "forecast.2026.q4.collection"),
        ("2026 Q4 回款信心度", "forecast.2026.q4.confidence"),
    ],
)
def test_legacy_labels_keep_units_and_periods(label, key):
    value = event([dict(label=label, before="未填写", after="1.25")])["fields"][0]
    assert value["field"] == key and value["label"] == label
    assert value["after"]["rows"][0]["name"] == "1.25"


def test_people_ids_roles_and_snapshot_names_are_not_dictionary_strings():
    person, line = str(uuid4()), str(uuid4())
    fields = event(
        [
            dict(field="participants", before={}, after={person: "fde"}),
            dict(field="fde_members", before=[], after=[person]),
            dict(field="primary_fde_id", before=None, after=person),
            dict(
                field="intent_product_line_ids",
                before=[],
                after=[line],
                display_snapshot=dict(
                    version=1,
                    basis="directory_at_change",
                    before=[],
                    after=[dict(id=line, name="历史线名", status="recorded", secret="must not escape")],
                ),
            ),
        ]
    )["fields"]
    row = fields[0]["after"]["rows"][0]
    assert row == dict(name=None, id=person, missing_name=True, id_label="人员ID", detail="FDE参与者")
    assert fields[1]["after"]["rows"][0]["id"] == person
    assert fields[2]["before"]["status"] == "empty"
    assert fields[3]["after"]["rows"][0]["name"] == "历史线名"
    assert "must not escape" not in json.dumps(fields)
    assert "[object Object]" not in json.dumps(fields)


def test_mismatched_snapshot_never_supplies_a_name():
    line = str(uuid4())
    field = event(
        [
            dict(
                field="intent_product_line_ids",
                before=[],
                after=[line],
                display_snapshot=dict(
                    version=1,
                    basis="directory_at_change",
                    before=[],
                    after=[dict(id=str(uuid4()), name="错误名称", status="recorded")],
                ),
            )
        ]
    )["fields"][0]
    assert field["after"]["rows"][0]["missing_name"]
    assert "错误名称" not in json.dumps(field, ensure_ascii=False)


def test_decimal_precision_and_material_snapshot_are_retained():
    material = str(uuid4())
    raw = (
        '[{"field":"material_lines","before":[],"after":[{"material_id":"'
        + material
        + '","quantity":1234567890.1234,"unit_price":1234567890.12,"catalog_snapshot":'
        + '{"id":"'
        + material
        + '","name":"保存时物料名","secret":"private"},"unknown":{"secret":"private"}}]}]'
    )
    value = event(raw)["fields"][0]["after"]["rows"][0]
    assert value["name"] == "保存时物料名"
    assert "1234567890.1234" in value["detail"] and "1234567890.12" in value["detail"]
    assert "private" not in json.dumps(value)


@pytest.mark.parametrize(
    "change",
    [
        None,
        42,
        {"field": "secrets", "before": "private", "after": "private"},
        {"field": "participants", "before": {}, "after": {str(uuid4()): {}}},
        {"field": "participants", "before": {}, "after": {str(uuid4()): []}},
        {"field": "intent_product_line_ids", "before": [], "after": [42]},
        {"field": "material_lines", "before": [], "after": [{"material_id": "bad"}]},
        {"field": "amount", "before": "1", "after": "NaN"},
        {"field": "summary", "before": "", "after": {"secret": "private"}},
        {"field": "is_key", "before": False, "after": "false"},
    ],
)
def test_malformed_values_are_explicit_and_do_not_escape_as_json(change):
    document = event([change])
    assert document["unavailable_field_count"] == 1
    assert "private" not in json.dumps(document)


def test_missing_value_is_not_a_cleared_value():
    field = event([dict(field="note", after=None)])["fields"][0]
    assert field["before"]["status"] == "unavailable"
    assert field["after"]["status"] == "empty"


def test_financial_actual_restriction_differs_from_missing_history():
    raw = [dict(field="operational_actual", before={"amount": "101"}, after={"amount": "202"})]
    denied = event(raw)["fields"][0]
    assert denied["before"]["status"] == denied["after"]["status"] == "restricted"
    assert "101" not in json.dumps(denied) and "202" not in json.dumps(denied)
    allowed = event(raw, actual_allowed=True)["fields"][0]
    assert allowed["after"]["rows"][0]["detail"] == "202"


def test_all_projection_boundaries_are_visible():
    person = str(uuid4())
    material = str(uuid4())
    document = event(
        [
            dict(field="note", before="", after="文" * 6000),
            dict(field="fde_members", before=[], after=[person] * 101),
            dict(
                field="material_lines",
                before=[],
                after=[
                    dict(material_id=material, note="长" * 5000),
                ],
            ),
        ]
    )
    assert all(field["after"]["truncated"] for field in document["fields"])
    assert document["detail_status"] == "partial"
    many = event([dict(field="note", before="", after="x")] * 201)
    assert len(many["fields"]) == 200 and many["unavailable_field_count"] >= 1
    assert event('{"not":"an array"}')["detail_status"] == "unavailable"


def test_unknown_event_and_zero_are_not_fabricated_or_dropped():
    assert event([dict(field="new_unknown_field")])["fields"][0]["field"] == "unknown"
    field = event([dict(label="ACV（万元）", before="未填写", after="0")])["fields"][0]
    assert field["after"]["rows"][0]["name"] == "0"
