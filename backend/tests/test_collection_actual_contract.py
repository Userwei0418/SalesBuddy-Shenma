from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from sales_backend.api.idempotency import require_material_key
from sales_backend.contracts.collection_actuals import CollectionActualInput
from sales_backend.contracts.models import OpportunityCreate


def item(**overrides):
    return dict(
        year=2026, quarter=4, amount="0", source_ref=" bank receipt ", expected_actual_revision="a" * 64, **overrides
    )


def opportunity(**overrides):
    data = dict(
        action="update",
        opportunity_id=str(uuid4()),
        version_no=1,
        name="Opportunity",
        probability=10,
        amount="100000",
        expected_close_date="2026-12-31",
    )
    return OpportunityCreate.model_validate({**data, **overrides})


def test_zero_is_real_money_and_scope_cannot_be_supplied():
    parsed = CollectionActualInput.model_validate(item())
    assert parsed.amount == Decimal("0") and parsed.source_ref == "bank receipt"
    for field, value in [("kind", "recognized"), ("source", "historical"), ("workspace_id", str(uuid4()))]:
        with pytest.raises(ValidationError):
            CollectionActualInput.model_validate({**item(), field: value})


def test_changed_collection_requires_idempotency_and_existing_opportunity():
    with pytest.raises(HTTPException):
        require_material_key({"collection_actuals": [item()]}, None)
    require_material_key({"collection_actuals": []}, None)
    require_material_key({"collection_actuals": [item()]}, uuid4())
    with pytest.raises(ValidationError):
        opportunity(action="create", collection_actuals=[item()])
    with pytest.raises(ValidationError):
        opportunity(collection_actuals=[item(), item()])
    assert opportunity().collection_actuals is None


def test_collection_history_shows_period_amount_basis_without_private_finance_payload():
    from sales_backend.domain.opportunity_change_history import change_document

    document = change_document(
        dict(
            id=str(uuid4()),
            actor_id=str(uuid4()),
            actor_name="Sales",
            version_no=3,
            created_at="2026-10-24T01:00:00Z",
            changes=[
                dict(
                    field="collection_actual",
                    year=2026,
                    quarter=4,
                    before=dict(amount=None, source=None, status="missing", revision="secret-revision"),
                    after=dict(
                        amount="150000.00",
                        source="operations",
                        source_ref="Bank basis",
                        note="Quarter total",
                        revision="secret-revision",
                        private={"secret": "must not render"},
                    ),
                )
            ],
        )
    )
    assert document["detail_status"] == "recorded"
    rows = document["fields"][0]["after"]["rows"]
    assert any(r["detail"] == "2026 Q4" for r in rows)
    assert any(r["detail"] == "150000.00" for r in rows)
    assert any(r["detail"] == "Bank basis" for r in rows)
    assert any(r["detail"] == "Quarter total" for r in rows)
    assert "secret" not in str(document)
