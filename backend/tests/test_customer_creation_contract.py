"""Creation choices and old request compatibility without business writes."""
import json
from itertools import product
from pathlib import Path

import pytest
from pydantic import ValidationError

from sales_backend.contracts.customer_creation import CustomerCreationRequestBody
from sales_backend.contracts.models import CustomerCreate, CustomerUpdate
from sales_backend.domain.business_options import business_options


def payload(**changes):
    return {"name": "合成建档客户", "industry": "企业软件", "customer_type": "潜在客户",
            "level_code": "Tier-1", "source": "销售自拓", "target_team": "合成目标团队",
            "contact_name": "合成联系人", "contact_title": "总经理", "contact_role": "决策者", **changes}


@pytest.mark.parametrize("source,customer_type", product(
    ["销售自拓", "客户转介绍", "市场活动", "销售线索", "合作伙伴", "其他"],
    ["潜在客户", "商机客户", "已成单客户"],
))
def test_canonical_old_payloads_remain_valid_without_optional_identifiers(source, customer_type):
    body = payload(source=source, customer_type=customer_type)
    result = CustomerCreationRequestBody.model_validate(body)
    assert result.source == source and result.customer_type == customer_type
    assert result.company_reference is None and result.target_team_id is None
    assert {key: result.model_dump()[key] for key in body} == body


def test_creation_catalog_is_additive_and_every_choice_is_valid():
    catalog = business_options()
    previous = json.loads((Path(__file__).resolve().parents[2]
                           / "frontend/tests/helpers/business-options.json").read_text())
    assert catalog["contract_version"] == 1
    assert {k: v for k, v in catalog.items() if k != "customer_creation"} == {
        k: v for k, v in previous.items() if k != "customer_creation"}
    creation = catalog["customer_creation"]
    assert set(creation) == {"industry", "customer_type", "source", "level_code", "contact_role", "business_identities"}
    assert creation["business_identities"] == [{"value": "customer", "label": "客户"}, {"value": "partner", "label": "合作伙伴"}]
    schema = CustomerCreate.model_json_schema()["properties"]
    for field, choices in creation.items():
        if field == "business_identities":
            continue
        if "enum" in schema[field]:
            assert choices == schema[field]["enum"]
        for choice in choices:
            assert CustomerCreationRequestBody.model_validate(payload(**{field: choice}))


def test_legacy_self_prospecting_alias_is_normalized_only_at_submission_boundary():
    original = payload(source="自主拓展")
    result = CustomerCreationRequestBody.model_validate(original)
    assert result.model_dump(mode="json")["source"] == "销售自拓"
    assert original["source"] == "自主拓展"
    with pytest.raises(ValidationError):
        CustomerCreate.model_validate(original)


@pytest.mark.parametrize("field,value", [
    ("source", "公司分配"), ("source", "未知来源"), ("source", ""),
    ("source", {"untrusted": "销售自拓"}),
    ("customer_type", "新拓客户"), ("customer_type", "存量客户"), ("customer_type", "未知类型"),
])
def test_ambiguous_or_unknown_choices_are_not_guessed(field, value):
    with pytest.raises(ValidationError) as error:
        CustomerCreationRequestBody.model_validate(payload(**{field: value}))
    assert [(e["loc"], e["type"]) for e in error.value.errors()] == [((field,), "literal_error")]


def test_general_catalog_and_update_model_keep_historical_values():
    customer = business_options()["customer"]
    for value in ["公司分配", "自主拓展"]:
        assert value in customer["source"]
        assert CustomerUpdate(source=value).source == value
    for value in ["新拓客户", "存量客户"]:
        assert value in customer["customer_type"]
        assert CustomerUpdate(customer_type=value).customer_type == value


def test_existing_extra_fields_and_invalid_team_ids_still_fail():
    for changes, expected in [({"unexpected": "value"}, "extra_forbidden"),
                              ({"target_team_id": "not-a-uuid"}, "uuid_parsing")]:
        with pytest.raises(ValidationError) as error:
            CustomerCreationRequestBody.model_validate(payload(**changes))
        assert error.value.errors()[0]["type"] == expected
