"""Self appointment changes must keep live administrator authority throughout."""

from uuid import uuid4

import asyncpg
import pytest

from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.services.operations_accounts import OperationsAccountService
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_team_directory import add_team

pytestmark = pytest.mark.asyncio


async def company_admin(connection):
    # Table-owner/superuser execution skips the guards that exposed this defect.
    assert not await connection.fetchval(
        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
    )
    original_admin = await actor(connection, "ADMIN001")
    service = OperationsAccountService()
    code = "SELF" + uuid4().hex[:12]
    created = await service.create(connection, original_admin, dict(
        account_code=code, display_name="隔离自任职管理员", roles=["administrator", "operations"],
        company_roles=["administrator", "operations"], team_id=None, memberships=[],
    ), "Isolated-Testing-2026")
    await add_team(connection, "隔离自任职第二部门")
    self_actor = await actor(connection, code)
    organization = await OperationsAccountRepository().organization(connection)
    teams = [t["id"] for t in organization["departments"] if t["status"] == "active"]
    assert len(teams) >= 2
    return original_admin, self_actor, created["id"], teams


def update_body(version=1, *, team=None, role="sales", company_roles=None, status="active"):
    company_roles = ["administrator", "operations"] if company_roles is None else company_roles
    memberships = [{"team_id": team, "roles": [role]}] if team else []
    return dict(
        version_no=version, display_name="隔离自任职管理员", status=status,
        company_roles=company_roles, memberships=memberships, team_id=team,
        roles=company_roles + ([role] if team else []),
    )


async def live_bindings(connection, uid):
    return [dict(row) for row in await connection.fetch(
        """SELECT id::text,role_code,team_id::text,data_scope_code,valid_from,valid_to
        FROM platform.role_binding WHERE user_ref_id=$1::uuid
          AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to
        ORDER BY role_code,team_id NULLS FIRST,id""", uid,
    )]


async def test_company_admin_can_change_own_appointments_without_losing_authority(connection):
    _, admin, uid, teams = await company_admin(connection)
    before = await live_bindings(connection, uid)
    service = OperationsAccountService()
    repository = OperationsAccountRepository()
    for version, team, role in [(1, teams[0], "sales"), (2, teams[1], "fde"), (3, None, "sales")]:
        result = await service.update(connection, admin, uid, update_body(version, team=team, role=role))
        assert result["version_no"] == version + 1
        current = await live_bindings(connection, uid)
        assert [r for r in current if r["team_id"] is None] == before
        assert {(r["role_code"], r["team_id"]) for r in current if r["team_id"] is not None} == (
            {(role, team)} if team else set()
        )
        assignments = await repository.assignments(connection, uid)
        assert [(m["team_id"], m["roles"], m["is_primary"]) for m in assignments] == (
            [(team, [role], True)] if team else []
        )
        assert await connection.fetchval("SELECT security.authorization_has('organization.manage')")
        assert await connection.fetchval("SELECT security.authorization_has('authorization.accounts_manage')")


@pytest.mark.parametrize("change", ["remove_admin", "deactivate"])
async def test_self_administrator_protection_still_rejects_and_rolls_back(connection, change):
    _, admin, uid, teams = await company_admin(connection)
    before = await live_bindings(connection, uid)
    body = update_body(team=teams[0])
    if change == "remove_admin":
        body["company_roles"] = ["operations"]
        body["roles"] = ["operations", "sales"]
    else:
        body["status"] = "inactive"
    with pytest.raises(ValueError, match="另一位公司管理员"):
        async with connection.transaction():
            await OperationsAccountService().update(connection, admin, uid, body)
    assert await live_bindings(connection, uid) == before
    member = await OperationsAccountRepository().member(connection, uid)
    assert member["version_no"] == 1 and member["status"] == "active"
    assert await OperationsAccountRepository().assignments(connection, uid) == []


async def test_other_admin_can_remove_company_role_without_preserving_obsolete_grant(connection):
    _, _, uid, teams = await company_admin(connection)
    before = await live_bindings(connection, uid)
    admin = await actor(connection, "ADMIN001")
    await OperationsAccountService().update(
        connection, admin, uid, update_body(team=teams[0], company_roles=["administrator"])
    )
    current = await live_bindings(connection, uid)
    assert [r for r in current if r["team_id"] is None] == [r for r in before if r["role_code"] == "administrator"]
    assert {r["role_code"] for r in current} == {"administrator", "sales"}


async def test_operations_cannot_change_protected_admin_appointments(connection):
    _, _, uid, teams = await company_admin(connection)
    before = await live_bindings(connection, uid)
    operations = await actor(connection, "OPS001")
    with pytest.raises((PermissionError, asyncpg.InsufficientPrivilegeError)):
        async with connection.transaction():
            await OperationsAccountService().update(connection, operations, uid, update_body(team=teams[0]))
    await actor(connection, "ADMIN001")
    assert await live_bindings(connection, uid) == before
    member = await OperationsAccountRepository().member(connection, uid)
    assert member["version_no"] == 1
    assert await OperationsAccountRepository().assignments(connection, uid) == []


async def test_http_self_save_succeeds_and_requires_relogin_with_updated_appointments(connection):
    _, _, uid, teams = await company_admin(connection)
    code = await connection.fetchval("SELECT account_code FROM platform.user_ref WHERE id=$1::uuid", uid)
    password = "Isolated-Changed-2026"
    async with await client_for(connection) as client:
        await sign_in(client, code)
        changed = await client.post("/api/v1/console/auth/password", json={
            "old_password": "Isolated-Testing-2026", "new_password": password,
        })
        assert changed.status_code == 200, changed.text
        saved = await client.put(
            "/api/v1/console/accounts/" + uid, json=update_body(team=teams[0]),
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["version_no"] == 2
        assert (await client.get("/api/v1/console/auth/me")).status_code == 401
        assert (await client.post("/api/v1/console/auth/refresh")).status_code == 401
        await sign_in(client, code, password)
        organization = await client.get("/api/v1/console/organization")
        assert organization.status_code == 200, organization.text
        member = next(r for r in organization.json()["accounts"] if r["id"] == uid)
        assert set(member["company_roles"]) == {"administrator", "operations"}
        assert member["team_id"] == teams[0]
        assert len(member["memberships"]) == 1
        assert member["memberships"][0]["roles"] == ["sales"]
