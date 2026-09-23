from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.contracts.models import SalesTargetUpdate
from sales_backend.db import Database
from sales_backend.repositories.profile import ProfileRepository
from sales_backend.repositories.profile_scope import scope_options
from sales_backend.services.operations import management_write
from sales_backend.services.profile_growth import scoped_growth
from sales_backend.services.profile_performance import performance, save_target
from sales_backend.services.profile_scores import competency_score


def require_sales_profile_identity(identity: RequestIdentity = Depends(get_identity)) -> None:
    if identity.actor.role.value not in {"sales", "supervisor", "manager"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "SALES_PROFILE_UNAVAILABLE")


router = APIRouter(
    prefix="/api/v1/profile", tags=["Profile"], dependencies=[Depends(require_sales_profile_identity)]
)


@router.get("/scope-options")
async def profile_scope_options(
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await scope_options(connection, identity.actor)


@router.get("/performance")
async def scoped_performance(
    scope: Literal["self", "person", "team", "department"] = "self",
    account_code: str | None = Query(default=None, max_length=64),
    team: str | None = Query(default=None, max_length=100),
    member_id: UUID | None = None,
    team_id: UUID | None = None,
    year: int | None = Query(default=None, ge=2000, le=2100),
    quarter: int | None = Query(default=None, ge=1, le=4),
    structure_period: Literal["current", "year"] = "current",
    period: Literal["week", "quarter", "year", "all"] = "week",
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await performance(
                connection, identity.actor, scope=scope, account_code=account_code, team=team, year=year, period=period,
                member_id=member_id, team_id=team_id, quarter=quarter, structure_period=structure_period
            )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/sales-targets")
async def update_sales_target(
    body: SalesTargetUpdate,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
) -> dict:
    return await management_write(
        database, identity.actor, idempotency_key, "profile.sales_target",
        body.model_dump(), lambda connection: save_target(connection, identity.actor, body),
    )


def _require_competency_subject(identity: RequestIdentity) -> None:
    if identity.actor.role.value not in {"sales", "supervisor"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "COMPETENCY_PROFILE_UNAVAILABLE")


@router.post("/sales-growth/review", status_code=202)
async def ensure_sales_growth_review(
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict[str, object]:
    _require_competency_subject(identity)
    async with database.transaction(identity.actor) as connection:
        review = await ProfileRepository().ensure_daily_competency_review(connection, identity.actor)
    return {"review_id": review["id"], "status": review["status"]}


@router.get("/sales-growth")
async def sales_growth(
    days: int = Query(30, ge=7, le=180),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict[str, object]:
    _require_competency_subject(identity)
    async with database.transaction(identity.actor, readonly=True) as connection:
        result = await ProfileRepository().competency_growth(connection, identity.actor, days=days)
        return await competency_score(connection, result)


@router.get("/sales-growth/scoped")
async def scoped_sales_growth(
    scope: Literal["self", "person", "team", "department"] = "self",
    account_code: str | None = Query(default=None, max_length=64),
    team: str | None = Query(default=None, max_length=100),
    days: int = Query(30, ge=7, le=180),
    member_id: UUID | None = None,
    team_id: UUID | None = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await scoped_growth(
                connection, identity.actor, scope=scope, account_code=account_code, team=team, days=days,
                member_id=member_id, team_id=team_id
            )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/evaluation")
async def evaluation_summary(
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict[str, object]:
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await ProfileRepository().evaluation_summary(connection, identity.actor)


@router.get("/team-members/{account_code}/sales-growth")
async def team_member_sales_growth(
    account_code: str,
    days: int = Query(30, ge=7, le=180),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict[str, object]:
    if identity.actor.role.value not in {"supervisor", "manager"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "TEAM_GROWTH_FORBIDDEN")
    repository = ProfileRepository()
    async with database.transaction(identity.actor, readonly=True) as connection:
        subject = await repository.visible_sales_subject(connection, identity.actor, account_code=account_code)
        if subject is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "TEAM_MEMBER_NOT_FOUND")
        result = await repository.competency_growth(
            connection,
            identity.actor,
            days=days,
            subject_user_id=subject["id"],
        )
        await competency_score(connection, result)
    result["subject"] = subject
    return result
