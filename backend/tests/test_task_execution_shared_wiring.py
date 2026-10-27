from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from sales_backend.api import permission_gate, task_execution
from sales_backend.contracts.task_execution import COMMAND_MODELS
from sales_backend.domain.route_permissions import TASK_EXECUTION_ACTIONS
from sales_backend.main import app
from sales_backend.repositories.task_execution_projection import normalize_execution


@pytest.mark.parametrize("action", list(COMMAND_MODELS))
@pytest.mark.parametrize("has_action", [True, False])
@pytest.mark.asyncio
async def test_real_dispatcher_requires_surface_read_and_action(monkeypatch, action, has_action):
    codes = ["access.mini_program", "task.read"]
    if has_action:
        codes.append(TASK_EXECUTION_ACTIONS[action][0])
    snapshot = {
        "workspace_id": "company",
        "user_id": "user",
        "grants": [
            dict(
                permission_code=code,
                scope_code="workspace",
                team_ids=[],
                source_code="role",
                source_id="fixture",
                effect="allow",
            )
            for code in codes
        ],
    }

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
    request = Request(
        dict(
            type="http",
            method="POST",
            path="/api/v1/tasks/task/execution/" + action,
            path_params={"action": action},
            headers=[],
            endpoint=task_execution.execution_command,
            app=SimpleNamespace(
                state=SimpleNamespace(database=SimpleNamespace(transaction=transaction), settings=None)
            ),
        )
    )
    request._json = {}
    gate = permission_gate.enforce_route_permission(request)
    try:
        # A creator may cancel their own V2 task with read access even without
        # a role-wide cancel grant. The locked DB command checks creator/deny.
        if has_action or action == "cancel_task":
            await anext(gate)
        else:
            with pytest.raises(PermissionError):
                await anext(gate)
    finally:
        await gate.aclose()


def test_openapi_publishes_every_validated_command_and_required_key():
    schema = app.openapi()
    operation = schema["paths"]["/api/v1/tasks/{task_id}/execution/{action}"]["post"]
    assert next(p for p in operation["parameters"] if p["name"] == "Idempotency-Key")["required"]
    bodies = operation["requestBody"]["content"]["application/json"]["schema"]["x-action-schemas"]
    assert set(bodies) == set(COMMAND_MODELS) == set(TASK_EXECUTION_ACTIONS)
    for ref in bodies.values():
        model = schema["components"]["schemas"][ref["$ref"].split("/")[-1]]
        assert {"version_no", "requirements_version", "responsibility_version"} <= set(model["required"])
        assert model["additionalProperties"] is False


@pytest.mark.parametrize(
    "status,pending", [("pending", True), ("accepted", False), ("rejected", False), ("invalidated", False)]
)
def test_feedback_and_historical_handover_cannot_invent_current_action(status, pending):
    item = normalize_execution(
        {
            "execution_mode": "execution_v1",
            "execution": {
                "reviewer_handover": {"status": status},
                "execution_handover": {"status": status},
                "progress": {"percent": None, "basis": "missing_checklist"},
                "last_execution_feedback_at": "2026-10-17T01:01:01Z",
                "status": "in_progress",
            },
            "action_permissions": {"task.complete": True, "task.cancel": True},
        }
    )
    assert item["execution_flags"]["reviewer_handover_pending"] is pending
    assert item["execution_flags"]["execution_handover_pending"] is pending
    assert item["progress"]["percent"] is None
    assert not item["action_permissions"]["task.complete"]
    assert item["action_permissions"]["task.cancel"]
    assert not item["execution_flags"]["awaiting_review"]
