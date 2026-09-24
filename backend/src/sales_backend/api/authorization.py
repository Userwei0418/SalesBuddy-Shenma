"""Permission configuration is guarded by effective grants, never a display role."""

from dataclasses import asdict
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.management_dependencies import get_password_identity
from sales_backend.contracts.authorization import AccountAuthorizationSave, RoleSave
from sales_backend.db import Database
from sales_backend.domain.permission_catalog import PERMISSIONS
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.services.operations import management_write

router = APIRouter(prefix="/api/v1", tags=["Authorization"])
repository = AuthorizationRepository()


def require_console_permission(permission):
    async def dependency(
        identity: RequestIdentity = Depends(get_password_identity),
        database: Database = Depends(get_database),
    ):
        async with database.transaction(identity.actor, readonly=True) as connection:
            effective = await repository.effective(connection)
            effective.require("access.console")
            effective.require(permission)
        return identity
    return dependency


read_permissions = require_console_permission("authorization.read")
manage_roles = require_console_permission("authorization.roles_manage")
manage_accounts = require_console_permission("authorization.accounts_manage")
read_audit = require_console_permission("authorization.audit")


@router.get("/auth/permissions")
async def own_permissions(identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as connection:
        snapshot = await repository.snapshot(connection)
        effective = repository.from_snapshot(snapshot)
    return {**snapshot, "permissions": effective.capabilities()}


@router.get("/console/permissions/catalog")
async def catalog(identity: RequestIdentity = Depends(read_permissions)):
    return {"permissions": [asdict(item) for item in PERMISSIONS]}


@router.get("/console/permissions/roles")
async def roles(identity: RequestIdentity = Depends(read_permissions), database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return {"roles": await repository.roles(connection)}


@router.get("/console/permissions/options")
async def options(identity: RequestIdentity = Depends(read_permissions), database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await repository.directory(connection)


@router.get("/console/permissions/me")
async def console_permissions(identity: RequestIdentity = Depends(get_password_identity), database: Database = Depends(get_database)):
    return await own_permissions(identity, database)


@router.post("/console/permissions/roles", status_code=201)
async def create_role(
    body: RoleSave, idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(manage_roles), database: Database = Depends(get_database),
):
    return await management_write(database, identity.actor, idempotency_key, "authorization.role.create",
        body.model_dump(mode="json"), lambda c: repository.save_role(c, None, body.model_dump(mode="json")))


@router.put("/console/permissions/roles/{role_id}")
async def update_role(
    role_id: UUID, body: RoleSave, idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(manage_roles), database: Database = Depends(get_database),
):
    return await management_write(database, identity.actor, idempotency_key, f"authorization.role.update:{role_id}",
        body.model_dump(mode="json"), lambda c: repository.save_role(c, role_id, body.model_dump(mode="json")))


@router.get("/console/permissions/accounts/{user_id}")
async def account(user_id: UUID, identity: RequestIdentity = Depends(read_permissions),
                  database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as connection:
        try:
            snapshot = await repository.account(connection, user_id)
        except asyncpg.InsufficientPrivilegeError as exc:
            raise HTTPException(404, "账号不存在或不可访问") from exc
        return {**snapshot, "permissions": repository.from_snapshot(snapshot).capabilities()}


@router.put("/console/permissions/accounts/{user_id}")
async def update_account(
    user_id: UUID, body: AccountAuthorizationSave, idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(manage_accounts), database: Database = Depends(get_database),
):
    return await management_write(database, identity.actor, idempotency_key, f"authorization.account.update:{user_id}",
        body.model_dump(mode="json"), lambda c: repository.save_account(c, user_id, body.model_dump(mode="json")))


@router.get("/console/permissions/audit")
async def audit(limit: int = Query(50, ge=1, le=200), identity: RequestIdentity = Depends(read_audit),
                database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return {"entries": await repository.audit(connection, limit)}
