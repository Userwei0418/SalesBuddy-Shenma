"""Company password gates, actual native/Web login and maintenance reset."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest

from tests.integration.test_company_tenants import provision_fixture
from tests.integration.test_operations_api import client_for, sign_in

pytestmark = pytest.mark.asyncio


async def save_policy(client, enabled, version, key=None):
    return await client.put("/api/v1/console/password-policy",
        json={"require_initial_change": enabled, "version_no": version},
        headers={"Idempotency-Key": key or str(uuid4())})


async def create_user(admin):
    org = (await admin.get("/api/v1/console/organization")).json()
    account = "POLICY" + uuid4().hex[:10]
    response = await admin.post("/api/v1/console/accounts", headers={"Idempotency-Key": str(uuid4())},
        json={"account_code": account, "display_name": "隔离策略测试", "team_id": org["departments"][0]["id"],
              "roles": ["sales"], "temporary_password": "Policy-Initial-2026"})
    assert response.status_code == 201, response.text
    return account, response.json()


async def native_login(client, account, password="Policy-Initial-2026"):
    response = await client.post("/api/v1/auth/password/login", json={"account_code": account, "password": password})
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
    return response.json()


async def test_gate_toggle_login_refresh_guard_and_changed_password(connection):
    async with await client_for(connection) as admin, await client_for(connection) as user:
        await sign_in(admin, "ADMIN001")
        assert (await admin.get("/api/v1/console/password-policy")).json() == {"require_initial_change": True, "version_no": 0}
        account, _ = await create_user(admin)
        auth = await native_login(user, account)
        assert auth["must_change_password"] is True
        assert (await user.get("/api/v1/dashboard")).status_code == 403
        disabled = await save_policy(admin, False, 0)
        assert disabled.status_code == 200, disabled.text
        assert (await user.get("/api/v1/dashboard")).status_code == 200
        refreshed = await user.post("/api/v1/auth/refresh", json={"refresh_token": auth["refresh_token"]})
        assert refreshed.status_code == 200 and refreshed.json()["must_change_password"] is False
        user.headers["Authorization"] = "Bearer " + refreshed.json()["access_token"]
        assert (await save_policy(admin, True, 1)).status_code == 200
        assert (await user.get("/api/v1/dashboard")).status_code == 403
        changed = await user.post("/api/v1/auth/password", json={"old_password": "Policy-Initial-2026", "new_password": "Policy-Changed-2026"})
        assert changed.status_code == 200, changed.text
        assert (await user.get("/api/v1/dashboard")).status_code == 200
        assert (await native_login(user, account, "Policy-Changed-2026"))["must_change_password"] is False


async def test_disabled_policy_create_reset_web_and_old_sessions(connection):
    async with await client_for(connection) as admin, await client_for(connection) as user:
        await sign_in(admin, "ADMIN001")
        assert (await save_policy(admin, False, 0)).status_code == 200
        account, created = await create_user(admin)
        assert created["must_change_password"] is False
        signed = await sign_in(user, account, "Policy-Initial-2026")
        assert signed.json()["must_change_password"] is False
        reset = await admin.post(f"/api/v1/console/accounts/{created['id']}/reset-password",
            headers={"Idempotency-Key": str(uuid4())},
            json={"version_no": created["version_no"], "temporary_password": "Policy-Reset-2026"})
        assert reset.status_code == 200 and reset.json()["must_change_password"] is False
        assert (await user.get("/api/v1/console/auth/me")).status_code == 401
        assert (await native_login(user, account, "Policy-Reset-2026"))["must_change_password"] is False
        assert (await user.get("/api/v1/dashboard")).status_code == 200


async def test_policy_permissions_idempotency_version_and_audit(connection):
    async with await client_for(connection) as admin, await client_for(connection) as ops, await client_for(connection) as user:
        await sign_in(admin, "ADMIN001")
        await sign_in(ops)
        assert (await ops.get("/api/v1/console/password-policy")).status_code == 200
        assert (await save_policy(ops, False, 0)).status_code == 403
        key = str(uuid4())
        first = await save_policy(admin, False, 0, key)
        repeat = await save_policy(admin, False, 0, key)
        assert first.status_code == repeat.status_code == 200 and first.json() == repeat.json()
        assert (await save_policy(admin, True, 0)).status_code == 409
        account, _ = await create_user(admin)
        await native_login(user, account)
        assert (await user.get("/api/v1/console/password-policy")).status_code == 403
        assert (await save_policy(user, True, 1)).status_code == 403
        await admin.get("/api/v1/console/password-policy")
        assert await connection.fetchval("SELECT count(*) FROM ops.audit_log WHERE action_code='account.password_policy'") == 1


async def test_selected_company_policy_does_not_change_other_company(connection):
    _, manifest, wid, _ = await provision_fixture(connection)
    async with await client_for(connection) as admin, await client_for(connection) as user:
        await sign_in(admin, "ADMIN001")
        admin.headers["X-Company-ID"] = wid
        assert (await save_policy(admin, False, 0)).status_code == 200
        assert (await admin.get("/api/v1/console/password-policy")).json()["require_initial_change"] is False
        admin.headers.pop("X-Company-ID")
        assert (await admin.get("/api/v1/console/password-policy")).json()["require_initial_change"] is True
        account = next(iter(manifest["accounts"].values()))["account"]
        assert (await native_login(user, account, "OnlyLettersInitial"))["must_change_password"] is False
        admin.headers["X-Company-ID"] = str(uuid4())
        assert (await save_policy(admin, False, 0)).status_code == 403


async def test_batch_reset_reviewed_accounts_preserves_business_and_managed_identity(connection):
    source, manifest, wid, role = await provision_fixture(connection)
    spec = importlib.util.spec_from_file_location("reset_company_passwords", Path(__file__).parents[2] / "scripts/reset_company_passwords.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    await connection.execute("RESET ROLE")
    args = dict(source_workspace="demo-sales-workspace", administrator="ADMIN001")
    workspaces = ["demo-sales-workspace", manifest["target_company"]["code"]]
    before = await connection.fetchval("SELECT count(*) FROM crm.customer")
    plan = await module.reset_companies(connection, workspaces, **args)
    with pytest.raises(ValueError, match="changed"):
        await module.reset_companies(connection, workspaces, **args, apply=True, expected="stale", password="Batch-Reset-Testing")
    result = await module.reset_companies(connection, workspaces, **args, apply=True, expected=plan["plan_sha256"], password="Batch-Reset-Testing")
    assert result["applied"] and result["total_accounts"] == plan["total_accounts"]
    assert await connection.fetchval("SELECT count(*) FROM crm.customer") == before
    assert await connection.fetchval("SELECT count(*) FROM platform.password_credential p JOIN platform.user_ref u ON u.id=p.user_ref_id WHERE u.attributes->>'platform_managed'='true'") == 0
    assert await connection.fetchval("SELECT count(*) FROM ops.audit_log WHERE action_code='account.password_batch'") == 2
    await connection.execute(f'SET LOCAL ROLE "{role}"')
    async with await client_for(connection) as user:
        account = next(iter(manifest["accounts"].values()))["account"]
        assert (await native_login(user, account, "Batch-Reset-Testing"))["must_change_password"] is False
        assert (await user.get("/api/v1/dashboard")).status_code == 200
