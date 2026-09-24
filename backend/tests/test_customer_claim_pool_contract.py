"""Native route ordering, request validation and reference-only response contract."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from sales_backend.api.customers import router
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.domain.agent import RoleCode


@pytest.fixture
def boundary(monkeypatch):
    class DirectoryDatabase:
        calls = []

        @asynccontextmanager
        async def transaction(self, actor, readonly=False):
            assert readonly
            yield self

        async def fetchval(self, sql, *args):
            self.calls.append((sql, args))
            if "directory_industries" in sql:
                return [{"value": "软件", "label": "软件"}]
            return {"items": [], "total": 137, "has_more": False, "next_offset": None}

    database = DirectoryDatabase()
    from sales_backend.api.permission_gate import enforce_route_permission
    from tests.authorization_fixtures import install_http_authorization
    person = SimpleNamespace(role=RoleCode.SALES, workspace_id="w", user_id="u", team_ids=())
    app = FastAPI(dependencies=[Depends(enforce_route_permission)])
    grants = {p: "workspace" for p in ("access.console", "customer.claim_directory")}
    _, identity = install_http_authorization(monkeypatch, app, person, grants)
    identity.grants = grants
    from fastapi.responses import JSONResponse
    @app.exception_handler(PermissionError)
    async def denied(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=403)
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
    assert database.calls == [("SELECT security.company_customer_directory_search($1,$2,$3,$4,$5,$6::text[])", ("客户", 50, 150, None, None, []))]


@pytest.mark.parametrize("params", [
    {"offset": -1}, {"offset": "x"}, {"offset": 2_147_483_648},
    {"industry": "业" * 101}, {"claim_status": "forged"},
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
    identity.grants.pop("customer.claim_directory")
    result = client.get("/api/v1/customers/claim-pool")
    assert result.status_code == 403
    assert not database.calls


def test_options_are_reference_authorized_and_filters_pass_through(boundary):
    client, database, identity = boundary
    options = client.get("/api/v1/customers/claim-pool/options")
    assert options.status_code == 200, options.text
    assert options.json()["industries"] == [{"value": "", "label": "全部行业"}, {"value": "软件", "label": "软件"}]
    result = client.get("/api/v1/customers/claim-pool", params={"q": "客户", "industry": "软件", "claim_status": "pending"})
    assert result.status_code == 200
    assert database.calls[-1][1] == ("客户", 50, 0, "软件", "pending", [])
    count = len(database.calls)
    identity.grants.pop("customer.claim_directory")
    assert client.get("/api/v1/customers/claim-pool/options").status_code == 403
    assert len(database.calls) == count
