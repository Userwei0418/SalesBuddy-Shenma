"""The profile surface is self-only, independent of the team dashboard."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.contracts.fde_profile import FdeProfileResponse
from sales_backend.db import Database
from sales_backend.services.fde_profile import get_profile, request_review

router = APIRouter(prefix="/api/v1/fde/profile", tags=["FDE collaboration"])


@router.get("", response_model=FdeProfileResponse)
async def profile(
    days: int = Query(30, ge=7, le=90),
    scope: Literal["self", "team"] = "self",
    member_id: UUID | None = None,
    team_id: UUID | None = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await get_profile(
                connection, identity.actor, days, scope=scope, member_id=member_id, team_id=team_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/review", status_code=202, response_model=FdeProfileResponse)
async def review(
    days: int = Query(30, ge=7, le=90),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    try:
        async with database.transaction(identity.actor) as connection:
            return await request_review(connection, identity.actor, days)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
