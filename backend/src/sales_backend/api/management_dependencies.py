from fastapi import Depends, HTTPException, Request

from sales_backend.api.dependencies import RequestIdentity, get_identity, get_database
from sales_backend.db import Database
from sales_backend.domain.authorization import Authorization
from sales_backend.repositories.authorization import AuthorizationRepository

MANAGEMENT_ROLES = frozenset({"operations", "administrator"})


async def get_password_identity(
    identity: RequestIdentity = Depends(get_identity),
) -> RequestIdentity:
    if identity.auth_method != "password":
        raise HTTPException(403, "请使用正式账号密码登录")
    return identity


async def get_management_identity(
    request: Request,
    identity: RequestIdentity = Depends(get_password_identity),
    database: Database = Depends(get_database),
) -> RequestIdentity:
    # The global route gate already resolves a snapshot for this request and
    # identity. Reuse only that surface check, never object/write authorization.
    # Each later business transaction still refreshes the database grants.
    effective = getattr(request.state, "permissions", None)
    if not (
        getattr(request.state, "verified_identity", None) is identity
        and isinstance(effective, Authorization)
        and (effective.workspace_id, effective.user_id) == (identity.actor.workspace_id, identity.actor.user_id)
    ):
        async with database.transaction(identity.actor, readonly=True) as connection:
            effective = await AuthorizationRepository().effective(connection)
    if not effective.allows("access.console"):
        raise HTTPException(403, "当前账号未获运营后台访问权限")
    if identity.must_change_password:
        raise HTTPException(403, "PASSWORD_CHANGE_REQUIRED")
    return identity


async def get_system_identity(identity: RequestIdentity = Depends(get_management_identity)) -> RequestIdentity:
    # The global route dependency enforces the exact administrative action.
    # No display-role bypass is allowed: an administrator can also be denied it.
    return identity
