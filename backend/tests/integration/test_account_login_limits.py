"""Real HTTP/SQL account recovery; disposable runtime has no throttle-table access."""

import hashlib
import os
import re
from uuid import uuid4

import asyncpg
import pytest

from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


def throttle_key(kind, value):
    text = f"account:demo-sales-workspace:{value}" if kind == "account" else f"ip:{value}"
    return hashlib.sha256(text.encode()).hexdigest()


async def attempts(connection, key, limit, count):
    for _ in range(count):
        await connection.fetchval("SELECT security.login_attempt($1,$2)", key, limit)


async def organization(client):
    response = await client.get("/api/v1/console/organization")
    assert response.status_code == 200, response.text
    return {a["account_code"]: a for a in response.json()["accounts"]}


async def test_account_unlock_real_login_idempotency_version_and_business_audit(connection):
    async with await client_for(connection) as admin, await client_for(connection) as user:
        await sign_in(admin, "ADMIN001")
        for _ in range(5):
            response = await user.post(
                "/api/v1/auth/password/login", json={"account_code": "OPS001", "password": "Wrong-Isolated-Value"}
            )
            assert response.status_code == 401
        response = await user.post(
            "/api/v1/auth/password/login", json={"account_code": "OPS001", "password": "Isolated-Testing-2026"}
        )
        assert response.status_code == 429 and "该账号" in response.json()["detail"]
        assert 0 < int(response.headers["Retry-After"]) <= 900
        target = (await organization(admin))["OPS001"]
        assert target["login_locked"] and target["login_attempts"] == 6 and target["login_retry_at"]
        path = f"/api/v1/console/accounts/{target['id']}/unlock-login"
        body = {"version_no": target["version_no"], "reason": "隔离验收：核对本人申请"}
        key = str(uuid4())
        first = await admin.post(path, json=body, headers={"Idempotency-Key": key})
        assert first.status_code == 200, first.text
        assert first.json() == {
            "id": target["id"],
            "version_no": target["version_no"] + 1,
            "login_locked": False,
            "login_retry_at": None,
            "login_attempts": 0,
        }
        replay = await admin.post(path, json=body, headers={"Idempotency-Key": key})
        assert replay.json() == first.json()
        stale = await admin.post(path, json=body, headers={"Idempotency-Key": str(uuid4())})
        assert stale.status_code == 409
        assert (await organization(admin))["OPS001"]["login_locked"] is False
        rows = await connection.fetch(
            """SELECT before_snapshot,after_snapshot FROM ops.audit_log
            WHERE action_code='account.login_unlock' AND object_id=$1::uuid""",
            target["id"],
        )
        assert len(rows) == 1 and rows[0]["before_snapshot"]["login_locked"]
        assert rows[0]["after_snapshot"]["reason"] == body["reason"]
        assert "key_hash" not in str(rows)
        activities = await admin.get("/api/v1/console/activities", params={"category": "account", "q": body["reason"]})
        assert activities.status_code == 200, activities.text
        assert any(r["action_label"] == "解除账号登录限制" for r in activities.json()["items"])
        await sign_in(user, "OPS001")


async def test_unlock_does_not_clear_network_or_another_account(connection):
    async with await client_for(connection) as admin, await client_for(connection) as user:
        await sign_in(admin, "ADMIN001")
        target = (await organization(admin))["OPS001"]
        await attempts(connection, throttle_key("account", "OPS001"), 5, 6)
        await attempts(connection, throttle_key("account", "XS002"), 5, 6)
        await attempts(connection, throttle_key("ip", "127.0.0.1"), 50, 50)
        response = await admin.post(
            f"/api/v1/console/accounts/{target['id']}/unlock-login",
            json={"version_no": target["version_no"], "reason": "隔离验收仅解除账号"},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 200 and not response.json()["login_locked"]
        listing = await organization(admin)
        assert listing["XS002"]["login_locked"] and not listing["OPS001"]["login_locked"]
        response = await user.post(
            "/api/v1/console/auth/login", json={"account_code": "OPS001", "password": "Isolated-Testing-2026"}
        )
        assert response.status_code == 429 and "当前网络" in response.json()["detail"]
        assert "该账号" not in response.json()["detail"] and int(response.headers["Retry-After"]) > 0


@pytest.mark.parametrize("reset_password", ["Isolated-Reset-2026", "LettersOnlyResetPassword", "12345678"])
async def test_password_reset_unlocks_only_target_and_still_revokes_sessions(connection, reset_password):
    async with await client_for(connection) as admin, await client_for(connection) as user:
        await sign_in(admin, "ADMIN001")
        await sign_in(user, "OPS001")
        target = (await organization(admin))["OPS001"]
        await attempts(connection, throttle_key("account", "OPS001"), 5, 6)
        await attempts(connection, throttle_key("account", "XS002"), 5, 6)
        response = await admin.post(
            f"/api/v1/console/accounts/{target['id']}/reset-password",
            json={"version_no": target["version_no"], "temporary_password": reset_password},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 200, response.text
        assert response.json()["must_change_password"] is True
        assert response.json()["login_locked"] is False and response.json()["login_attempts"] == 0
        assert (await user.get("/api/v1/console/auth/me")).status_code == 401
        assert (await organization(admin))["XS002"]["login_locked"]
        renewed = await sign_in(user, "OPS001", reset_password)
        assert renewed.json()["must_change_password"] is True


async def test_unlock_roles_null_reason_missing_target_and_private_table_boundary(connection):
    async with await client_for(connection) as ops:
        await sign_in(ops)
        accounts = await organization(ops)
        for code in ("OPS001", "ADMIN001"):
            target = accounts[code]
            response = await ops.post(
                f"/api/v1/console/accounts/{target['id']}/unlock-login",
                json={"version_no": target["version_no"], "reason": "不应允许"},
            )
            assert response.status_code == 403
        target = accounts["XS001"]
        response = await ops.post(
            f"/api/v1/console/accounts/{target['id']}/unlock-login",
            json={"version_no": target["version_no"], "reason": "   "},
        )
        assert response.status_code == 422
        response = await ops.post(
            f"/api/v1/console/accounts/{uuid4()}/unlock-login", json={"version_no": 1, "reason": "目标不存在"}
        )
        assert response.status_code == 404
        await actor(connection, "XS001")
        for query, args in [
            ("SELECT security.unlock_account_login($1::uuid,'拒绝越权')", [target["id"]]),
            ("SELECT * FROM security.account_login_status($1::uuid)", [target["id"]]),
            ("DELETE FROM security.login_throttle WHERE false", []),
            ("SELECT * FROM security.login_throttle", []),
        ]:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with connection.transaction():
                    await connection.execute(query, *args)


async def test_retry_window_is_actual_remaining_and_expired_state_is_unlocked(connection):
    # Adjust only unique technical keys, only inside the disposable runner DB.
    if not (await connection.fetchval("SELECT current_database()")).startswith("salegent_verify_integration_"):
        pytest.skip("Clock-fixture mutation is limited to the disposable integration database")
    runtime = os.environ["SALES_TEST_ROLE"]
    assert re.fullmatch(r"salegent_verify_role_[a-f0-9]+", runtime)
    keys = [hashlib.sha256(uuid4().bytes).hexdigest() for _ in range(2)]
    await connection.execute("RESET ROLE")
    try:
        for key, minutes in zip(keys, (5, 16), strict=True):
            await connection.execute(
                """INSERT INTO security.login_throttle(key_hash,attempts,window_start)
                VALUES($1,6,clock_timestamp()-make_interval(mins=>$2))""",
                key,
                minutes,
            )
    finally:
        await connection.execute(f'SET LOCAL ROLE "{runtime}"')
    active, expired = [
        dict(await connection.fetchrow("SELECT * FROM security.login_limit_status($1,5)", k)) for k in keys
    ]
    assert active["login_locked"] and 598 <= active["retry_after_seconds"] <= 600
    assert expired == {"login_locked": False, "login_retry_at": None, "login_attempts": 0, "retry_after_seconds": 0}
    for key, limit in [(None, 5), (keys[0], None), (keys[0], 500), ("ip:any", 5)]:
        with pytest.raises(asyncpg.InvalidParameterValueError):
            async with connection.transaction():
                await connection.fetch("SELECT * FROM security.login_limit_status($1,$2)", key, limit)
