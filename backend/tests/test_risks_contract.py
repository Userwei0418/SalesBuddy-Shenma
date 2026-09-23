from __future__ import annotations

import pytest
from pydantic import ValidationError

from sales_backend.api.models import RiskResolve
from sales_backend.main import app


def test_risk_routes_are_registered() -> None:
    paths = app.openapi()["paths"]
    assert "get" in paths["/api/v1/risks"]
    assert "get" in paths["/api/v1/risks/{risk_id}"]
    assert "post" in paths["/api/v1/risks/{risk_id}/resolve"]


def test_risk_resolution_requires_audit_note() -> None:
    assert RiskResolve(resolution_note="客户已确认后续方案和时间").resolution_note
    with pytest.raises(ValidationError):
        RiskResolve(resolution_note="已好")
