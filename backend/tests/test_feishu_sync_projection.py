import pytest

from sales_backend.domain.feishu_sync.projection import project


@pytest.mark.parametrize('amount,grade', [(None, None), (-1, None), (0, 'D'), (99999, 'D'),
                                        (100000, 'C'), (499999, 'C'), (500000, 'B'),
                                        (999999, 'B'), (1000000, 'A')])
def test_grade_matches_miniprogram_amount_bands(amount, grade):
    values = project('opportunity', {'id': 'fixture', 'amount': amount}, {'grade'})
    assert values['grade'] == grade


def test_scope_exit_never_exports_derived_or_business_fields():
    values = project('customer', {'id': 'fixture', 'excluded': True, 'name': 'private',
                                 'potential_score': 99, 'fde_members': ['private']},
                     {'name', 'potential_score', 'fde_members'})
    assert values == {'system_id': 'fixture', 'record_status': '已归档'}


def test_forecast_preserves_raw_zero_null_and_period_without_inventing_actuals():
    from sales_backend.domain.feishu_sync.config import COMMON_FIELDS, SOURCE_FIELDS
    raw = {"id": "fixture", "year": 2026, "quarter": 3, "recognized_amount": 0,
           "collection_amount": None, "updated_by_user_ref_id": "member",
           "updated_at": "2026-09-22T00:00:00Z", "untrusted_extra": 99}
    values = project("forecast", raw, SOURCE_FIELDS["forecast"] | COMMON_FIELDS)
    assert values["recognized_amount"] == 0
    assert values["collection_amount"] is None
    assert (values["year"], values["quarter"]) == (2026, 3)
    assert values["updated_by"] == "member"
    assert values["source_created_at"] is None and values["source_version"] is None
    assert "untrusted_extra" not in values and "amount" not in values


def test_historical_quarter_preserves_unit_and_unknown_tax_without_date():
    from sales_backend.domain.feishu_sync.config import COMMON_FIELDS, SOURCE_FIELDS
    values = project("period_actual_snapshot", {
        "id": "fixture", "year": 2026, "quarter": 2, "kind": "recognized",
        "raw_amount": "0", "source_unit": "wan_cny", "tax_basis": "unknown",
    }, SOURCE_FIELDS["period_actual_snapshot"] | COMMON_FIELDS)
    assert values["raw_amount"] == "0"
    assert values["source_unit"] == "wan_cny" and values["tax_basis"] == "unknown"
    assert "occurred_on" not in values and "amount" not in values


@pytest.mark.parametrize("linked,snapshot,expected", [
    ("关联伙伴", None, "关联伙伴"),
    ("关联伙伴", "旧文本", "关联伙伴"),
    (None, "历史文本", "历史文本"),
    (None, None, None),
])
def test_visit_partner_uses_linked_name_and_preserves_legacy_snapshot(linked, snapshot, expected):
    raw = {"id": "fixture", "archived_at": "2026-09-22T00:00:00Z",
           "partner_name": linked, "partner_name_snapshot": snapshot}
    assert project("visit", raw, {"partner_name"})["partner_name"] == expected
    assert "partner_name" not in project("visit", raw, {"content"})


@pytest.mark.parametrize("original,basis", [
    ("2025/12/31", "history_source_fields"),
    ("2026/03/31", "history_legacy_raw_fields"),
    ("unknown original date", "history_source_fields"),
    (None, "historical_unknown"),
    ("2026-09-22T00:00:00+00:00", "system_entry"),
])
def test_original_opportunity_creation_is_separate_from_system_creation(original, basis):
    from sales_backend.domain.feishu_sync.config import COMMON_FIELDS, SOURCE_FIELDS
    fields = SOURCE_FIELDS["opportunity"] | COMMON_FIELDS
    raw = {"id": "fixture", "created_at": "2026-09-22T00:00:00+00:00",
           "original_created_at_raw": original, "original_created_at_source": basis}
    values = project("opportunity", raw, fields)
    assert values["original_created_at_raw"] == original
    assert values["original_created_at_source"] == basis
    assert values["source_created_at"] == raw["created_at"]
    for kind in ("customer", "visit", "member"):
        assert "original_created_at_raw" not in SOURCE_FIELDS[kind] | COMMON_FIELDS
    assert project("opportunity", {**raw, "excluded": True}, fields) == {
        "system_id": "fixture", "record_status": "已归档",
    }
