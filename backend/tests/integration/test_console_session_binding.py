"""Console cookie ordering and logout against real HTTP, PostgreSQL and RLS.

The application and repositories are not mocked. A delayed response is represented
by applying its actual Set-Cookie headers after a later login response, exactly as
a browser cookie jar does. Every test runs in the shared rollback-only DB fixture.
"""

from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie

import jwt
import pytest

from sales_backend.auth.tokens import TokenService
from sales_backend.db import set_request_context
from sales_backend.main import app
from sales_backend.repositories.identity import IdentityRepository
from tests.integration.test_operations_api import client_for, sign_in

pytestmark = pytest.mark.asyncio

AUTH_PATH = "/api/v1/console/auth"
REFRESH_COOKIE = "sales_console_refresh"
BINDING_COOKIE = "sales_console_session"


def response_cookies(response):
    result = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        result.load(header)
    return result


def cookie_values(response):
    return {name: value.value for name, value in response_cookies(response).items()}


def cookie_header(values):
    return {"Cookie": "; ".join(f"{name}={value}" for name, value in values.items())}


def session_identity(response):
    return TokenService(app.state.settings).decode_access_token(response.json()["access_token"])


async def session_state(connection, login_response):
    actor, session_id = session_identity(login_response)
    await set_request_context(connection, actor)
    row = await connection.fetchrow(
        """SELECT status,refresh_token_hash,expires_at,updated_at,revoked_at
           FROM platform.auth_session WHERE id=$1::uuid""",
        session_id,
    )
    assert row is not None
    return dict(row)


def expired_access(response, *, signing_secret=None):
    settings = app.state.settings
    claims = jwt.decode(
        response.json()["access_token"],
        settings.access_token_secret,
        algorithms=["HS256"],
        audience=settings.access_token_audience,
        issuer=settings.access_token_issuer,
    )
    now = datetime.now(UTC)
    claims.update(iat=now - timedelta(minutes=3), nbf=now - timedelta(minutes=3), exp=now - timedelta(minutes=1))
    return jwt.encode(claims, signing_secret or settings.access_token_secret, algorithm="HS256")


async def test_console_login_sets_binding_once_and_normal_refresh_rotates_only_refresh(connection):
    async with await client_for(connection) as client:
        login = await sign_in(client)
        cookies = response_cookies(login)
        assert set(cookies) == {REFRESH_COOKIE, BINDING_COOKIE}
        for cookie in cookies.values():
            assert cookie["httponly"] and cookie["secure"]
            assert cookie["samesite"].lower() == "strict"
            assert cookie["path"] == AUTH_PATH
        assert "refresh_token" not in login.json()
        assert login.headers["cache-control"] == "no-store"
        binding_before = client.cookies.get(BINDING_COOKIE)
        before = await session_state(connection, login)

        # A reload can bootstrap using the cookies without a still-live bearer.
        client.headers.pop("Authorization")
        refreshed = await client.post(AUTH_PATH + "/refresh")
        assert refreshed.status_code == 200, refreshed.text
        assert set(response_cookies(refreshed)) == {REFRESH_COOKIE}
        binding_unchanged = client.cookies.get(BINDING_COOKIE) == binding_before
        assert binding_unchanged
        rotated = client.cookies.get(REFRESH_COOKIE) != cookies[REFRESH_COOKIE].value
        assert rotated
        assert "refresh_token" not in refreshed.json()
        assert refreshed.json()["actor"] == login.json()["actor"]
        assert session_identity(refreshed) == session_identity(login)
        after = await session_state(connection, login)
        database_rotated = after["refresh_token_hash"] != before["refresh_token_hash"]
        assert database_rotated
        assert after["status"] == "active"

        # The old single-use refresh credential remains unusable.
        old_refresh = await client.post(AUTH_PATH + "/refresh", headers=cookie_header(cookie_values(login)))
        assert old_refresh.status_code == 401
        assert "set-cookie" not in old_refresh.headers


@pytest.mark.parametrize("other_account", ["ADMIN001", "OPS001"], ids=["different-account", "same-account-new-session"])
async def test_valid_refresh_cannot_rotate_with_another_session_binding(connection, other_account):
    # Two independent browsers have live sessions, so a 401 cannot be explained
    # by login's separate revocation of the previous browser session.
    async with await client_for(connection) as first, await client_for(connection) as second:
        first_login = await sign_in(first, "OPS001")
        second_login = await sign_in(second, other_account)
        assert session_identity(first_login)[1] != session_identity(second_login)[1]
        first_cookies = cookie_values(first_login)
        second_cookies = cookie_values(second_login)
        before = await session_state(connection, first_login)
        assert before["status"] == "active"
        assert (await session_state(connection, second_login))["status"] == "active"

        mismatched = await first.post(AUTH_PATH + "/refresh", headers=cookie_header({
            REFRESH_COOKIE: first_cookies[REFRESH_COOKIE],
            BINDING_COOKIE: second_cookies[BINDING_COOKIE],
        }))
        assert mismatched.status_code == 401
        assert "set-cookie" not in mismatched.headers
        after = await session_state(connection, first_login)
        unchanged = before == after
        assert unchanged, "A rejected binding must roll back all token-rotation state"

        recovered = await first.post(AUTH_PATH + "/refresh", headers=cookie_header(first_cookies))
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["actor"]["user_id"] == first_login.json()["actor"]["user_id"]
        other = await second.post(AUTH_PATH + "/refresh", headers=cookie_header(second_cookies))
        assert other.status_code == 200, other.text


@pytest.mark.parametrize("account", ["ADMIN001", "OPS001"], ids=["different-account", "same-account-new-session"])
async def test_late_old_refresh_cookie_cannot_replace_new_login_identity(connection, account):
    async with await client_for(connection) as browser:
        old_login = await sign_in(browser, "OPS001")
        old_cookie = cookie_values(old_login)
        # Complete request A on the server but hold its response outside the jar
        # used for the next login, modelling a delayed network delivery.
        late_response = await browser.post(AUTH_PATH + "/refresh")
        assert late_response.status_code == 200
        next_login = await browser.post(
            AUTH_PATH + "/login",
            json={"account_code": account, "password": "Isolated-Testing-2026"},
            headers=cookie_header(old_cookie),
        )
        assert next_login.status_code == 200, next_login.text
        new_cookie = cookie_values(next_login)
        browser.headers["Authorization"] = "Bearer " + next_login.json()["access_token"]
        assert session_identity(old_login)[1] != session_identity(next_login)[1]
        assert (await session_state(connection, old_login))["status"] == "revoked"
        assert (await session_state(connection, next_login))["status"] == "active"

        # HttpOnly does not prevent the browser accepting an older Set-Cookie.
        browser.cookies.extract_cookies(late_response)
        current_binding_is_new = browser.cookies.get(BINDING_COOKIE) == new_cookie[BINDING_COOKIE]
        assert current_binding_is_new
        assert (await browser.post(AUTH_PATH + "/refresh")).status_code == 401
        current = await browser.get(AUTH_PATH + "/me")
        assert current.status_code == 200
        assert current.json()["actor"]["user_id"] == next_login.json()["actor"]["user_id"]
        recovered = await browser.post(AUTH_PATH + "/refresh", headers=cookie_header(new_cookie))
        assert recovered.status_code == 200, recovered.text
        assert session_identity(recovered)[1] == session_identity(next_login)[1]


@pytest.mark.parametrize("binding_kind", ["missing", "malformed", "access-token"])
async def test_refresh_requires_valid_purpose_bound_cookie_without_consuming_refresh(connection, binding_kind):
    async with await client_for(connection) as client:
        login = await sign_in(client)
        good = cookie_values(login)
        invalid = {REFRESH_COOKIE: good[REFRESH_COOKIE]}
        if binding_kind == "malformed":
            invalid[BINDING_COOKIE] = "invalid.session.binding"
        elif binding_kind == "access-token":
            invalid[BINDING_COOKIE] = login.json()["access_token"]
        before = await session_state(connection, login)
        rejected = await client.post(AUTH_PATH + "/refresh", headers=cookie_header(invalid))
        assert rejected.status_code == 401
        assert "set-cookie" not in rejected.headers
        after = await session_state(connection, login)
        unchanged = before == after
        assert unchanged
        assert (await client.post(AUTH_PATH + "/refresh", headers=cookie_header(good))).status_code == 200


async def test_session_binding_is_not_an_api_access_token(connection):
    async with await client_for(connection) as client:
        login = await sign_in(client)
        denied = await client.get(AUTH_PATH + "/me", headers={
            "Authorization": "Bearer " + cookie_values(login)[BINDING_COOKIE],
        })
        assert denied.status_code == 401
        assert (await session_state(connection, login))["status"] == "active"


async def test_rejected_new_login_preserves_previous_browser_session(connection):
    async with await client_for(connection) as client:
        login = await sign_in(client)
        cookies = cookie_values(login)
        rejected = await client.post(AUTH_PATH + "/login", json={
            "account_code": "ADMIN001", "password": "Deliberately-Incorrect-2026",
        })
        assert rejected.status_code == 401
        assert "set-cookie" not in rejected.headers
        assert (await session_state(connection, login))["status"] == "active"
        assert (await client.post(AUTH_PATH + "/refresh", headers=cookie_header(cookies))).status_code == 200


async def test_failed_new_login_rolls_back_new_session_and_previous_revocation(connection, monkeypatch):
    async with await client_for(connection) as client:
        login = await sign_in(client)
        actor, _ = session_identity(login)
        await set_request_context(connection, actor)
        count_before = await connection.fetchval("SELECT count(*) FROM platform.auth_session")
        original = IdentityRepository.revoke_identified_session

        async def revoke_then_fail(self, conn, reference):
            await original(self, conn, reference)
            raise RuntimeError("injected failure after previous session revocation")

        with monkeypatch.context() as patch:
            patch.setattr(IdentityRepository, "revoke_identified_session", revoke_then_fail)
            with pytest.raises(RuntimeError, match="injected failure after previous session revocation"):
                await client.post(AUTH_PATH + "/login", json={
                    "account_code": "OPS001", "password": "Isolated-Testing-2026",
                })
        assert (await session_state(connection, login))["status"] == "active"
        assert await connection.fetchval("SELECT count(*) FROM platform.auth_session") == count_before
        assert (await client.post(AUTH_PATH + "/refresh")).status_code == 200


@pytest.mark.parametrize("proof", ["binding-with-expired-access", "binding-only", "legacy-expired-access"])
async def test_logout_revokes_session_without_unexpired_access_and_clears_cookies(connection, proof):
    async with await client_for(connection) as client:
        login = await sign_in(client)
        cookies = cookie_values(login)
        actor, _ = session_identity(login)
        token = expired_access(login)
        assert (await client.get(AUTH_PATH + "/me", headers={"Authorization": "Bearer " + token})).status_code == 401
        logout_cookies = dict(cookies)
        client.headers.pop("Authorization")
        headers = {}
        if proof != "binding-only":
            headers["Authorization"] = "Bearer " + token
        if proof == "legacy-expired-access":
            logout_cookies.pop(BINDING_COOKIE)
        headers.update(cookie_header(logout_cookies))
        logout = await client.post(AUTH_PATH + "/logout", headers=headers)
        assert logout.status_code == 200, logout.text
        assert logout.json() == {"logged_out": True}
        cleared = response_cookies(logout)
        assert set(cleared) == {REFRESH_COOKIE, BINDING_COOKIE}
        assert all(cookie["max-age"] == "0" and cookie["path"] == AUTH_PATH for cookie in cleared.values())
        assert client.cookies.get(REFRESH_COOKIE) is None
        assert client.cookies.get(BINDING_COOKIE) is None
        assert (await session_state(connection, login))["status"] == "revoked"
        assert (await client.post(AUTH_PATH + "/refresh", headers=cookie_header(cookies))).status_code == 401
        assert (await client.get(AUTH_PATH + "/me", headers={
            "Authorization": "Bearer " + login.json()["access_token"],
        })).status_code == 401
        await set_request_context(connection, actor)
        assert await connection.fetchval(
            """SELECT count(*) FROM ops.audit_log WHERE object_label='POST /api/v1/console/auth/logout'
               AND actor_user_ref_id=$1::uuid AND result_code='success'""",
            actor.user_id,
        ) == 1


async def test_logout_with_forged_proofs_clears_browser_but_cannot_revoke_a_session(connection):
    async with await client_for(connection) as client:
        login = await sign_in(client)
        saved = cookie_values(login)
        forged = expired_access(login, signing_secret="deliberately-wrong-isolated-test-signing-secret")
        result = await client.post(AUTH_PATH + "/logout", headers={
            "Authorization": "Bearer " + forged,
            **cookie_header({BINDING_COOKIE: "forged.session.binding"}),
        })
        assert result.status_code == 200
        assert set(response_cookies(result)) == {REFRESH_COOKIE, BINDING_COOKIE}
        assert (await session_state(connection, login))["status"] == "active"
        assert (await client.post(AUTH_PATH + "/refresh", headers=cookie_header(saved))).status_code == 200


@pytest.mark.parametrize("refresh_state", ["stale", "missing", "expired"])
async def test_signed_binding_logout_remains_audited_without_a_live_refresh(connection, refresh_state):
    async with await client_for(connection) as client:
        login = await sign_in(client)
        actor, session_id = session_identity(login)
        cookies = cookie_values(login)
        if refresh_state == "stale":
            assert (await client.post(AUTH_PATH + "/refresh")).status_code == 200
        elif refresh_state == "missing":
            cookies.pop(REFRESH_COOKIE)
        else:
            await set_request_context(connection, actor)
            await connection.execute(
                """UPDATE platform.auth_session SET created_at=clock_timestamp()-interval '2 days',
                   expires_at=clock_timestamp()-interval '1 minute' WHERE id=$1::uuid""",
                session_id,
            )
        before = await session_state(connection, login)
        client.headers.pop("Authorization")
        result = await client.post(AUTH_PATH + "/logout", headers=cookie_header(cookies))
        assert result.status_code == 200, result.text
        assert set(response_cookies(result)) == {REFRESH_COOKIE, BINDING_COOKIE}
        after = await session_state(connection, login)
        assert after["status"] == "revoked"
        assert after["expires_at"] == before["expires_at"]
        no_rotation = after["refresh_token_hash"] == before["refresh_token_hash"]
        assert no_rotation, "Signed session revocation must not rotate an unrelated refresh credential"
        audit = await connection.fetchrow(
            """SELECT actor_user_ref_id::text,actor_role_code,result_code FROM ops.audit_log
               WHERE object_label='POST /api/v1/console/auth/logout'""",
        )
        assert audit is not None
        assert dict(audit) == {
            "actor_user_ref_id": actor.user_id, "actor_role_code": actor.role.value, "result_code": "success",
        }


async def test_signed_binding_logout_does_not_shorten_expiry_below_database_creation_time(connection):
    async with await client_for(connection) as client:
        login = await sign_in(client)
        actor, session_id = session_identity(login)
        await set_request_context(connection, actor)
        await connection.execute(
            """UPDATE platform.auth_session SET created_at=clock_timestamp()+interval '5 minutes',
               expires_at=clock_timestamp()+interval '3 days' WHERE id=$1::uuid""",
            session_id,
        )
        before = await session_state(connection, login)
        assert await connection.fetchval(
            "SELECT created_at>clock_timestamp() FROM platform.auth_session WHERE id=$1::uuid", session_id,
        )
        result = await client.post(AUTH_PATH + "/logout")
        assert result.status_code == 200, result.text
        after = await session_state(connection, login)
        assert after["status"] == "revoked"
        assert after["expires_at"] == before["expires_at"]


async def test_legacy_refresh_only_logout_handles_valid_future_creation_time_and_revokes(connection):
    async with await client_for(connection) as client:
        login = await sign_in(client)
        actor, session_id = session_identity(login)
        await set_request_context(connection, actor)
        await connection.execute(
            """UPDATE platform.auth_session SET created_at=clock_timestamp()+interval '5 minutes',
               expires_at=clock_timestamp()+interval '3 days' WHERE id=$1::uuid""",
            session_id,
        )
        client.headers.pop("Authorization")
        result = await client.post(AUTH_PATH + "/logout", headers=cookie_header({
            REFRESH_COOKIE: cookie_values(login)[REFRESH_COOKIE],
        }))
        assert result.status_code == 200, result.text
        assert set(response_cookies(result)) == {REFRESH_COOKIE, BINDING_COOKIE}
        assert (await session_state(connection, login))["status"] == "revoked"
        assert await connection.fetchval(
            """SELECT count(*) FROM ops.audit_log WHERE object_label='POST /api/v1/console/auth/logout'
               AND actor_user_ref_id=$1::uuid AND actor_role_code=$2 AND result_code='success'""",
            actor.user_id, actor.role.value,
        ) == 1


async def test_disabled_account_can_revoke_its_signed_session_and_keeps_audit_actor(connection):
    async with await client_for(connection) as client:
        login = await sign_in(client)
        actor, _ = session_identity(login)
        await set_request_context(connection, actor)
        await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", actor.user_id)
        client.headers.pop("Authorization")
        result = await client.post(AUTH_PATH + "/logout", headers=cookie_header({
            BINDING_COOKIE: cookie_values(login)[BINDING_COOKIE],
        }))
        assert result.status_code == 200, result.text
        assert (await session_state(connection, login))["status"] == "revoked"
        # Read the receipt as another still-active administrator, not the disabled actor.
        await sign_in(client, "ADMIN001")
        audit = await connection.fetchrow(
            """SELECT actor_user_ref_id::text,actor_role_code,result_code FROM ops.audit_log
               WHERE object_label='POST /api/v1/console/auth/logout'""",
        )
        assert audit is not None
        assert dict(audit) == {
            "actor_user_ref_id": actor.user_id, "actor_role_code": actor.role.value, "result_code": "success",
        }


async def test_cross_origin_logout_cannot_revoke_or_clear_console_session(connection):
    async with await client_for(connection) as client:
        login = await sign_in(client)
        rejected = await client.post(AUTH_PATH + "/logout", headers={"Origin": "https://elsewhere.invalid"})
        assert rejected.status_code == 403
        assert "set-cookie" not in rejected.headers
        assert (await session_state(connection, login))["status"] == "active"
