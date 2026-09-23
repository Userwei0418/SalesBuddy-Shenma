from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi import Path as ApiPath
from fastapi.responses import HTMLResponse

from sales_backend.api.dependencies import RequestIdentity, get_database, get_settings
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.management_dependencies import get_system_identity
from sales_backend.api.models import AgentRuntimeConfigRollback, AgentRuntimeConfigUpdate
from sales_backend.config import Settings
from sales_backend.db import Database
from sales_backend.repositories.admin import AgentRuntimeConfigRepository
from sales_backend.security.runtime_credentials import RuntimeCredentialUnavailable
from sales_backend.services.runtime_config_management import ConfigVersionConflict, RuntimeConfigService

router = APIRouter(tags=["System configuration"])
ADMIN_HTML = Path(__file__).resolve().parent.parent / "web" / "admin.html"


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def admin_page() -> HTMLResponse:
    return HTMLResponse(
        ADMIN_HTML.read_text(encoding="utf-8"),
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        },
    )


@router.get("/api/v1/admin/agent-config")
async def get_agent_config(
    identity: RequestIdentity = Depends(get_system_identity),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await RuntimeConfigService(settings).get(connection, identity.actor)


@router.get("/api/v1/admin/agent-config/releases")
async def get_agent_config_releases(
    limit: int = Query(default=50, ge=1, le=200),
    identity: RequestIdentity = Depends(get_system_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        return {"items": await AgentRuntimeConfigRepository().releases(connection, identity.actor, limit=limit)}


@router.post("/api/v1/admin/agent-config/releases/{version}/rollback")
async def rollback_agent_config(
    version: Annotated[int, ApiPath(ge=1, le=2147483646)],
    body: AgentRuntimeConfigRollback,
    key: MutationKey = None,
    identity: RequestIdentity = Depends(get_system_identity),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        async with database.transaction(identity.actor) as connection:
            return await RuntimeConfigService(settings).rollback(
                connection, identity.actor, version, body.expected_version, key=key)
    except ConfigVersionConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": str(exc)}) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.put("/api/v1/admin/agent-config")
async def update_agent_config(
    body: AgentRuntimeConfigUpdate,
    key: MutationKey = None,
    identity: RequestIdentity = Depends(get_system_identity),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        async with database.transaction(identity.actor) as connection:
            return await RuntimeConfigService(settings).save(connection, identity.actor, body.model_dump(), key=key)
    except ConfigVersionConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": str(exc)}) from exc
    except RuntimeCredentialUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
