from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from sales_backend.api import feishu_sync
from sales_backend.api.dependencies import get_database
from sales_backend.api.permission_gate import enforce_route_permission
from tests.authorization_fixtures import install_http_authorization
from sales_backend.api.management_dependencies import get_password_identity
from sales_backend.domain.agent import RoleCode


@pytest.fixture
def console(monkeypatch):
    app = FastAPI(dependencies=[Depends(enforce_route_permission)])
    app.include_router(feishu_sync.router)
    person = SimpleNamespace(role=RoleCode.OPERATIONS, workspace_id="company", user_id="operator", team_ids=())
    grants = {p: "workspace" for p in ("access.console", "feishu.read", "feishu.configure", "feishu.control", "feishu.recover")}
    db, identity = install_http_authorization(monkeypatch, app, person, grants)
    identity.grants = grants
    app.dependency_overrides[get_password_identity] = lambda: identity
    app.dependency_overrides[get_database] = lambda: db
    from fastapi.responses import JSONResponse
    @app.exception_handler(PermissionError)
    async def denied(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=403)
    repository = SimpleNamespace(config=AsyncMock(), action=AsyncMock(), initialize=AsyncMock())
    monkeypatch.setattr(feishu_sync, "repository", repository)
    return TestClient(app), identity, repository


def test_non_management_role_cannot_read_or_enable(console):
    client, identity, repo = console
    identity.actor.role = RoleCode.SALES
    identity.grants.clear()
    assert client.get("/api/v1/console/feishu-sync").status_code == 403
    assert client.post("/api/v1/console/feishu-sync/enable", json={"expected_revision": 1}).status_code == 403
    repo.config.assert_not_called()


@pytest.mark.parametrize(
    "credential,validated,revision,expected",
    [(False, None, 1, 422), (True, None, 1, 422), (True, 1, 2, 409), (True, 1, 1, 200)],
)
def test_enable_requires_current_validated_credential(console, credential, validated, revision, expected):
    client, _, repo = console
    repo.config.return_value = {
        "id": "connection",
        "revision": revision,
        "has_credential": credential,
        "validated_revision": validated,
        "settings": {},
    }
    response = client.post("/api/v1/console/feishu-sync/enable", json={"expected_revision": 1})
    assert response.status_code == expected
    assert repo.action.await_count == (1 if expected == 200 else 0)


@pytest.mark.parametrize("action", ["validate", "enable", "initialize", "pause"])
def test_reserved_direction_cannot_run_but_can_be_paused(console, action):
    client, _, repo = console
    repo.config.return_value = {
        "id": "connection", "revision": 1, "has_credential": True,
        "validated_revision": 1, "settings": {"direction": "bidirectional"},
    }
    response = client.post(f"/api/v1/console/feishu-sync/{action}", json={"expected_revision": 1})
    assert response.status_code == (200 if action == "pause" else 422)
    if action != "pause":
        assert "双向同步仅为配置预留" in response.json()["detail"]
    assert repo.action.await_count == (1 if action == "pause" else 0)
    repo.initialize.assert_not_awaited()


def test_reserved_direction_can_be_saved_as_paused_draft(console):
    from tests.test_feishu_sync_policy import payload

    client, identity, repo = console
    data = payload()
    data.update(direction="bidirectional", enabled=False)
    identity.actor.workspace_id = data["workspace_id"]
    repo.save = AsyncMock()
    response = client.put("/api/v1/console/feishu-sync", json={"expected_revision": 0, "config": data})
    assert response.status_code == 200
    assert response.json() == {"saved": True, "revision": 1, "enabled": False}
    config = repo.save.await_args.args[2]
    assert config.direction == "bidirectional" and config.enabled is False
    repo.action.assert_not_awaited()


def test_read_does_not_return_stored_credential(console):
    client, _, repo = console
    repo.config.return_value = {
        "enabled": False,
        "settings": {},
        "has_credential": True,
        "validated_revision": None,
        "validation_requested": False,
        "last_error_code": None,
        "ciphertext": "never expose",
        "encryption_key_id": "private",
    }
    response = client.get("/api/v1/console/feishu-sync?workspace_id=other")
    assert response.status_code == 200
    assert "never expose" not in response.text and "encryption_key_id" not in response.text
    assert repo.config.call_args.args[1] == "company"


@pytest.mark.parametrize('enabled,expected', [(True, 422), (False, 200)])
def test_recovery_requires_paused_current_company(console, enabled, expected):
    client, _, repo = console
    repo.recover = AsyncMock()
    repo.config.return_value = {'id': 'connection', 'revision': 2, 'enabled': enabled}
    response = client.post('/api/v1/console/feishu-sync/recover', json={
        'expected_revision': 2, 'event_id': '00000000-0000-0000-0000-000000000001',
        'action': 'suppress', 'delivery_key': 'known-key', 'note': 'checked target group',
    })
    assert response.status_code == expected
    assert repo.recover.await_count == (expected == 200)


def test_recovery_hides_foreign_event_and_rejects_unknown_action(console):
    from asyncpg import InsufficientPrivilegeError

    client, _, repo = console
    repo.config.return_value = {'id': 'connection', 'revision': 2, 'enabled': False}
    repo.recover = AsyncMock(side_effect=InsufficientPrivilegeError())
    body = {'expected_revision': 2, 'event_id': '00000000-0000-0000-0000-000000000001',
            'action': 'retry', 'note': 'after investigation'}
    assert client.post('/api/v1/console/feishu-sync/recover', json=body).status_code == 404
    body['action'] = 'resend_unknown'
    assert client.post('/api/v1/console/feishu-sync/recover', json=body).status_code == 422
