"""Review route wiring and the coordinator/creator authorization entry gate."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from sales_backend.api import permission_gate, review_okr, review_overview, tasks
from sales_backend.domain.route_permissions import route_permission
from sales_backend.main import app


def test_new_handlers_are_mounted_and_classified():
    paths = app.openapi()["paths"]
    for router in (review_okr.router, review_overview.router, tasks.router):
        for route in router.routes:
            if route.endpoint.__module__.endswith((".review_okr", ".review_overview")) or route.endpoint.__name__ == "update_task_alignment":
                assert route.path in paths
                assert route_permission(route.endpoint.__module__.rsplit(".", 1)[-1], route.endpoint.__name__)


@pytest.mark.asyncio
@pytest.mark.parametrize("grants,allowed", [
    ({"task.coordinate"}, True),
    ({"task.cancel"}, True),
    ({"task.read"}, False),
    (set(), False),
])
async def test_alignment_gate_accepts_both_existing_coordination_routes(monkeypatch, grants, allowed):
    required = []

    class Permissions:
        def allows(self, code):
            return code in grants

        def require(self, code):
            required.append(code)
            assert code == "access.console"

    class Database:
        @asynccontextmanager
        async def transaction(self, actor, readonly=False):
            assert readonly
            yield object()

    async def identity(*args):
        return SimpleNamespace(actor=object(), client_channel="web", auth_method="password")

    async def snapshot(*args):
        return {}

    async def bearer(*args):
        return None

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    monkeypatch.setattr(permission_gate, "get_identity", identity)
    monkeypatch.setattr(permission_gate, "bearer", bearer)
    monkeypatch.setattr(permission_gate.AuthorizationRepository, "snapshot", snapshot)
    monkeypatch.setattr(permission_gate.AuthorizationRepository, "from_snapshot", lambda value: Permissions())
    request = Request({
        "type": "http", "method": "PATCH", "path": "/api/v1/tasks/example/alignment", "headers": [],
        "endpoint": tasks.update_task_alignment,
        "app": SimpleNamespace(state=SimpleNamespace(database=Database(), settings=object())),
    }, receive)
    gate = permission_gate.enforce_route_permission(request)
    if allowed:
        assert await anext(gate) is None
        await gate.aclose()
    else:
        with pytest.raises(PermissionError):
            await anext(gate)
    assert required == ["access.console"]
