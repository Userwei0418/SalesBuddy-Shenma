import pytest

from sales_backend.contracts.opportunity_candidate import validate_candidate


def test_no_association_produces_no_update_fields():
    result = validate_candidate({'action':'none','name':'模型多余名称','amount':80000}, {})
    assert not result['name'] and result['amount'] is None
    assert result['missing_fields'] == []


def test_update_candidate_cannot_reference_out_of_scope_opportunity():
    with pytest.raises(ValueError):
        validate_candidate({'action':'update','opportunity_id':'other'}, {'current_opportunities':[{'id':'own'}]})
    result = validate_candidate({'action':'update','opportunity_id':'own','amount':1200}, {'current_opportunities':[{'id':'own'}]})
    assert result['amount'] == 1200 and result['probability'] is None


def test_create_candidate_can_remain_incomplete_for_human_confirmation():
    assert validate_candidate({'action':'create','name':'新项目'}, {})['expected_close_date'] is None
    with pytest.raises(ValueError):
        validate_candidate({'action':'create','opportunity_id':'old'}, {})
