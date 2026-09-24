from uuid import uuid4

import asyncpg
import pytest

from sales_backend.contracts.authorization import AccountAuthorizationSave, RoleSave
from sales_backend.db import set_request_context
from sales_backend.domain.authorization import ObjectScope
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.repositories.identity import IdentityRepository
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_sales_opportunity_permissions import account_fixture

pytestmark = pytest.mark.asyncio
repository = AuthorizationRepository()


async def resolve(connection, admin, user, role):
    record = await IdentityRepository().find_actor_by_id(connection, workspace_id=admin.workspace_id,
                                                        user_id=user["id"], role=role)
    await set_request_context(connection, record.context)
    return record.context


async def test_snapshot_unions_native_appointments_and_keeps_scopes(connection):
    user, teams, admin = await account_fixture(connection, ["fde", "sales", "supervisor"])
    current = await resolve(connection, admin, user, "fde")
    permissions = await repository.effective(connection)
    assert permissions.allows("opportunity.create") and permissions.allows("profile.fde_read")
    assert permissions.allows("opportunity.update", ObjectScope(admin.workspace_id, user["id"], teams["sales"]))
    assert permissions.allows("opportunity.update", ObjectScope(admin.workspace_id, str(uuid4()), teams["supervisor"]))
    assert not permissions.allows("opportunity.update", ObjectScope(admin.workspace_id, str(uuid4()), teams["sales"]))
    assert not permissions.allows("opportunity.update", ObjectScope(str(uuid4()), current.user_id, teams["supervisor"]))


async def test_explicit_account_grant_deny_cas_audit_and_no_appointment_changes(connection):
    user, teams, admin = await account_fixture(connection, ["fde"])
    before = await connection.fetch("SELECT * FROM platform.role_binding WHERE user_ref_id=$1::uuid", user["id"])
    payload = AccountAuthorizationSave(version_no=0, reason="明确额外授权", overrides=[
        dict(permission="opportunity.create", effect="allow", scope="teams", team_ids=[teams["fde"]]),
        dict(permission="profile.fde_read", effect="deny"),
    ]).model_dump(mode="json")
    saved = await repository.save_account(connection, user["id"], payload)
    assert saved["version_no"] == 1
    preview = await repository.snapshot(connection, user["id"])
    await resolve(connection, admin, user, "fde")
    permissions = await repository.effective(connection)
    assert permissions.allows("opportunity.create", ObjectScope(admin.workspace_id, user["id"], teams["fde"]))
    assert not permissions.allows("opportunity.create", ObjectScope(admin.workspace_id, user["id"], str(uuid4())))
    assert not permissions.allows("profile.fde_read")
    assert (await repository.snapshot(connection))["permission_version"] == preview["permission_version"]
    await set_request_context(connection, admin)
    with pytest.raises(asyncpg.SerializationError):
        async with connection.transaction():
            await repository.save_account(connection, user["id"], payload)
    assert len(await repository.audit(connection)) == 1
    assert await connection.fetch("SELECT * FROM platform.role_binding WHERE user_ref_id=$1::uuid", user["id"]) == before


async def test_custom_role_assignment_is_scoped_and_revocation_is_immediate(connection):
    user, teams, admin = await account_fixture(connection, ["fde"])
    role = await repository.save_role(connection, None, RoleSave(name="可创建商机", reason="新增可分配功能",
        permissions=[dict(permission="opportunity.create", scope="inherit")]).model_dump(mode="json"))
    await repository.save_account(connection, user["id"], AccountAuthorizationSave(version_no=0, reason="限定指定团队",
        roles=[dict(role_id=role["id"], scope="teams", team_ids=[teams["fde"]])]).model_dump(mode="json"))
    await resolve(connection, admin, user, "fde")
    before = await repository.snapshot(connection)
    assert (await repository.effective(connection)).allows("opportunity.create")
    await set_request_context(connection, admin)
    await repository.save_role(connection, role["id"], RoleSave(name="可创建商机", reason="停用角色",
        status="inactive", version_no=1, permissions=[]).model_dump(mode="json"))
    await resolve(connection, admin, user, "fde")
    assert not (await repository.effective(connection)).allows("opportunity.create")
    assert before["permission_version"] != (await repository.snapshot(connection))["permission_version"]


@pytest.mark.parametrize("change", ["role", "account"])
async def test_last_permission_administrator_cannot_be_removed(connection, change):
    admin = await actor(connection, "ADMIN001")
    with pytest.raises(asyncpg.InvalidParameterValueError, match="至少一位"):
        async with connection.transaction():
            if change == "role":
                role = next(r for r in await repository.roles(connection) if r["builtin_role_code"] == "administrator")
                await repository.save_role(connection, role["id"], RoleSave(name="系统管理员", reason="删除权限",
                    version_no=role["version_no"], permissions=[]).model_dump(mode="json"))
            else:
                await repository.save_account(connection, admin.user_id, AccountAuthorizationSave(version_no=0,
                    reason="删除权限", overrides=[dict(permission="authorization.accounts_manage", effect="deny")]
                ).model_dump(mode="json"))
    assert (await repository.effective(connection)).allows("authorization.accounts_manage")
    assert not await repository.audit(connection)


async def test_regular_user_cannot_write_or_preview_others_and_direct_writes_are_denied(connection):
    admin = await actor(connection, "ADMIN001")
    sales = await actor(connection, "XS001")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await repository.snapshot(connection, admin.user_id)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await repository.save_account(connection, sales.user_id, AccountAuthorizationSave(version_no=0,
                reason="越权", overrides=[dict(permission="authorization.roles_manage", effect="allow", scope="workspace")]
            ).model_dump(mode="json"))
    await set_request_context(connection, admin)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.execute("INSERT INTO config.permission_role(workspace_id,name) VALUES($1::uuid,'绕开审计')",
                                     admin.workspace_id)
