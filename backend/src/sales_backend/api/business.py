from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.models import VisitCreate
from sales_backend.contracts.team_directory import TeamDirectory
from sales_backend.contracts.types import UUIDString
from sales_backend.db import Database
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.repositories.dashboard import DashboardRepository
from sales_backend.repositories.dashboard_scope import dashboard_members, dashboard_team_groups
from sales_backend.repositories.directory import DirectoryRepository
from sales_backend.repositories.opportunities import NAME_CONFLICT_MESSAGE, OpportunityRepository
from sales_backend.repositories.opportunity_overview import opportunity_overview
from sales_backend.repositories.task_targets import TaskTargetRepository
from sales_backend.repositories.team_directory import TeamPurpose, attach_member_teams, selectable_teams
from sales_backend.repositories.visits import VisitRepository
from sales_backend.repositories.workbench import WorkbenchRepository
from sales_backend.services.capabilities import require_capability
from sales_backend.services.dashboard_rankings import dashboard_rankings
from sales_backend.services.idempotency import execute_mutation
from sales_backend.services.visit_access import require_visit_recording_scope, require_visit_supplement_scope
from sales_backend.services.visit_archive import archive_visit

router = APIRouter(prefix="/api/v1", tags=["Business"])


@router.get('/opportunities/create-options')
async def opportunity_create_options(identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)) -> dict:
    from sales_backend.repositories.authorization_checks import opportunity_creation_options
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await opportunity_creation_options(connection, identity.actor)


@router.get("/metadata/business-options")
async def business_options(identity: RequestIdentity = Depends(get_identity)) -> dict:
    """Current server-owned form/filter choices. Requires a valid actor session."""
    from sales_backend.domain.business_options import business_options
    return business_options()


@router.get("/directory/teams", response_model=TeamDirectory)
async def directory_teams(
    purpose: TeamPurpose = 'browse',
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return {'data_source': 'database', 'teams': await selectable_teams(connection, identity.actor, purpose)}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/directory/members")
async def directory_members(
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        items = await DirectoryRepository().members(connection, identity.actor)
        teams = await selectable_teams(connection, identity.actor)
        items = await attach_member_teams(connection, identity.actor, items, teams)
    return {"items": items, "teams": teams, "data_source": "database"}


@router.get("/directory/task-assignees")
async def task_assignees(
    opportunity_id: UUID | None = None,
    customer_id: UUID | None = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        items = await DirectoryRepository().task_assignees(
            connection, identity.actor, opportunity_id=opportunity_id, customer_id=customer_id
        )
    return {"items": items}


@router.get("/directory/task-positions")
async def task_positions(
    opportunity_id: UUID | None = None,
    customer_id: UUID | None = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        return {
            "items": await TaskTargetRepository().available_positions(
                connection, identity.actor, opportunity_id=opportunity_id, customer_id=customer_id
            )
        }


@router.get("/workbench")
async def workbench(
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await WorkbenchRepository().load(connection, identity.actor)


@router.get("/dashboard/rankings", description=(
    "公共排名排除团队类型 fde，组织筛选与活跃商机事实不受影响。团队跟进 calculation=team_followup_per_capita_v1，"
    "value 为未舍入人均次数（排序依据），average 保留两位，record_count 为总次数，"
    "member_count 为当前有效销售业务成员数（含零次）。"
    "members 只含授权范围内姓名、账号及个人次数汇总；current_member=false 的原团队贡献者不计入分母。"
    "分母为零时 value/average/rank 为 null，不参与排名。ACV仍按金额，record_count仅为辅助商机数；个人跟进仍按次数。"
))
async def dashboard_ranking_view(
    year: int = Query(ge=2000, le=2100),
    quarters: list[int] = Query(min_length=1, max_length=4),
    personal: bool = False,
    member_id: UUID | None = None,
    team_groups: list[str] = Query(default=[], max_length=100),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    if any(quarter < 1 or quarter > 4 for quarter in quarters):
        raise HTTPException(422, "季度只能是1至4")
    async with database.transaction(identity.actor, readonly=True) as connection:
        try:
            return await dashboard_rankings(connection, identity.actor, year=year, quarters=quarters, personal=personal,
                                            member_id=member_id, team_groups=team_groups)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc


@router.get("/dashboard/options")
async def dashboard_options(
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        groups = await dashboard_team_groups(connection, identity.actor)
        return {"members": await dashboard_members(connection, identity.actor),
                "team_groups": groups}


@router.get("/dashboard")
async def dashboard(
    personal: bool = False,
    member_id: UUID | None = None,
    team_groups: list[str] = Query(default=[], max_length=100),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    """Complete permitted facts; actuals are recorded amounts, forecasts remain separate."""
    async with database.transaction(identity.actor, readonly=True) as connection:
        try:
            return await DashboardRepository().load(connection, identity.actor, personal=personal,
                                                    member_id=member_id, team_groups=team_groups)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc


@router.get("/opportunities/overview", description=(
    "商机总览 v3：total 是当前授权范围内全部状态（含输单）的商机存量，不随季度改变；"
    "newCount 按真实业务建单日期统计，历史导入优先读取原始建单日，缺失或非法日期计入 "
    "missingCreatedDates，不回退导入时刻；普通新录入使用系统创建时间。"
    "active 按正式跟进日期、won 按实际成单日期统计；季度为空表示跨年份全部时间。"
    "所有日期按上海业务日处理。审计 created_at 与飞书系统创建时间保持不变。"
))
async def opportunities_overview(
    scope: str | None = Query(None, pattern="^(self|team)$"),
    member_id: UUID | None = None,
    member_ids: list[UUID] = Query([], max_length=100),
    year: int = Query(ge=2000, le=2100),
    quarters: list[int] = Query(default=[], max_length=4),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    if any(quarter < 1 or quarter > 4 for quarter in quarters):
        raise HTTPException(422, "季度只能是1至4")
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await opportunity_overview(
                connection, identity.actor, year=year, quarters=quarters,
                scope=scope, member_id=member_id, member_ids=member_ids,
            )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/opportunities/{opportunity_id}/detail")
async def opportunity_detail(
    opportunity_id: UUID,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        result = await OpportunityRepository().detail(connection, identity.actor, str(opportunity_id))
    if result is None:
        raise HTTPException(404, "商机不存在或无权查看")
    return result


@router.get("/opportunities")
async def opportunities(
    customer_id: UUID | None = Query(default=None),
    owner: str | None = Query(default=None, max_length=100),
    q: str | None = Query(None, max_length=100),
    stages: list[Literal["identified", "qualified", "solution", "proposal", "negotiation", "won", "lost"]] = Query(
        [], max_length=7
    ),
    team: str | None = Query(None, max_length=100),
    team_id: UUID | None = None,
    grade: Literal["A", "B", "C", "D"] | None = None,
    product_line: str | None = Query(None, max_length=100),
    year: int | None = Query(None, ge=2000, le=2100),
    quarters: list[int] = Query([], max_length=4),
    close_period: Literal["all", "month", "quarter", "year"] = "all",
    order: Literal["close_date", "quarter_stage"] = "close_date",
    probability: int | None = Query(default=None),
    stage: str | None = Query(default=None, max_length=50),
    close_from: date | None = Query(default=None),
    close_to: date | None = Query(default=None),
    page_size: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
    include_closed: bool = False,
    scope: str | None = Query(None, pattern="^(self|team)$"),
    member_id: UUID | None = None,
    member_ids: list[UUID] = Query([], max_length=100),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    if any(quarter not in {1, 2, 3, 4} for quarter in quarters) or (quarters and year is None):
        raise HTTPException(422, "请选择有效年份和季度")
    if probability is not None and probability not in {10, 30, 50, 70, 90, 100}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "INVALID_PROBABILITY")
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            result = await OpportunityRepository().page(
                connection,
                identity.actor,
                customer_id=customer_id,
                query=q.strip() if q else None,
                stages=stages,
                team=team,
                team_id=team_id,
                grade=grade,
                product_line=product_line,
                year=year,
                quarters=quarters,
                close_period=close_period,
                order=order,
                owner_name=owner,
                probability=probability,
                stage_code=stage,
                close_from=close_from,
                close_to=close_to,
                limit=page_size,
                offset=offset,
                include_closed=include_closed,
                scope=scope,
                member_id=member_id,
                member_ids=member_ids,
            )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return result


@router.get("/visits/form-schema")
async def visit_form_schema(
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        items = await VisitRepository().form_schema(connection)
    return {"items": items}


@router.post("/visits", status_code=201)
async def create_visit(
    body: VisitCreate,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
) -> dict:
    try:
        async with database.transaction(identity.actor) as connection:
            await require_capability(connection, identity.actor, "visit.create")
            await require_visit_recording_scope(
                connection, identity.actor, body.customer_id, body.fields.get("opportunity_id")
            )
            return await execute_mutation(
                connection,
                identity.actor,
                idempotency_key,
                "visits.archive",
                body.model_dump(),
                lambda: archive_visit(
                    connection,
                    identity.actor,
                    customer_id=body.customer_id,
                    fields={**body.fields, "_fde_participant_ids": [str(x) for x in body.fde_participant_ids]},
                ),
            )
    except (FileExistsError, VersionConflict, asyncpg.SerializationError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except asyncpg.UniqueViolationError as exc:
        if exc.constraint_name == "uq_opportunity_customer_name":
            raise HTTPException(409, NAME_CONFLICT_MESSAGE) from exc
        raise
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/visits/{visit_id}", description="按权限读取正式跟进；历史记录可含伙伴主体 partner_id/partner_name，"
            "原跟进人 original_recorder_name、当前管理人 manager_name 及多条 linked_opportunities。"
            "customer_id 可为空，原跟进次数不转给管理人。")
async def get_visit(
    visit_id: UUIDString, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)
) -> dict:
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await VisitRepository().detail(connection, visit_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.patch("/visits/{visit_id}")
async def supplement_visit(
    visit_id: UUIDString,
    body: dict,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
) -> dict:
    try:
        async with database.transaction(identity.actor) as connection:
            await require_capability(connection, identity.actor, "visit.supplement")
            await require_visit_supplement_scope(connection, identity.actor, visit_id)
            return await execute_mutation(
                connection,
                identity.actor,
                idempotency_key,
                f"visits.supplement:{visit_id}",
                body,
                lambda: VisitRepository().supplement(connection, identity.actor, visit_id, body),
            )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get(
    "/directory/colleagues",
    description="拜访协同人候选：公司内有效账号，排除当前具有 FDE 或 FDE主管身份的人员；不限制销售部门。",
)
async def visit_colleagues(
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        rows = await DirectoryRepository().colleagues(connection, identity.actor)
    return {"items": [dict(r) for r in rows]}
