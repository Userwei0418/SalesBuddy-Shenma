"""Exercise the real route gate with synthetic permission snapshots; no DB/HTTP."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from sales_backend.api import permission_gate, risks
from sales_backend.domain.route_permissions import route_permission


@pytest.mark.asyncio
@pytest.mark.parametrize("permission,deny,allowed", [
    ("risk.read", False, False),
    ("risk.resolve", False, False),
    ("risk.decide", False, True),
    ("risk.decide", True, False),
])
async def test_risk_decision_requires_exact_action_and_honors_deny(monkeypatch, permission, deny, allowed):
    assert route_permission("risks", "decide_risk") == "risk.decide"
    snapshot = {"workspace_id": "company", "user_id": "user", "grants": [
        {"permission_code": code, "scope_code": "workspace", "team_ids": [],
         "source_code": "role", "source_id": "synthetic", "effect": "allow"}
        for code in ("access.mini_program", permission)
    ]}
    if deny:
        snapshot["grants"].append({"permission_code": "risk.decide", "effect": "deny"})

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield object()

    async def identity(*args):
        return SimpleNamespace(actor=object(), client_channel="mini_program", auth_method="password")

    async def bearer(*args):
        return None

    async def read_snapshot(*args):
        return snapshot

    monkeypatch.setattr(permission_gate, "get_identity", identity)
    monkeypatch.setattr(permission_gate, "bearer", bearer)
    monkeypatch.setattr(permission_gate.AuthorizationRepository, "snapshot", read_snapshot)
    app = SimpleNamespace(state=SimpleNamespace(database=SimpleNamespace(transaction=transaction), settings=None))
    request = Request({"type": "http", "method": "POST", "path": "/api/v1/risks/synthetic/decision",
                       "headers": [], "app": app, "endpoint": risks.decide_risk})
    gate = permission_gate.enforce_route_permission(request)
    try:
        if allowed:
            await anext(gate)
        else:
            with pytest.raises(PermissionError):
                await anext(gate)
    finally:
        await gate.aclose()
