from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from sales_backend.api import connectivity, model_api
from sales_backend.api.dependencies import get_settings as api_settings
from sales_backend.api.management_dependencies import get_system_identity
from sales_backend.api.model_connection_lock import LOCK_MESSAGE
from sales_backend.config import get_settings
from sales_backend.domain.agent import ActorContext
from sales_backend.main import app
from tests.authorization_fixtures import install_http_authorization


@pytest.fixture
def locked_client(monkeypatch):
    actor = ActorContext(workspace_id=str(uuid4()), user_id=str(uuid4()), role="administrator", data_scope="workspace")
    permissions = {code: "workspace" for code in (
        "access.console", "ai.config_read", "ai.config_test", "ai.config_publish", "ai.config_rollback",
        "authorization.roles_manage", "authorization.accounts_manage",
    )}
    _, identity = install_http_authorization(monkeypatch, app, actor, permissions)
    settings = replace(get_settings(), model_connections_locked=True)
    app.dependency_overrides[api_settings] = lambda: settings
    app.dependency_overrides[get_system_identity] = lambda: identity
    try:
        yield TestClient(app), settings
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/v1/admin/model-apis/text/tests"),
    ("POST", "/api/v1/admin/model-apis/asr/tests"),
    ("POST", "/api/v1/admin/model-apis/tts/publish"),
    ("PUT", "/api/v1/admin/agent-config"),
    ("POST", "/api/v1/admin/agent-config/releases/1/rollback"),
    ("POST", "/api/v1/admin/ai-connectivity/direct/text/tests"),
])
def test_deployment_lock_overrides_full_admin_grants_without_business_query(locked_client, method, path):
    client, _ = locked_client
    response = client.request(method, path, json={"api_key": "synthetic-never-echo"})
    assert response.status_code == 403
    assert response.json()["detail"] == LOCK_MESSAGE
    assert "synthetic-never-echo" not in response.text


def test_locked_page_can_read_safe_config(locked_client):
    client, settings = locked_client
    service = SimpleNamespace(settings=settings, list=AsyncMock(return_value={"items": []}))
    app.dependency_overrides[model_api.service] = lambda: service
    response = client.get("/api/v1/admin/model-apis")
    assert response.status_code == 200
    assert response.json() == {"items": [], "locked": True, "lock_message": LOCK_MESSAGE}


def test_agent_connectivity_route_is_not_blocked(locked_client):
    client, _ = locked_client
    service = SimpleNamespace(run=AsyncMock(return_value={"status": "passed"}))
    app.dependency_overrides[connectivity.manager] = lambda: service
    response = client.post("/api/v1/admin/ai-connectivity/agent/weekly/tests", json={"request_id": str(uuid4())})
    assert response.status_code == 200
    service.run.assert_awaited_once()


def test_unlocked_deployment_retains_existing_direct_probe(locked_client):
    client, settings = locked_client
    app.dependency_overrides[api_settings] = lambda: replace(settings, model_connections_locked=False)
    service = SimpleNamespace(run=AsyncMock(return_value={"status": "passed"}))
    app.dependency_overrides[connectivity.manager] = lambda: service
    response = client.post("/api/v1/admin/ai-connectivity/direct/text/tests", json={"request_id": str(uuid4())})
    assert response.status_code == 200
    service.run.assert_awaited_once()
