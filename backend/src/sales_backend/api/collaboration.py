from datetime import date
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.db import Database
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.repositories.collaboration import fde_directory, fde_scope_options
from sales_backend.repositories.fde_dashboard import fde_activity, fde_dashboard
from sales_backend.repositories.fde_recording import recording_opportunities
from sales_backend.services.capabilities import require_capability
from sales_backend.services.collaboration import set_opportunity_members
from sales_backend.services.idempotency import execute_mutation

router = APIRouter(prefix="/api/v1", tags=["FDE collaboration"])


class FdeMembersUpdate(BaseModel):
    member_ids: list[UUID] = Field(max_length=30)
    version_no: int = Field(ge=1)


@router.get("/directory/fde-members")
async def directory_fde(
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        people = await fde_directory(connection, query=q)
    return {"items": people[offset : offset + limit], "total": len(people)}


@router.put("/opportunities/{opportunity_id}/fde-members")
async def update_members(
    opportunity_id: UUID,
    body: FdeMembersUpdate,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
):
    try:
        async with database.transaction(identity.actor) as connection:
            return await execute_mutation(
                connection,
                identity.actor,
                idempotency_key,
                f"opportunity.fde_members:{opportunity_id}",
                body.model_dump(mode="json"),
                lambda: set_opportunity_members(
                    connection, identity.actor, str(opportunity_id), body.member_ids, body.version_no
                ),
            )
    except (VersionConflict, asyncpg.SerializationError) as exc:
        raise HTTPException(409, "协助人员已更新，请刷新后重试") from exc
    except (PermissionError, asyncpg.InsufficientPrivilegeError) as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/fde/scope-options")
async def profile_scope_options(
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await fde_scope_options(connection, identity.actor)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get(
    "/fde/dashboard",
    description=(
        "FDE 本人、授权成员或团队统计。company_rankings 为完整同级榜；"
        "scope=company_fde_teams 时，items[].user_id 表示团队 ID，role 为 fde_team。"
        "items[].opportunity_count 为所选期间已归档拜访涉及的去重商机数，"
        "团队按录入时团队快照去重，不累加个人去重数；followup_count 为归档次数。"
        "summary 仅统计所选对象，selection.team_ids 标识所选团队，不裁剪完整榜单。"
    ),
)
async def dashboard(
    period: Literal["week", "month", "quarter", "year", "all"] | None = None,
    scope: Literal["self", "team"] | None = None,
    member_id: UUID | None = None,
    member_ids: list[UUID] = Query([], max_length=100),
    team_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    year: int | None = Query(None, ge=2000, le=2100),
    quarters: list[int] = Query([], max_length=4),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    if any(q not in {1, 2, 3, 4} for q in quarters):
        raise HTTPException(422, "季度只能是1至4")
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await fde_dashboard(
                connection,
                identity.actor,
                scope=scope,
                member_id=member_id,
                member_ids=member_ids,
                team_id=team_id,
                year=year,
                quarters=quarters,
                period=period,
                date_from=date_from,
                date_to=date_to,
            )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/fde/activity")
async def activity(
    scope: Literal["self", "team"] | None = None,
    member_id: UUID | None = None,
    member_ids: list[UUID] = Query([], max_length=100),
    team_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    opportunity_id: UUID | None = None,
    period: Literal["week", "month", "quarter", "year", "all"] = "year",
    year: int | None = Query(None, ge=2000, le=2100),
    quarters: list[int] = Query([], max_length=4),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    if any(q not in {1, 2, 3, 4} for q in quarters):
        raise HTTPException(422, "季度只能是1至4")
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await fde_activity(
                connection,
                identity.actor,
                scope=scope,
                member_id=member_id,
                member_ids=member_ids,
                team_id=team_id,
                period=period,
                date_from=date_from,
                date_to=date_to,
                opportunity_id=opportunity_id,
                all_history=period == "all",
                year=year,
                quarters=quarters,
                offset=offset,
                limit=limit,
            )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/fde/visit-opportunities", description="仅返回本人可录入跟进的商机；包含 sales_channel、partner_id、"
            "partner_name，供跟进复用商机伙伴，空伙伴保持未知。")
async def visit_opportunities(
    customer_id: UUID | None = None,
    opportunity_id: UUID | None = None,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            await require_capability(connection, identity.actor, "visit.create")
            return await recording_opportunities(
                connection,
                customer_id=customer_id,
                opportunity_id=opportunity_id,
                query=q,
                limit=limit,
                offset=offset,
            )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
