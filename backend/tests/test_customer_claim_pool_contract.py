"""Native route ordering, request validation and reference-only response contract."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sales_backend.api.customers import router
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.domain.agent import RoleCode


@pytest.fixture
def boundary():
    class DirectoryDatabase:
        calls = []

        @asynccontextmanager
        async def transaction(self, actor, readonly=False):
            assert readonly
            yield self

        async def fetchval(self, sql, *args):
            self.calls.append((sql, args))
            return {"items": [], "total": 137, "has_more": False, "next_offset": None}

    database = DirectoryDatabase()
    identity = SimpleNamespace(actor=SimpleNamespace(role=RoleCode.SALES))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_identity] = lambda: identity
    with TestClient(app) as client:
        yield client, database, identity


def test_static_claim_pool_route_wins_over_uuid_detail_and_preserves_empty_page_total(boundary):
    client, database, _ = boundary
    result = client.get("/api/v1/customers/claim-pool", params={"q": "  客户  ", "page_size": 50, "offset": 150})
    assert result.status_code == 200, result.text
    assert result.json() == {"items": [], "total": 137, "has_more": False, "next_offset": None}
    assert database.calls == [("SELECT security.company_customer_directory_page($1,$2,$3)", ("客户", 50, 150))]


@pytest.mark.parametrize("params", [
    {"offset": -1}, {"offset": "x"}, {"offset": 2_147_483_648},
    {"page_size": 0}, {"page_size": 101}, {"q": "客" * 101},
])
def test_invalid_pagination_is_422_before_any_directory_query(boundary, params):
    client, database, _ = boundary
    result = client.get("/api/v1/customers/claim-pool", params=params)
    assert result.status_code == 422
    assert not database.calls


@pytest.mark.parametrize("role", [RoleCode.FDE, RoleCode.FDE_LEAD])
def test_claim_directory_does_not_expand_fde_customer_selection(boundary, role):
    client, database, identity = boundary
    identity.actor.role = role
    result = client.get("/api/v1/customers/claim-pool")
    assert result.status_code == 403
    assert not database.calls
