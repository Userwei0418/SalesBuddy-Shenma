"""Public company naming never enumerates employees or broadens authenticated tenant scope."""

from dataclasses import replace
from uuid import uuid4

import pytest

from sales_backend.main import app
from tests.integration.feishu_fixtures import fixture_owner
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_platform_control import BASE, activate, provision, tenant, transition
from tests.integration.test_platform_control import platform as platform

pytestmark = pytest.mark.asyncio


async def test_exact_company_entry_returns_only_active_company_name_without_account_data(connection):
    async with await client_for(connection) as client:
        entry = await client.get("/api/v1/auth/company-entry", params={"company_code": " DEMO-SALES-WORKSPACE "})
        assert entry.status_code == 200, entry.text
        assert entry.json() == {"company_code": "demo-sales-workspace", "company_name": "隔离验收"}
        assert entry.headers["cache-control"] == "no-store"
        responses = []
        for code in ("", "demo", "missing-company", "demo-sales-workspace OR 1=1", "x" * 129):
            response = await client.get("/api/v1/auth/company-entry", params={"company_code": code})
            assert response.status_code == 404, response.text
            assert response.headers["cache-control"] == "no-store"
            responses.append(response.json())
        assert all(value == responses[0] for value in responses)


async def test_entry_network_throttle_prevents_code_enumeration_and_does_not_lock_member(connection):
    async with await client_for(connection) as client:
        for index in range(50):
            response = await client.get("/api/v1/auth/company-entry", params={"company_code": f"unknown-{index}"})
            assert response.status_code == 404
        blocked = await client.get("/api/v1/auth/company-entry", params={"company_code": "demo-sales-workspace"})
        assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
        assert (await sign_in(client, "ADMIN001")).status_code == 200


async def test_company_actor_is_canonical_on_login_refresh_and_me_without_otp(connection):
    async with await client_for(connection) as client:
        login = await sign_in(client, "ADMIN001")  # Compatibility: unique accounts may still omit company.
        expected = {"company_code": "demo-sales-workspace", "company_name": "隔离验收"}
        for response in (
            login,
            await client.get("/api/v1/console/auth/me"),
            await client.post("/api/v1/console/auth/refresh"),
        ):
            assert response.status_code == 200, response.text
            assert {key: response.json()["actor"][key] for key in expected} == expected
        native = await client.post(
            "/api/v1/auth/password/login",
            json={
                "account_code": "ADMIN001",
                "password": "Isolated-Testing-2026",
                "workspace": "demo-sales-workspace",
            },
        )
        assert native.status_code == 200, native.text
        client.headers["Authorization"] = "Bearer " + native.json()["access_token"]
        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert {key: me.json()[key] for key in expected} == expected
        refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": native.json()["refresh_token"]})
        assert refreshed.status_code == 200, refreshed.text
        assert {key: refreshed.json()["actor"][key] for key in expected} == expected
        # A company selected by a header never changes the authenticated account's company.
        client.headers["X-Company-ID"] = str(uuid4())
        assert (await client.get("/api/v1/auth/me")).json()["company_code"] == expected["company_code"]


async def test_wrong_company_and_bad_password_share_generic_login_failure(connection):
    async with await client_for(connection) as client:
        responses = []
        for code, account, password in [
            ("missing-company", "ADMIN001", "Isolated-Testing-2026"),
            ("demo-sales-workspace", "ADMIN001", "wrong-password"),
            ("demo-sales-workspace", "MISSINGUSER", "Isolated-Testing-2026"),
        ]:
            response = await client.post(
                "/api/v1/console/auth/login",
                json={
                    "workspace": code,
                    "account_code": account,
                    "password": password,
                },
            )
            assert response.status_code == 401
            responses.append(response.json())
        assert responses[0] == responses[1] == responses[2]
        assert "公司" in responses[0]["detail"] and "ADMIN001" not in responses[0]["detail"]


async def test_public_entry_hides_provisioning_suspended_and_closed_but_platform_links_are_scoped(
    platform, connection, monkeypatch
):
    client, _ = platform
    body, _, created = await provision(client)
    lookup = "/api/v1/auth/company-entry"
    params = {"company_code": body["company_code"]}
    assert (await client.get(lookup, params=params)).status_code == 404
    await activate(client, body, created)
    assert (await transition(client, await tenant(client, created["id"]), "active")).status_code == 200
    assert (await client.get(lookup, params=params)).json() == {
        "company_code": body["company_code"],
        "company_name": body["name"],
    }
    monkeypatch.setattr(app.state, "settings", replace(app.state.settings, public_web_origin="https://sales.example"))
    link = await client.get(BASE + "/tenants/" + created["id"] + "/entry")
    assert link.status_code == 200, link.text
    values = link.json()
    assert values["admin_path"] == "/admin?company=" + body["company_code"]
    assert values["activation_path"] == "/company-activate?company=" + body["company_code"]
    assert values["web_qr_svg"].startswith("<svg") and values["web_qr_unavailable_reason"] is None
    assert values["mini_program_scene"] == "c=" + body["company_code"]
    assert values["mini_program_qr_available"] is False
    assert (await client.get(BASE + "/tenants/" + str(uuid4()) + "/entry")).status_code == 404
    async with await client_for(connection) as anonymous:
        assert (await anonymous.get(BASE + "/tenants/" + created["id"] + "/entry")).status_code == 401
    # client_for changes app settings; link generation must fail closed for its untrusted host.
    absent = await client.get(BASE + "/tenants/" + created["id"] + "/entry")
    assert absent.status_code == 200
    assert absent.json()["web_qr_svg"] is None
    assert absent.json()["web_qr_unavailable_reason"] == "PUBLIC_WEB_ORIGIN_NOT_CONFIGURED"
    assert (await transition(client, await tenant(client, created["id"]), "suspended")).status_code == 200
    assert (await client.get(lookup, params=params)).status_code == 404
    assert (await transition(client, await tenant(client, created["id"]), "closed")).status_code == 200
    assert (await client.get(lookup, params=params)).status_code == 404


async def test_old_company_codes_are_exact_not_reinterpreted(connection):
    async with fixture_owner(connection):
        workspace_id = await connection.fetchval(
            "SELECT id FROM platform.workspace WHERE external_workspace_id='demo-sales-workspace'"
        )
        await connection.execute(
            "UPDATE platform.workspace SET external_workspace_id='legacy_code' WHERE id=$1", workspace_id
        )
    async with await client_for(connection) as client:
        result = await client.get("/api/v1/auth/company-entry", params={"company_code": "legacy_code"})
        assert result.status_code == 200 and result.json()["company_code"] == "legacy_code"


async def test_same_account_in_two_companies_requires_exact_company_without_cross_login(platform, connection):
    platform_client, _ = platform
    companies = []
    for _ in range(2):
        body, _, created = await provision(platform_client)
        await activate(platform_client, body, created)
        assert (
            await transition(platform_client, await tenant(platform_client, created["id"]), "active")
        ).status_code == 200
        companies.append((body, created))
    async with await client_for(connection) as member:
        ambiguous = await member.post(
            "/api/v1/console/auth/login", json={"account_code": "ADMIN", "password": "Isolated-platform-test-2026"}
        )
        assert ambiguous.status_code == 401
        assert all(body["company_code"] not in ambiguous.text for body, _ in companies)
        for body, created in companies:
            login = await member.post(
                "/api/v1/console/auth/login",
                json={
                    "workspace": body["company_code"],
                    "account_code": "ADMIN",
                    "password": "Isolated-platform-test-2026",
                },
            )
            assert login.status_code == 200, login.text
            assert login.json()["actor"]["workspace_id"] == created["id"]
            assert login.json()["actor"]["company_code"] == body["company_code"]
