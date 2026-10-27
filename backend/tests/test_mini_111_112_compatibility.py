"""Exercise legacy and reviewed mini-program request shapes through HTTP."""
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from sales_backend.api import customer_assets
from sales_backend.api.dependencies import get_database, get_identity


@pytest.fixture
def client(monkeypatch):
    calls = []

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield object()

    async def read(*args, **kwargs):
        calls.append(kwargs)
        return []

    async def refresh(*args, **kwargs):
        calls.append(kwargs)
        return {"status": "ready", "enqueued_count": 0}

    async def mutation(connection, actor, key, action, payload, operation):
        return await operation()

    app = FastAPI()
    app.include_router(customer_assets.router)
    app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=object())
    app.dependency_overrides[get_database] = lambda: SimpleNamespace(transaction=transaction)
    monkeypatch.setattr(customer_assets, "today", lambda: date(2026, 10, 8))
    monkeypatch.setattr(customer_assets, "read_map", read)
    monkeypatch.setattr(customer_assets, "refresh_missing_map", refresh)
    monkeypatch.setattr(customer_assets, "execute_mutation", mutation)
    with TestClient(app) as http:
        yield http, calls


@pytest.mark.parametrize("query,window,start", [
    ("", "year", "2026-01-01"),
    ("?scope=self", "year", "2026-01-01"),
    ("?activity_window=today", "today", "2026-10-08"),
    ("?activity_window=3d", "3d", "2026-10-06"),
    ("?activity_window=week", "week", "2026-10-05"),
    ("?activity_window=month", "month", "2026-10-01"),
    ("?activity_window=quarter", "quarter", "2026-10-01"),
])
def test_legacy_annual_and_explicit_new_windows_match_on_read_and_refresh(client, query, window, start):
    http, calls = client
    response = http.get("/api/v1/customer-assets/map" + query)
    assert response.status_code == 200
    body = response.json()
    assert body["activity_since"] == body["activity_start"] == start
    assert body["activity_end"] == body["as_of"] == "2026-10-08"
    assert body["activity_window"] == window
    assert http.post("/api/v1/customer-assets/map/refresh" + query).status_code == 200
    assert [call["activity_window"] for call in calls] == [window, window]


def test_invalid_new_window_cannot_silently_fall_back_to_annual(client):
    http, calls = client
    for method, path in [(http.get, "/map"), (http.post, "/map/refresh")]:
        assert method("/api/v1/customer-assets" + path + "?activity_window=unsupported-window").status_code == 422
    assert calls == []


def test_scope_denial_remains_enforced(client, monkeypatch):
    async def deny(*args, **kwargs):
        raise PermissionError("outside authorized scope")
    monkeypatch.setattr(customer_assets, "read_map", deny)
    assert client[0].get("/api/v1/customer-assets/map?activity_window=today&scope=team").status_code == 403
