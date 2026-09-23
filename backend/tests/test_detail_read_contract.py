"""Public read model contracts must remain visible and numeric in OpenAPI/JSON."""

import json
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sales_backend.contracts.detail_reads import DetailObject, HistoryPage, TimelineEvent
from sales_backend.main import app


def test_every_new_detail_endpoint_has_a_resolvable_response_schema():
    spec = app.openapi()
    paths = ["/customers/{customer_id}/header", "/opportunities/{opportunity_id}/header",
             "/customers/{customer_id}/opportunities/{opportunity_id}/header",
             "/customers/{customer_id}/overview", "/opportunities/{opportunity_id}/overview",
             "/customers/{customer_id}/opportunities/{opportunity_id}/overview",
             "/customers/{customer_id}/opportunities", "/customers/{customer_id}/contacts", "/visits",
             "/opportunities/{opportunity_id}/timeline", "/customer-assets/quarters",
             "/console/customers/{customer_id}/overview", "/console/customers/{customer_id}/history"]
    for path in paths:
        schema = spec["paths"]["/api/v1" + path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["$ref"].startswith("#/components/schemas/")
        model = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
        assert model["properties"] and model["required"]


def test_detail_extensions_preserve_numeric_json_and_explicit_unknowns():
    item = DetailObject.model_validate({"amount": Decimal("12.25"), "attributes": {"budget": Decimal(0)},
                                       "quarterly_forecasts": [{"recognized_amount": None,
                                                                "collection_amount": Decimal("50.5")}]})
    result = json.loads(item.model_dump_json())
    assert result == {"amount": 12.25, "attributes": {"budget": 0},
                      "quarterly_forecasts": [{"recognized_amount": None, "collection_amount": 50.5}]}


def test_history_page_rejects_missing_pagination_and_invalid_target_ids():
    with pytest.raises(ValidationError):
        HistoryPage[TimelineEvent].model_validate({"items": [], "has_more": False})
    event = {"key": "visit", "title": "拜访", "at": "2026-09-14T00:00:00+08:00", "detail": "摘要",
             "object_type": "visit", "object_id": str(uuid4())}
    page = HistoryPage[TimelineEvent].model_validate({"items": [event], "has_more": False, "next_offset": None})
    assert page.items[0].object_type == "visit"
    with pytest.raises(ValidationError):
        TimelineEvent.model_validate({**event, "object_id": "not-a-uuid"})
