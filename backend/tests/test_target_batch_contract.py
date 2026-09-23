from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from sales_backend.contracts.targets import TargetBatchSave, TargetDecision
from sales_backend.main import app
from sales_backend.services.targets import read_targets, save_target_batch, target_period


def payload(**extra):
    return {"anchor_date": "2026-09-15", "reason": "  与负责人确认季度目标  ",
            "items": [{"kind": "collection", "amount": "1000000.01"},
                      {"kind": "recognized", "amount": "2000000.02", "version_no": 3}], **extra}


def test_batch_preserves_decimal_reason_versions_and_quarter():
    body = TargetBatchSave(**payload())
    assert body.reason == "与负责人确认季度目标"
    assert body.items[0].amount == Decimal("1000000.01")
    assert body.items[0].model_dump(mode="json")["amount"] == "1000000.01"
    assert body.items[0].version_no is None and body.items[1].version_no == 3
    assert target_period(body.period_type, body.anchor_date) == {
        "type": "quarter", "start": date(2026, 7, 1), "end": date(2026, 9, 30)}


@pytest.mark.parametrize("extra", [
    {"reason": "  "}, {"reason": "a" * 2001}, {"items": []},
    {"items": [{"kind": "collection", "amount": 1}] * 2},
    {"items": [{"kind": "visit_count", "amount": "0.5"}]},
    {"items": [{"kind": "collection", "amount": "1.001"}]},
    {"items": [{"kind": "collection", "amount": "NaN"}]},
    {"items": [{"kind": "collection", "amount": 0}]},
    {"items": [{"kind": "collection", "amount": 1, "version_no": 2147483648}]},
    {"items": [{"kind": "collection", "amount": 1, "approved": True}]},
    {"scope": "person"}, {"scope": "team"}, {"scope": "self", "department_code": "fde"},
])
def test_batch_rejects_invalid_or_ambiguous_requests(extra):
    with pytest.raises(ValidationError):
        TargetBatchSave(**payload(**extra))


def test_rejection_requires_an_explanation():
    with pytest.raises(ValidationError):
        TargetDecision(decision="rejected", reason=" ")


def test_batch_endpoints_use_the_strict_form_contract():
    paths = app.openapi()["paths"]
    for path in ("/api/v1/targets/batch", "/api/v1/console/targets/batch"):
        schema = paths[path]["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith("/TargetBatchSave")
    assert "/api/v1/console/target-batches/{request_id}/decision" in paths


@pytest.mark.asyncio
@pytest.mark.parametrize("amount, expected", [("1.2300", "1.23"), ("1E+3", "1000.00"), ("1000000.01", "1000000.01")])
async def test_batch_normalizes_valid_decimal_input_for_database(monkeypatch, amount, expected):
    from sales_backend.repositories.targets import TargetRepository

    save = AsyncMock(return_value={"status": "effective"})
    monkeypatch.setattr(TargetRepository, "save_batch", save)
    body = TargetBatchSave(**payload(items=[{"kind": "collection", "amount": amount}]))
    await save_target_batch(object(), SimpleNamespace(user_id="self"), body)
    assert save.call_args.kwargs["items"][0]["amount"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("role, period_type, anchor, expected", [
    ("sales", "quarter", date(2026, 9, 15), True),
    ("sales", "quarter", date(2026, 6, 15), False),
    ("sales", "year", date(2026, 9, 15), False),
    ("operations", "year", date(2026, 9, 15), True),
])
async def test_target_read_editability_matches_current_quarter_limit(monkeypatch, role, period_type, anchor, expected):
    from sales_backend.repositories.targets import TargetRepository

    for name in ("list", "requests", "batches"):
        monkeypatch.setattr(TargetRepository, name, AsyncMock(return_value={"items": [], "total": 0}))
    monkeypatch.setattr(TargetRepository, "allowed", AsyncMock(return_value=True))
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=date(2026, 9, 15)))
    actor = SimpleNamespace(user_id="self", role=SimpleNamespace(value=role))
    result = await read_targets(connection, actor, period_type=period_type, anchor_date=anchor)
    assert result["editable"] is expected
