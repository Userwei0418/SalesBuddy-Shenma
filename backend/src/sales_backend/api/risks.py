from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.models import PageResponse, RiskResolve
from sales_backend.contracts.types import UUIDString
from sales_backend.db import Database
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.repositories.risks import RiskForbidden, RiskNotFound, RiskRepository
from sales_backend.services.capabilities import require_capability

router = APIRouter(prefix="/api/v1/risks", tags=["Risks"])


@router.get("", response_model=PageResponse)
async def list_risks(
    risk_status: str | None = Query(default=None, alias="status", pattern="^(open|resolved)$"),
    page_size: int = Query(default=50, ge=1, le=100),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> PageResponse:
    async with database.transaction(identity.actor, readonly=True) as connection:
        items = await RiskRepository().list(connection, status=risk_status, limit=page_size)
    return PageResponse(items=items)


@router.get("/{risk_id}")
async def risk_detail(
    risk_id: UUIDString,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        item = await RiskRepository().detail(connection, risk_id=risk_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "RISK_NOT_FOUND")
    return item


@router.post("/{risk_id}/resolve")
async def resolve_risk(
    risk_id: UUIDString,
    body: RiskResolve,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    try:
        async with database.transaction(identity.actor) as connection:
            await require_capability(connection, identity.actor, "risk.resolve")
            return await RiskRepository().resolve(
                connection,
                actor=identity.actor,
                risk_id=risk_id,
                resolution_note=body.resolution_note,
                expected_version=body.version_no,
            )
    except RiskNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except RiskForbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except VersionConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
