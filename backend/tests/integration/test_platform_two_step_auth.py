"""Password/MFA stages use restricted SQL and rotate the browser proof on promotion."""

import base64
import time
from uuid import uuid4

import pytest

from sales_backend.auth.passwords import encode_password
from sales_backend.security.platform_mfa import authenticator
from sales_backend.services.platform_control import digest
from tests.integration.feishu_fixtures import fixture_owner
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_platform_control import BASE, PASSWORD
from tests.integration.test_platform_control import platform as platform

pytestmark = pytest.mark.asyncio
COOKIE = "sales_platform_session"


async def start(client, account, password=PASSWORD):
    return await client.post(BASE + "/auth/start", json={"account_code": account, "password": password})


async def test_two_step_existing_operator_rotates_token_and_never_grants_password_only_authority(platform, connection):
    client, database = platform
    company = await sign_in(client, "ADMIN001")
    company_cookie = client.cookies.get("sales_console_refresh")
    response = await start(client, database.account)
    assert response.status_code == 200, response.text
    assert response.json()["operator"]["mfa_enrolled"] is True
    assert response.json()["operator"]["mfa_verified"] is False
    assert 0 < response.json()["expires_in"] <= 300
    assert "Max-Age=" + str(response.json()["expires_in"]) in response.headers["set-cookie"]
    restricted = client.cookies.get(COOKIE)
    assert (await client.get(BASE + "/tenants")).status_code == 403
    assert (await client.get(BASE + "/auth/me")).status_code == 200
    assert (await client.post(BASE + "/auth/mfa/enroll", json={})).status_code == 403
    next_code = authenticator(database.seed).generate(time.time() + 30).decode()
    # The old sensitive-action API must not become a no-rotation login bypass.
    assert (await client.post(BASE + "/auth/verify", json={"password": PASSWORD, "otp": next_code})).status_code == 403
    rejected = await client.post(BASE + "/auth/mfa/login", json={"otp": "abcdef"})
    assert rejected.status_code == 403
    assert client.cookies.get(COOKIE) == restricted
    assert (await client.get(BASE + "/tenants")).status_code == 403
    completed = await client.post(BASE + "/auth/mfa/login", json={"otp": next_code})
    assert completed.status_code == 200, completed.text
    assert completed.json()["operator"]["mfa_verified"] is True
    assert 1795 <= completed.json()["expires_in"] <= 1800
    assert client.cookies.get(COOKIE) != restricted
    assert (await client.get(BASE + "/tenants")).status_code == 200
    assert client.cookies.get("sales_console_refresh") == company_cookie
    client.headers["Authorization"] = "Bearer " + company.json()["access_token"]
    assert (await client.get("/api/v1/console/auth/me")).status_code == 200
    async with fixture_owner(connection):
        assert await connection.fetchval(
            "SELECT revoked_at IS NOT NULL FROM control.session WHERE token_hash=$1", digest(restricted)
        )
    async with await client_for(connection) as old:
        old.cookies.set(COOKIE, restricted, path="/api/v1/platform")
        assert (await old.get(BASE + "/auth/me")).status_code == 401
        assert (await old.post(BASE + "/auth/mfa/login", json={"otp": next_code})).status_code == 401
    assert (await client.post(BASE + "/auth/logout")).status_code == 200
    assert (await client.get("/api/v1/console/auth/me")).status_code == 200


async def test_first_login_binds_from_restricted_stage_with_local_qr_and_rotates_token(platform, connection):
    account = "FIRST" + uuid4().hex[:12].upper()
    async with fixture_owner(connection):
        await connection.execute(
            "INSERT INTO control.operator(account_code,display_name,password_hash) VALUES($1,$1,$2)",
            account,
            encode_password(PASSWORD),
        )
    async with await client_for(connection) as client:
        response = await start(client, account)
        assert response.status_code == 200, response.text
        assert response.json()["operator"]["mfa_enrolled"] is False
        assert response.json()["operator"]["mfa_verified"] is False
        restricted = client.cookies.get(COOKIE)
        assert response.json()["expires_in"] <= 300
        assert (await client.post(BASE + "/auth/mfa/login", json={"otp": "123456"})).status_code == 403
        prepare = await client.post(BASE + "/auth/mfa/enroll", json={})
        assert prepare.status_code == 200, prepare.text
        data = prepare.json()
        assert data["uri"].startswith("otpauth://totp/")
        assert data["qr_svg"].startswith("<svg") and "href=" not in data["qr_svg"]
        assert prepare.headers["cache-control"] == "no-store"
        seed = base64.b32decode(data["secret"])
        code = authenticator(seed).generate(time.time()).decode()
        confirmed = await client.post(BASE + "/auth/mfa/confirm", json={"otp": code})
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["operator"]["mfa_enrolled"] is True
        assert confirmed.json()["operator"]["mfa_verified"] is True
        assert 1795 <= confirmed.json()["expires_in"] <= 1800
        assert restricted != client.cookies.get(COOKIE)
        assert (await client.get(BASE + "/tenants")).status_code == 200
        async with fixture_owner(connection):
            assert await connection.fetchval(
                "SELECT revoked_at IS NOT NULL FROM control.session WHERE token_hash=$1", digest(restricted)
            )
        # Enrollment cannot be re-used to replace a configured secret.
        assert (await client.post(BASE + "/auth/mfa/confirm", json={"otp": code})).status_code == 403


async def test_expired_password_stage_cannot_be_promoted(platform, connection):
    client, database = platform
    assert (await start(client, database.account)).status_code == 200
    token = client.cookies.get(COOKIE)
    async with fixture_owner(connection):
        await connection.execute(
            "UPDATE control.session SET expires_at=clock_timestamp()-interval '1 second' WHERE token_hash=$1",
            digest(token),
        )
    response = await client.post(
        BASE + "/auth/mfa/login",
        json={
            "otp": authenticator(database.seed).generate(time.time() + 30).decode(),
        },
    )
    assert response.status_code == 401
    assert (await client.get(BASE + "/tenants")).status_code == 401


async def test_wrong_password_never_exposes_operator_enrollment_or_issues_cookie(platform):
    client, database = platform
    old = client.cookies.get(COOKIE)
    actual = await start(client, database.account, "Incorrect-password")
    absent = await start(client, "ABSENT" + uuid4().hex[:12], "Incorrect-password")
    assert actual.status_code == absent.status_code == 401
    assert actual.json() == absent.json() == {"detail": "PLATFORM_LOGIN_FAILED"}
    assert "set-cookie" not in actual.headers and "set-cookie" not in absent.headers
    assert client.cookies.get(COOKIE) == old


async def test_stage_mfa_failures_are_limited_across_new_password_sessions(platform):
    client, database = platform
    for _ in range(5):
        assert (await start(client, database.account)).status_code == 200
        assert (await client.post(BASE + "/auth/mfa/login", json={"otp": "abcdef"})).status_code == 403
    assert (await start(client, database.account)).status_code == 200
    assert (await client.post(BASE + "/auth/mfa/login", json={"otp": "abcdef"})).status_code == 429
    assert (await client.get(BASE + "/tenants")).status_code == 403
