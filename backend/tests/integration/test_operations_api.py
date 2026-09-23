"""HTTP + real PostgreSQL with the non-bypass runtime role; no endpoint/repository stubs."""

from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from sales_backend.config import get_settings
from sales_backend.db import set_request_context
from sales_backend.main import app

pytestmark = pytest.mark.asyncio


class TransactionDatabase:
    def __init__(self, connection):
        self.conn = connection

    @asynccontextmanager
    async def transaction(self, actor, readonly=False):
        async with self.conn.transaction():
            await set_request_context(self.conn, actor)
            yield self.conn

    @asynccontextmanager
    async def connection(self):
        yield self.conn


async def client_for(connection, *, auth_mode="password"):
    app.state.database = TransactionDatabase(connection)
    app.state.settings = replace(
        get_settings(),
        app_env="test",
        auth_mode=auth_mode,
        access_token_secret="isolated-testing-signing-secret-not-for-production",
        demo_workspace="demo-sales-workspace",
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test.invalid",
        headers={"Origin": "https://test.invalid"},
    )


async def sign_in(client, code="OPS001", password="Isolated-Testing-2026"):
    result = await client.post("/api/v1/console/auth/login", json={"account_code": code, "password": password})
    assert result.status_code == 200, result.text
    client.headers["Authorization"] = "Bearer " + result.json()["access_token"]
    return result


async def test_console_password_session_and_reads(connection):
    async with await client_for(connection) as client:
        response = await sign_in(client)
        audit = await connection.fetchval("SELECT count(*) FROM ops.audit_log WHERE object_label='POST /api/v1/console/auth/login' AND result_code='success'")
        assert audit == 1
        assert "HttpOnly" in response.headers["set-cookie"] and "Secure" in response.headers["set-cookie"]
        for path in [
            "/auth/me",
            "/summary",
            "/organization",
            "/customers",
            "/claims",
            "/opportunities",
            "/ai/overview",
            "/ai/calls",
            "/ai/rules",
            "/audit",
            "/system-events",
        ]:
            response = await client.get("/api/v1/console" + path)
            assert response.status_code == 200, (path, response.text)
        for path in [
            "/customers/export",
            "/opportunities/export",
            "/ai/calls/export",
            "/ai/roles/export",
            "/audit/export",
            "/system-events/export",
        ]:
            response = await client.get("/api/v1/console" + path)
            assert response.status_code == 200, (path, response.text)
            assert response.content.startswith(b"\xef\xbb\xbf")
        refresh = await client.post("/api/v1/console/auth/refresh")
        assert refresh.status_code == 200, refresh.text
        client.headers["Authorization"] = "Bearer " + refresh.json()["access_token"]
        assert (await client.get("/api/v1/console/organization")).status_code == 200
        assert (await client.post("/api/v1/console/auth/logout")).status_code == 200
        assert (await client.get("/api/v1/console/organization")).status_code == 401


async def test_create_account_change_password_and_ops_boundary(connection):
    async with await client_for(connection) as client:
        await sign_in(client)
        org = (await client.get("/api/v1/console/organization")).json()
        key = str(uuid4())
        account = "API" + uuid4().hex[:12]
        body = dict(
            account_code=account,
            display_name="隔离API用户",
            team_id=org["departments"][0]["id"],
            roles=["sales"],
            temporary_password="Isolated-User-2026",
        )
        created = await client.post("/api/v1/console/accounts", json=body, headers={"Idempotency-Key": key})
        assert created.status_code == 201, created.text
        replay = await client.post("/api/v1/console/accounts", json=body, headers={"Idempotency-Key": key})
        assert replay.json() == created.json()
        response = await sign_in(client, account, "Isolated-User-2026")
        assert response.json()["must_change_password"]
        assert (await client.get("/api/v1/console/organization")).status_code == 403
        assert (
            await client.post(
                "/api/v1/console/auth/password",
                json={"old_password": "Isolated-User-2026", "new_password": "Isolated-Changed-2026"},
            )
        ).status_code == 200
        assert (await client.get("/api/v1/console/auth/me")).json()["must_change_password"] is False
        assert (await client.get("/api/v1/console/organization")).status_code == 403
        # A business role cannot use an ordinary customer endpoint to bypass ops creation.
        payload = dict(
            name="Denied",
            company_reference="x",
            industry="",
            customer_type="潜在客户",
            level_code="Tier-2",
            source="其他",
            target_team="南区",
            partner_name="",
            contact_name="测试",
            contact_title="经理",
            contact_role="使用者",
        )
        assert (await client.post("/api/v1/customers", json=payload)).status_code == 403


async def test_origin_required_and_failed_auth_has_no_cookie(connection):
    async with await client_for(connection) as client:
        r = await client.post(
            "/api/v1/console/auth/login",
            headers={"Origin": "https://evil.invalid"},
            json={"account_code": "OPS001", "password": "Isolated-Testing-2026"},
        )
        assert r.status_code == 403 and "set-cookie" not in r.headers
        r = await client.post(
            "/api/v1/console/auth/login", json={"account_code": "OPS001", "password": "incorrect-value"}
        )
        assert r.status_code == 401 and "set-cookie" not in r.headers


async def test_role_change_revokes_access_and_refresh_sessions(connection):
    async with await client_for(connection) as admin, await client_for(connection) as user:
        await sign_in(admin, "ADMIN001")
        org = (await admin.get("/api/v1/console/organization")).json()
        team = org["departments"][0]["id"]
        code = "ROLE" + uuid4().hex[:10]
        created = await admin.post("/api/v1/console/accounts", headers={"Idempotency-Key": str(uuid4())}, json=dict(
            account_code=code, display_name="角色变更验收", team_id=team, roles=["operations"],
            temporary_password="Isolated-Role-2026"))
        assert created.status_code == 201, created.text
        await sign_in(user, code, "Isolated-Role-2026")
        assert (await user.get("/api/v1/console/auth/me")).status_code == 200
        updated = await admin.put("/api/v1/console/accounts/" + created.json()["id"],
            headers={"Idempotency-Key": str(uuid4())}, json=dict(version_no=1, display_name="角色变更验收",
            team_id=team, roles=["sales"], status="active"))
        assert updated.status_code == 200, updated.text
        assert (await user.get("/api/v1/console/auth/me")).status_code == 401
        assert (await user.post("/api/v1/console/auth/refresh")).status_code == 401
