"""Fixed-scope role saves, real console login and inherited data boundaries."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.contracts.authorization import AccountAuthorizationSave
from sales_backend.db import set_request_context
from sales_backend.domain.authorization import ObjectScope
from sales_backend.repositories.authorization import AuthorizationRepository
from tests.integration.test_authorization_store import resolve
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_sales_opportunity_permissions import PASSWORD, account_fixture

pytestmark = pytest.mark.asyncio
repo = AuthorizationRepository()
BASE = "/api/v1/console/permissions"


def role_body(**extra):
    return dict(name="固定范围测试", status="active", reason="明确确认功能范围", permissions=[
        dict(permission="access.console", scope="inherit"),
        dict(permission="visit.upload"),
        dict(permission="customer.read", scope="inherit"),
    ], **extra)


def assignment(role, *, scope="self", teams=None, version=0, overrides=None, expiry=None):
    return AccountAuthorizationSave(version_no=version, reason="独立测试绑定", roles=[
        dict(role_id=role["id"], scope=scope, team_ids=teams or [], expires_at=expiry),
    ], overrides=overrides or []).model_dump(mode="json")


async def test_sql_role_normalization_and_default_self_binding_allow_real_console_login(connection):
    user, _, admin = await account_fixture(connection, ["fde"])
    role = await repo.save_role(connection, None, role_body())  # Raw SQL path, no Python normalizer.
    assert [p["scope"] for p in role["permissions"]] == ["workspace", "self", "inherit"]
    await repo.save_account(connection, user["id"], assignment(role))
    async with await client_for(connection) as client:
        await sign_in(client, user["account_code"], PASSWORD)
        mine = await client.get(BASE + "/me")
        assert mine.status_code == 200, mine.text
        assert mine.json()["permissions"]["access.console"]
        assert not mine.json()["permissions"]["authorization.roles_manage"]
        # Entry access is not business-data authorization or role-management power.
        assert (await client.get(BASE + "/roles")).status_code == 403
    await set_request_context(connection, admin)
    audit = next(row for row in await repo.audit(connection) if row["subject_id"] == role["id"])
    assert audit["after_snapshot"]["permissions"] == role["permissions"]


@pytest.mark.parametrize("scope", ["self", "teams", "workspace"])
async def test_business_inheritance_and_self_only_function_keep_their_distinct_ranges(connection, scope):
    user, teams, admin = await account_fixture(connection, ["fde"])
    role = await repo.save_role(connection, None, role_body())
    await repo.save_account(connection, user["id"], assignment(
        role, scope=scope, teams=[teams["fde"]] if scope == "teams" else [],
        overrides=[dict(permission="opportunity.create", effect="deny")],
    ))
    await resolve(connection, admin, user, "fde")
    snapshot = await repo.snapshot(connection)
    grants = {g["permission_code"]: g for g in snapshot["grants"] if g["source_id"] == role["id"]}
    assert grants["access.console"]["scope_code"] == "workspace"
    assert grants["visit.upload"]["scope_code"] == "self"
    assert grants["visit.upload"]["team_ids"] == []
    assert grants["customer.read"]["scope_code"] == scope
    assert grants["customer.read"]["team_ids"] == ([teams["fde"]] if scope == "teams" else [])
    auth = await repo.effective(connection)
    assert not auth.allows("opportunity.create")
    assert not auth.allows("customer.read", ObjectScope(str(uuid4()), user["id"], teams["fde"]))
    other = ObjectScope(admin.workspace_id, str(uuid4()), str(uuid4()))
    assert auth.allows("customer.read", other) == (scope == "workspace")
    assert not auth.allows("visit.upload", other)


@pytest.mark.parametrize("permission,scope", [("access.console", "self"), ("visit.upload", "workspace")])
async def test_illegal_explicit_scope_rejected_by_http_and_sql_without_audit_or_role_mutation(connection, permission, scope):
    _, _, admin = await account_fixture(connection, ["fde"])
    body = {**role_body(), "permissions": [dict(permission=permission, scope=scope)]}
    before = (await repo.roles(connection), await repo.audit(connection))
    with pytest.raises(asyncpg.InvalidParameterValueError):
        async with connection.transaction():
            await repo.save_role(connection, None, body)
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        result = await client.post(BASE + "/roles", json=body)
        assert result.status_code == 422, result.text
    await set_request_context(connection, admin)
    assert (await repo.roles(connection), await repo.audit(connection)) == before


async def test_legacy_inheritance_stays_inactive_until_explicit_resave_preserving_appointments(connection):
    user, _, admin = await account_fixture(connection, ["fde"])
    role = await repo.save_role(connection, None, role_body())
    await repo.save_account(connection, user["id"], assignment(role))
    # Reconstruct an old persisted role, without granting a production bypass.
    runtime = await connection.fetchval("SELECT current_user")
    await connection.execute("RESET ROLE")
    try:
        await connection.execute("UPDATE config.permission_role_grant SET scope_code='inherit' "
                                 "WHERE role_id=$1::uuid AND permission_code='access.console'", role["id"])
    finally:
        await connection.execute(f'SET LOCAL ROLE "{runtime}"')
    appointments = await connection.fetch("SELECT * FROM platform.role_binding WHERE user_ref_id=$1::uuid", user["id"])
    await resolve(connection, admin, user, "fde")
    assert not (await repo.effective(connection)).allows("access.console")
    await set_request_context(connection, admin)
    prior = next(r for r in await repo.roles(connection) if r["id"] == role["id"])
    assert next(g for g in prior["permissions"] if g["permission"] == "access.console")["scope"] == "inherit"
    updated = await repo.save_role(connection, role["id"], role_body(version_no=1))
    assert updated["version_no"] == 2
    assert len(await repo.roles(connection)) == 8  # Seven built-in roles plus this role.
    audit = next(row for row in await repo.audit(connection) if row["subject_id"] == role["id"])
    assert next(g for g in audit["before_snapshot"]["permissions"] if g["permission_code"] == "access.console")["scope_code"] == "inherit"
    assert audit["after_snapshot"]["permissions"][0]["scope"] == "workspace"
    assert (await repo.account(connection, user["id"]))["roles"][0]["scope"] == "self"
    assert await connection.fetch("SELECT * FROM platform.role_binding WHERE user_ref_id=$1::uuid", user["id"]) == appointments
    await resolve(connection, admin, user, "fde")
    assert (await repo.effective(connection)).allows("access.console")


@pytest.mark.parametrize("restriction", ["deny", "role_inactive", "role_expired", "assignment_expired"])
async def test_fixed_scope_does_not_bypass_denial_status_or_expiry(connection, restriction):
    user, _, admin = await account_fixture(connection, ["fde"])
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    body = role_body()
    if restriction == "role_expired":
        body["expires_at"] = expired
    role = await repo.save_role(connection, None, body)
    await repo.save_account(connection, user["id"], assignment(role,
        expiry=expired if restriction == "assignment_expired" else None,
        overrides=[dict(permission="access.console", effect="deny")] if restriction == "deny" else [],
    ))
    if restriction == "role_inactive":
        await repo.save_role(connection, role["id"], {**body, "status": "inactive", "version_no": 1})
    await resolve(connection, admin, user, "fde")
    assert not (await repo.effective(connection)).allows("access.console")


async def test_http_default_role_save_and_self_assignment_are_canonical_end_to_end(connection):
    user, _, _ = await account_fixture(connection, ["fde"])
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        response = await client.post(BASE + "/roles", json=dict(
            name="仅开放后台入口", reason="确认唯一范围", permissions=[dict(permission="access.console")],
        ))
        assert response.status_code == 201, response.text
        role = response.json()
        assert role["permissions"] == [dict(permission="access.console", scope="workspace", team_ids=[])]
        response = await client.put(BASE + "/accounts/" + user["id"], json=assignment(role))
        assert response.status_code == 200, response.text
        await sign_in(client, user["account_code"], PASSWORD)
        mine = await client.get(BASE + "/me")
        assert mine.status_code == 200 and mine.json()["permissions"]["access.console"]
        assert not mine.json()["permissions"]["opportunity.create"]


async def test_normalized_role_resave_still_invalidates_delegated_template_approval(connection):
    from tests.integration.test_authorization_delegation import body, fixture

    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    editor, target, _, admin, policy = await fixture(connection, expiry=expiry)
    await repo.save_delegation(connection, editor["id"], {
        **policy, "version_no": 1,
        "permissions": [*policy["permissions"], dict(permission="access.console", scope="workspace")],
    })
    payload = {**role_body(), "permissions": [
        dict(permission="access.console", scope="inherit"), dict(permission="opportunity.create", scope="inherit"),
    ]}
    role = await repo.save_role(connection, None, payload)
    assigned = dict(role_id=role["id"], scope="self", expires_at=expiry)
    await resolve(connection, admin, editor, "fde")
    await repo.save_account(connection, target["id"], body(roles=[assigned]))
    await resolve(connection, admin, target, "fde")
    auth = await repo.effective(connection)
    assert auth.allows("access.console") and auth.allows("opportunity.create")
    await set_request_context(connection, admin)
    updated = await repo.save_role(connection, role["id"], {**payload, "version_no": 1})
    assert updated["version_no"] == 2 and updated["permissions"][0]["scope"] == "workspace"
    await resolve(connection, admin, target, "fde")
    auth = await repo.effective(connection)
    assert not auth.allows("access.console") and not auth.allows("opportunity.create")
    await resolve(connection, admin, editor, "fde")
    await repo.save_account(connection, target["id"], body(version=1, roles=[assigned]))
    await resolve(connection, admin, target, "fde")
    assert (await repo.effective(connection)).allows("access.console")
