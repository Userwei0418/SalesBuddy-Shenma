"""Small direct-net-recognition contract/money/attribution checks; no DB I/O."""

from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest
from pydantic import ValidationError

from sales_backend.api.idempotency import require_material_key
from sales_backend.contracts.models import OpportunityCreate
from sales_backend.contracts.recognized_actuals import RecognizedActualInput
from sales_backend.domain.dashboard_actuals import actual_summary
from sales_backend.domain.recognized_actual_errors import RecognizedActualError
from sales_backend.domain.opportunity_change_history import change_document
from sales_backend.services import recognized_actuals


def period(amount="0"):
    return dict(
        year=2026,
        quarter=4,
        expected_actual_revision="a" * 64,
        entries=[dict(material_line_id=str(uuid4()), amount=amount, source_ref=" receipt ")],
    )


def test_exact_money_zero_and_non_overridable_scope():
    item = RecognizedActualInput.model_validate(period("12345678901234.01"))
    assert item.entries[0].amount == Decimal("12345678901234.01")
    assert item.entries[0].source_ref == "receipt"
    assert RecognizedActualInput.model_validate(period()).entries[0].amount == 0
    for amount in ("1.001", "-1", "NaN", "10000000000000000"):
        with pytest.raises(ValidationError):
            RecognizedActualInput.model_validate(period(amount))
    data = period()
    for key in ("workspace_id", "owner_id", "kind"):
        with pytest.raises(ValidationError):
            RecognizedActualInput.model_validate(data | {key: str(uuid4())})
    data["entries"].append(data["entries"][0])
    with pytest.raises(ValidationError):
        RecognizedActualInput.model_validate(data)


def test_save_requires_existing_opportunity_version_and_same_receipt():
    payload = dict(
        action="update",
        opportunity_id=str(uuid4()),
        version_no=1,
        name="test",
        probability=10,
        amount="1000",
        expected_close_date="2026-12-31",
        recognized_actuals=[period()],
    )
    assert OpportunityCreate.model_validate(payload).recognized_actuals
    for changes in ({"action": "create"}, {"version_no": None}, {"recognized_actuals": [period(), period()]}):
        with pytest.raises(ValidationError):
            OpportunityCreate.model_validate(payload | changes)
    with pytest.raises(RecognizedActualError, match="IDEMPOTENCY_KEY_REQUIRED"):
        require_material_key(payload, None)
    require_material_key(payload, uuid4())


@pytest.mark.asyncio
async def test_rejected_sql_uses_stable_code_without_detail(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(recognized_actuals, "read_recognized_actual", AsyncMock(return_value={"can_edit": True}))
    error = asyncpg.PostgresError.new(
        {"C": "40001", "M": "OPPORTUNITY_RECOGNIZED_VERSION_CONFLICT", "D": "private SQL"}
    )
    connection = type("Connection", (), {"fetchval": AsyncMock(side_effect=error)})()
    with pytest.raises(RecognizedActualError) as caught:
        await recognized_actuals.save_recognized_actuals(
            connection, object(), str(uuid4()), {}, {"action": "update", "recognized_actuals": [period()]}
        )
    assert caught.value.code == "OPPORTUNITY_RECOGNIZED_VERSION_CONFLICT"
    assert "private" not in str(caught.value)


def fact(**changes):
    return (
        dict(
            id="fact",
            opportunity_id="op",
            customer_id="customer",
            year=2026,
            quarter=4,
            kind="recognized",
            amount="123.45",
            raw_amount="123.45",
            complete=True,
            tax_unresolved=False,
            status="confirmed",
            missing_material_count=0,
            version_no=1,
            attribution_status="unresolved",
        )
        | changes
    )


def test_unknown_owner_keeps_company_money_but_never_enters_person_total_or_refs():
    company = actual_summary([fact()], "actual_recognized_exclusive", scope_complete=True)
    assert company["value"] == "123.45" and company["attribution_unknown_period_count"] == 1
    personal = actual_summary([fact(_attribution_excluded=True)], "actual_recognized_exclusive", scope_complete=True)
    assert personal["known_subtotal"] is None and personal["value"] is None and personal["source_refs"] == []
    assert personal["registered_fact_count"] == 0 and personal["attribution_unknown_period_count"] == 1
    assert personal["missing_reasons"] == ["actual_attribution_unresolved"]
    zero = actual_summary(
        [fact(amount="0", attribution_status="frozen")], "actual_recognized_exclusive", scope_complete=True
    )
    assert zero["value"] == "0.00"


def test_history_requires_actual_permission_and_only_projects_reviewed_fields():
    row = dict(
        id=str(uuid4()),
        actor_id=str(uuid4()),
        actor_name="Sales",
        version_no=1,
        created_at="2026-10-25T01:00:00Z",
        changes=[
            dict(
                field="recognized_actual",
                year=2026,
                quarter=4,
                before={"items": []},
                after={
                    "revision": "private",
                    "items": [{"material_name": "物料", "amount": "0.00", "source_ref": "验收单", "note": "季度累计"}],
                },
            )
        ],
    )
    hidden = change_document(row)
    assert hidden["fields"][0]["after"]["status"] == "restricted"
    shown = change_document(row, actual_allowed=True)
    assert "private" not in str(shown) and "0.00" in str(shown) and "验收单" in str(shown)
