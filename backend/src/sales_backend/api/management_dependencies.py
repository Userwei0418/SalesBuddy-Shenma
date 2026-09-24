from fastapi import Depends, HTTPException

from sales_backend.api.dependencies import RequestIdentity, get_identity, get_database
from sales_backend.db import Database
from sales_backend.repositories.authorization import AuthorizationRepository

MANAGEMENT_ROLES = frozenset({"operations", "administrator"})


async def get_password_identity(
    identity: RequestIdentity = Depends(get_identity),
) -> RequestIdentity:
    if identity.auth_method != "password":
        raise HTTPException(403, "请使用正式账号密码登录")
    return identity


async def get_management_identity(
    identity: RequestIdentity = Depends(get_password_identity),
    database: Database = Depends(get_database),
) -> RequestIdentity:
    async with database.transaction(identity.actor, readonly=True) as connection:
        if not (await AuthorizationRepository().effective(connection)).allows("access.console"):
            raise HTTPException(403, "当前账号未获运营后台访问权限")
    if identity.must_change_password:
        raise HTTPException(403, "PASSWORD_CHANGE_REQUIRED")
    return identity


async def get_system_identity(identity: RequestIdentity = Depends(get_management_identity)) -> RequestIdentity:
    # The global route dependency enforces the exact administrative action.
    # No display-role bypass is allowed: an administrator can also be denied it.
    return identity
