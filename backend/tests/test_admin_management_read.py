from contextlib import asynccontextmanager
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sales_backend.api.operations_accounts import router as accounts
from sales_backend.api.operations_reports import router as reports
from sales_backend.api.dependencies import get_database
from sales_backend.api.management_dependencies import get_password_identity
from sales_backend.domain.agent import ActorContext, RoleCode


@pytest.mark.parametrize("role", [RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER])
def test_business_roles_cannot_access_console(role):
    app = FastAPI()
    app.include_router(accounts)
    app.include_router(reports)
    app.dependency_overrides[get_password_identity] = lambda: SimpleNamespace(actor=SimpleNamespace(role=role))
    app.dependency_overrides[get_database] = lambda: None
    client = TestClient(app)
    for path in ["/organization", "/audit", "/ai/overview", "/system-events"]:
        assert client.get("/api/v1/console" + path).status_code == 403


def test_read_endpoints_derive_workspace_from_identity():
    actor = ActorContext(
        workspace_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        role=RoleCode.OPERATIONS,
        data_scope="workspace",
    )

    class DB:
        @asynccontextmanager
        async def transaction(self, actual, *, readonly):
            assert actual is actor and readonly
            yield self

        async def fetchval(self, sql, *args):
            if "count(*)" in sql.lower():
                return 0
            return {"must_change_password": False, "auth_method": "password"}

        async def fetch(self, sql, *args):
            return []

    app = FastAPI()
    app.include_router(accounts)
    app.include_router(reports)
    app.dependency_overrides[get_password_identity] = lambda: SimpleNamespace(
        actor=actor, session_id="s", auth_method="password", must_change_password=False,
    )
    app.dependency_overrides[get_database] = lambda: DB()
    client = TestClient(app)
    result = client.get("/api/v1/console/organization?workspace_id=other").json()
    assert result["departments"] == [] and result["accounts"] == [] and len(result["roles"]) == 7
    assert client.get("/api/v1/console/audit").json() == {"items": [], "total": 0}
    assert client.get("/api/v1/console/audit?limit=501").status_code == 422
