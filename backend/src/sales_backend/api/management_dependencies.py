from fastapi import Depends, HTTPException

from sales_backend.api.dependencies import RequestIdentity, get_identity

MANAGEMENT_ROLES = frozenset({"operations", "administrator"})


async def get_password_identity(
    identity: RequestIdentity = Depends(get_identity),
) -> RequestIdentity:
    if identity.auth_method != "password":
        raise HTTPException(403, "请使用正式账号密码登录")
    return identity


async def get_management_identity(
    identity: RequestIdentity = Depends(get_password_identity),
) -> RequestIdentity:
    if identity.actor.role.value not in MANAGEMENT_ROLES:
        raise HTTPException(403, "当前账号没有后台管理权限")
    if identity.must_change_password:
        raise HTTPException(403, "PASSWORD_CHANGE_REQUIRED")
    return identity


async def get_system_identity(identity: RequestIdentity = Depends(get_management_identity)) -> RequestIdentity:
    if identity.actor.role.value != "administrator":
        raise HTTPException(403, "此操作需要系统管理员权限")
    return identity
