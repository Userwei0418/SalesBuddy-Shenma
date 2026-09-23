"""Native mini-program and Web share real credentials, with distinct token transports."""

from uuid import uuid4

import pytest

from tests.integration.test_operations_api import client_for, sign_in

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    ("initial_password", "new_password"),
    [("Native-Initial-2026", "Native-Changed-2026"), ("abcdefgh", "12345678")],
    ids=["mixed-characters", "letters-to-digits"],
)
async def test_native_password_first_login_change_refresh_and_role_boundary(
    connection, initial_password, new_password
):
    async with await client_for(connection) as admin, await client_for(connection) as user:
        await sign_in(admin)
        team = (await admin.get("/api/v1/console/organization")).json()["departments"][0]["id"]
        account = "NATIVE" + uuid4().hex[:10]
        created = await admin.post(
            "/api/v1/console/accounts",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "account_code": account,
                "display_name": "隔离小程序账号",
                "team_id": team,
                "roles": ["sales"],
                "temporary_password": initial_password,
            },
        )
        assert created.status_code == 201, created.text
        user.headers.pop("Origin", None)  # A native client does not send a browser origin/cookie.
        assert (
            await user.post(
                "/api/v1/auth/password/login",
                json={
                    "account_code": account,
                    "password": "Incorrect-Value-2026",
                    "role": "sales",
                },
            )
        ).status_code == 401
        assert (
            await user.post(
                "/api/v1/auth/password/login",
                json={
                    "account_code": account,
                    "password": initial_password,
                    "role": "manager",
                },
            )
        ).status_code == 401
        response = await user.post(
            "/api/v1/auth/password/login",
            json={
                "account_code": account,
                "password": initial_password,
                "role": "sales",
            },
        )
        assert response.status_code == 200, response.text
        auth = response.json()
        assert auth["auth_method"] == "password" and auth["must_change_password"]
        assert "set-cookie" not in response.headers and response.headers["cache-control"] == "no-store"
        user.headers["Authorization"] = "Bearer " + auth["access_token"]
        assert (await user.get("/api/v1/dashboard")).status_code == 403
        unchanged = await user.post(
            "/api/v1/auth/password",
            json={"old_password": initial_password, "new_password": initial_password},
        )
        assert unchanged.status_code == 422 and "不同" in unchanged.json()["detail"]
        for invalid_password in ("a" * 7, "1" * 129):
            rejected = await user.post(
                "/api/v1/auth/password",
                json={"old_password": initial_password, "new_password": invalid_password},
            )
            assert rejected.status_code == 422 and "8–128" in rejected.json()["detail"]
        changed = await user.post(
            "/api/v1/auth/password",
            json={
                "old_password": initial_password,
                "new_password": new_password,
            },
        )
        assert changed.status_code == 200, changed.text
        refreshed = await user.post("/api/v1/auth/refresh", json={"refresh_token": auth["refresh_token"]})
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["auth_method"] == "password"
        assert refreshed.json()["must_change_password"] is False
        user.headers["Authorization"] = "Bearer " + refreshed.json()["access_token"]
        assert (await user.get("/api/v1/dashboard")).status_code == 200
        assert (await user.get("/api/v1/console/organization")).status_code == 403
        assert (await user.post("/api/v1/auth/logout")).status_code == 204
        assert (await user.get("/api/v1/auth/me")).status_code == 401
        # A real password account can never fall back to the old account-only login.
        assert (await user.post("/api/v1/auth/session", json={"account_code": account})).status_code == 401
