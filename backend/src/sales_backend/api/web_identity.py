from fastapi import Depends, HTTPException
from sales_backend.api.dependencies import RequestIdentity, get_identity, get_database
from sales_backend.db import Database
from sales_backend.services.authorization import require_permission


async def get_business_web_identity(
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
) -> RequestIdentity:
    if identity.auth_method != "password" or identity.client_channel != "business_web":
        raise HTTPException(403, "BUSINESS_WEB_LOGIN_REQUIRED")
    async with database.transaction(identity.actor, readonly=True) as connection:
        await require_permission(connection, "access.business_web")
    return identity
