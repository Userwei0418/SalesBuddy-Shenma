from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey, require_material_key
from sales_backend.api.models import VisitCreate
from sales_backend.contracts.opportunity_creation import OpportunityCreateAccess, OpportunityCreateCustomers
from sales_backend.contracts.team_directory import TeamDirectory
from sales_backend.contracts.types import UUIDString
from sales_backend.contracts.visit_management import VisitManagementPage
from sales_backend.read_budget import read_budget
from sales_backend.db import Database, json_value
from sales_backend.domain.activity_window import activity_window_bounds
from sales_backend.domain.business_read import ReadPermission
from sales_backend.domain.team_directory import TeamDirectoryPermission
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.repositories.dashboard import DashboardRepository
from sales_backend.repositories.dashboard_scope import dashboard_members
from sales_backend.repositories.directory import DirectoryRepository
from sales_backend.repositories.opportunities import NAME_CONFLICT_MESSAGE, OpportunityRepository
from sales_backend.repositories.opportunity_overview import opportunity_overview
from sales_backend.repositories.task_targets import TaskTargetRepository
from sales_backend.repositories.team_directory import (
    TeamPurpose,
    attach_member_teams,
    default_team_id,
    selectable_teams,
)
from sales_backend.repositories.visit_management import VisitManagementRepository
from sales_backend.repositories.visits import VisitRepository
from sales_backend.repositories.workbench import WorkbenchRepository
from sales_backend.services.capabilities import require_capability
from sales_backend.services.dashboard_rankings import dashboard_rankings
from sales_backend.services.idempotency import execute_mutation
from sales_backend.services.visit_access import (
    require_visit_recording_scope, require_visit_supplement_scope,
    validate_visit_attendance, validate_visit_optional_actions,
)
from sales_backend.services.visit_archive import archive_visit

router = APIRouter(prefix="/api/v1", tags=["Business"])


@router.get('/opportunities/create-options')
async def opportunity_create_options(
    include_owners: bool = Query(False, description="补充当前授权内的商机负责人选项，不授予组织管理权限"),
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
) -> dict:
    from sales_backend.repositories.authorization_checks import (
        opportunity_creation_form_options,
        opportunity_creation_options,
    )
    async with database.transaction(identity.actor, readonly=True) as connection:
        load = opportunity_creation_form_options if include_owners else opportunity_creation_options
        return await load(connection, identity.actor)


@router.get("/opportunities/create-customers", response_model=OpportunityCreateCustomers, description="创建专用客户候选，保留 customer.reference 授权；不改变客户浏览范围。最终负责人及团队仍需保存时校验。")
async def opportunity_create_customers(
    q: str = Query("", max_length=200), limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
) -> dict:
    from sales_backend.repositories.opportunity_creation import creation_customers
    async with read_budget(), database.transaction(identity.actor, readonly=True) as connection:
        return await creation_customers(connection, q.strip(), limit, offset)


@router.get("/opportunities/create-access", response_model=OpportunityCreateAccess, description="当前客户的创建资格与安全拒绝码；只判断客户侧门槛，指定负责人/团队在保存时按原规则校验。")
async def opportunity_create_access(
    customer_id: UUID,
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
) -> dict:
    from sales_backend.repositories.opportunity_creation import creation_access
    async with read_budget(), database.transaction(identity.actor, readonly=True) as connection:
        return await creation_access(connection, customer_id)


@router.get("/metadata/business-options")
async def business_options(identity: RequestIdentity = Depends(get_identity)) -> dict:
    """Current server-owned form/filter choices. Requires a valid actor session."""
    from sales_backend.domain.business_options import business_options
    return business_options()


@router.get("/directory/teams", response_model=TeamDirectory)
async def directory_teams(
    purpose: TeamPurpose = 'browse',
    permission: TeamDirectoryPermission | None = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return {'data_source': 'database', 'teams': await selectable_teams(connection, identity.actor, purpose, permission=permission)}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/directory/members", description="授权成员目录；items 包含稳定 id、account_code、全部有效 team_ids；teams 保留空团队；defaults.team_id 为当前人可选的有效主团队或 null。")
async def directory_members(
    permission: ReadPermission = "directory.read",
    include_stats: bool = True,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            items = await DirectoryRepository().members(connection, identity.actor,
                permission=permission, include_stats=include_stats)
            teams = await selectable_teams(connection, identity.actor, permission=permission)
            items = await attach_member_teams(connection, identity.actor, items, teams)
            primary = await default_team_id(connection, identity.actor, teams)
        return {"items": items, "teams": teams, "defaults": {"team_id": primary}, "data_source": "database"}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


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
    "ACV rows/groups 的 known_amount_count / missing_amount_count 为同范围已填 / 未填金额商机数，明确零算已填。"
    "value 与 rank 兼容保留；有缺项时仅为已知金额小计及暂定排名，全缺项不得显示为已登记零。"
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


@router.get("/dashboard/options", description="看板授权目录；members 包含稳定 id、account_code、全部有效 team_ids，teams 为可选团队，defaults.team_id 为当前人可选的有效主团队或 null；保留旧 team_groups。")
async def dashboard_options(
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        teams = await selectable_teams(connection, identity.actor, 'dashboard')
        members = await dashboard_members(connection, identity.actor)
        members = await attach_member_teams(connection, identity.actor, members, teams)
        return {"members": members, "teams": teams,
                "team_groups": [{"code": "team:" + team["id"], "name": team["name"],
                                 "team_id": team["id"], "kind": ""} for team in teams],
                "defaults": {"team_id": await default_team_id(connection, identity.actor, teams)}}


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
    scope: str | None = Query(None, pattern="^(self|team|authorized)$"),
    member_id: UUID | None = None,
    member_ids: list[UUID] = Query([], max_length=100),
    team_id: UUID | None = None,
    owner_id: UUID | None = None,
    year: int = Query(ge=2000, le=2100),
    quarters: list[int] = Query(default=[], max_length=4),
    activity_window: Literal["all", "today", "day", "3d", "week", "month", "year"] = Query("all"),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    if any(quarter < 1 or quarter > 4 for quarter in quarters):
        raise HTTPException(422, "季度只能是1至4")
    if activity_window != "all":
        try:
            activity_window_bounds(datetime.now(ZoneInfo("Asia/Shanghai")).date(), activity_window)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    try:
        async with read_budget(), database.transaction(identity.actor, readonly=True) as connection:
            return await opportunity_overview(
                connection, identity.actor, year=year, quarters=quarters,
                scope=scope, member_id=member_id, member_ids=member_ids, team_id=team_id, owner_id=owner_id,
                activity_window=activity_window,
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


@router.get("/opportunities", description=(
    "scope=authorized 为当前可查看商机并集，不授予团队/成员目录权限；self/team保留原范围。"
    "purpose 默认为 list：未传 customer_id 时按商机直接业务范围读取，传入时按该客户全景普通读取范围查询。"
    "purpose=upstream 使用普通读取范围，强制 trade_relation=distributor，必须传 exclude_customer_id，"
    "可选 exclude_opportunity_id；不得同时传 customer_id 或其他 trade_relation，冲突返回 422。"
    "普通贸易关系与排除筛选不推断查询用途；读取范围不授予修改、录入或实际登记权限。"
    "owner_id 精确筛选稳定成员ID，优先于兼容参数 owner。"
    "owner_options 为授权的当前成员目录（id/name/account_code/team_ids），不从商机记录推导；"
    "team_options 保留空团队，defaults.team_id 为当前人可选的有效主团队或 null。"
    "include_overview=true 时额外返回 overview，统计与 summary.total 相同的完整筛选结果，不受分页影响；"
    "overview_year 必填，overview_quarters 为空表示跨年，1至4全选表示该年全年。"
    "created_year/created_quarter 成对提供，按真实业务创建日期的自然季度筛选；未知历史日期不匹配。"
    "active_quarter=true 时必须指定 financial_year/financial_quarter，仅保留该季度预测确收或回款大于零的商机。"
    "两种新增筛选均在分页与 summary/overview 之前作用，独立于原关单时间筛选。"
    "原 year/quarters/close_period 仍按预计关单时间筛选商机；overview 的 won 按实际成单日、"
    "active 按正式跟进日去重且排除输单、newCount 按真实业务建单日统计。"
    "各指标可能重叠，不能相加；日期按上海业务日，缺失历史日期不补造。"
    "overview.definition_version=opportunity_filtered_overview_v1；未启用 include_overview 时响应结构不增加 overview。"
))
async def opportunities(
    purpose: Literal["list", "upstream"] = Query("list", description="项目/客户全景列表，或显式的上游总代候选"),
    customer_id: UUID | None = Query(default=None),
    owner: str | None = Query(default=None, max_length=100),
    owner_id: UUID | None = Query(None, description="稳定负责人ID；提供时优先于旧姓名参数 owner"),
    q: str | None = Query(None, max_length=100),
    stages: list[Literal["identified", "qualified", "solution", "proposal", "negotiation", "won", "lost"]] = Query(
        [], max_length=7
    ),
    team: str | None = Query(None, max_length=100),
    team_id: UUID | None = None,
    grade: Literal["A", "B", "C", "D"] | None = None,
    product_line: str | None = Query(None, max_length=200),
    product_line_id: UUID | None = Query(None, description="统一目录产品线ID，匹配物料派生或无物料意向集合"),
    year: int | None = Query(None, ge=2000, le=2100),
    quarters: list[int] = Query([], max_length=4),
    close_period: Literal["all", "month", "quarter", "year"] = "all",
    created_year: int | None = Query(None, ge=2000, le=2100),
    created_quarter: int | None = Query(None, ge=1, le=4),
    active_quarter: bool = False,
    financial_year: int | None = Query(None, ge=2000, le=2100),
    financial_quarter: int | None = Query(None, ge=1, le=4),
    include_overview: bool = False,
    overview_year: int | None = Query(None, ge=2000, le=2100),
    overview_quarters: list[int] = Query([], max_length=4),
    activity_window: Literal["all", "today", "day", "3d", "week", "month", "year"] = Query("all"),
    order: Literal["close_date", "quarter_stage", "recent_follow_up"] = Query(
        "close_date", description="recent_follow_up 按可见正式跟进日期倒序，未跟进后置；在完整授权候选中排序后分页。"
    ),
    probability: int | None = Query(default=None),
    stage: str | None = Query(default=None, max_length=50),
    close_from: date | None = Query(default=None),
    close_to: date | None = Query(default=None),
    page_size: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
    include_closed: bool = False,
    trade_relation: Literal['direct','distributor','downstream'] | None = None,
    exclude_customer_id: UUID | None = None,
    exclude_opportunity_id: UUID | None = None,
    scope: str | None = Query(None, pattern="^(self|team|authorized)$"),
    member_id: UUID | None = None,
    member_ids: list[UUID] = Query([], max_length=100),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    if any(quarter not in {1, 2, 3, 4} for quarter in quarters) or (quarters and year is None):
        raise HTTPException(422, "请选择有效年份和季度")
    if (any(quarter not in {1, 2, 3, 4} for quarter in overview_quarters)
            or (include_overview and overview_year is None)):
        raise HTTPException(422, "请选择有效的统计年份和季度")
    if (created_year is None) != (created_quarter is None):
        raise HTTPException(422, "请选择完整的创建年份和季度")
    if active_quarter and (financial_year is None or financial_quarter is None):
        raise HTTPException(422, "请选择有效的财务年份和季度")
    if activity_window != "all":
        try:
            activity_window_bounds(datetime.now(ZoneInfo("Asia/Shanghai")).date(), activity_window)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if probability is not None and probability not in {10, 30, 50, 70, 90, 100}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "INVALID_PROBABILITY")
    try:
        async with read_budget(), database.transaction(identity.actor, readonly=True) as connection:
            result = await OpportunityRepository().page(
                connection,
                identity.actor,
                purpose=purpose,
                customer_id=customer_id,
                query=q.strip() if q else None,
                stages=stages,
                team=team,
                team_id=team_id,
                grade=grade,
                product_line=product_line,
                product_line_id=product_line_id,
                year=year,
                quarters=quarters,
                close_period=close_period,
                created_year=created_year,
                created_quarter=created_quarter,
                active_quarter=active_quarter,
                financial_year=financial_year,
                financial_quarter=financial_quarter,
                include_overview=include_overview,
                overview_year=overview_year,
                overview_quarters=overview_quarters,
                order=order,
                owner_name=owner,
                owner_id=owner_id,
                probability=probability,
                stage_code=stage,
                close_from=close_from,
                close_to=close_to,
                limit=page_size,
                offset=offset,
                include_closed=include_closed,
                trade_relation=trade_relation,
                exclude_customer_id=exclude_customer_id,
                exclude_opportunity_id=exclude_opportunity_id,
                scope=scope,
                member_id=member_id,
                member_ids=member_ids,
                activity_window=activity_window,
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
    require_material_key(body.fields.get("_opportunity_mutation") or {}, idempotency_key)
    try:
        async with database.transaction(identity.actor) as connection:
            async def authorize():
                await connection.execute("SELECT security.authorization_refresh()")
                await require_capability(connection, identity.actor, "visit.create")
                await require_visit_recording_scope(
                    connection, identity.actor, body.customer_id, body.fields.get("opportunity_id")
                )
                await validate_visit_optional_actions(
                    connection, identity.actor, body.customer_id, body.fields.get("opportunity_id"),
                    mutation=body.fields.get("_opportunity_mutation"),
                    collaborators=body.fields.get("collaborator_ids", []),
                    first_visit=body.fields.get("is_first_visit", False),
                )
                await validate_visit_attendance(connection, identity.actor, body.fde_participant_ids,
                                                body.fields.get("opportunity_id"), body.fields.get("_opportunity_mutation"),
                                                customer_id=body.customer_id)

            await authorize()
            result = await execute_mutation(
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
                authorize=authorize,
            )
            # Re-evaluate even a cached idempotent response. New recording
            # authority must never replay an old full body after read loss.
            read_permissions = json_value(await connection.fetchval(
                "SELECT security.visit_saved_read_permissions($1::uuid)", result["id"],
            ))
            if not read_permissions.get("can_read"):
                return {key: result[key] for key in (
                    "id", "status", "customer_id", "opportunity_id", "completed_count", "total_count", "replayed",
                ) if key in result} | {"can_read_visit": False}
            return {**result, "can_read_visit": True}
    except asyncpg.DeadlockDetectedError as exc:
        raise HTTPException(409, "同时有其他修改正在保存，本次归档未完成，请刷新后重试") from exc
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


@router.get("/visits/range", response_model=VisitManagementPage, description=(
    "PC Web 跟进记录范围读取。保留逐客户 GET /visits 的历史契约；本接口按当前账号 visit.read "
    "原授权范围与当前客户认领者及其有效单一部门祖先在任主管/FDE主管的正式跟进范围一次读取多个客户。"
    "有效经理独立读取本公司客户（含未认领客户）的正式跟进；仅confirmed/archived，支持日期、跟进人、团队和关键词筛选。"
    "认领或任职变化会撤销相应新增读取分支，原有资格保留；不新增档案跳转、编辑、原件、录音或导出权限。"
))
async def visit_range(
    scope: Literal["all", "mine"] = Query("all"),
    q: str = Query("", max_length=100),
    recorder_ids: list[UUID] = Query(default=[], max_length=100),
    team_id: UUID | None = None,
    customer_id: UUID | None = None,
    opportunity_id: UUID | None = None,
    date_basis: Literal["interaction", "created"] = "interaction",
    date_from: date | None = None,
    date_to: date | None = None,
    page_size: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    """Cross-customer read for the Web follow-up/report pages.

    This uses the same RLS-protected projection as the management read model.
    The optional scope filters narrow the result after authorization; passing an
    arbitrary team/member ID can only reduce the result and never expands it.
    """
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    if date_to == date.max:
        raise HTTPException(422, "结束日期超出支持范围")
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        return await VisitManagementRepository().page(
            connection,
            scope=scope,
            q=q.strip(),
            recorder_ids=[str(value) for value in recorder_ids],
            team_id=str(team_id) if team_id else None,
            customer_id=str(customer_id) if customer_id else None,
            opportunity_id=str(opportunity_id) if opportunity_id else None,
            date_basis=date_basis,
            date_from=date_from,
            date_to=date_to,
            limit=page_size,
            offset=offset,
        )


@router.get("/visits/{visit_id}", description="按权限读取正式跟进；历史记录可含伙伴主体 partner_id/partner_name，"
            "原跟进人 original_recorder_name、当前管理人 manager_name 及多条 linked_opportunities。"
            "customer_id 可为空，原跟进次数不转给管理人。返回全文与recording_role_code_snapshot、"
            "recording_team_id_snapshot、record_source；历史缺失保留null/unknown，不按当前目录回填。"
            "除原规则外，当前客户认领者、其有效单一部门祖先在任主管/FDE主管及本公司有效经理可读客户正式记录。"
            "返回can_open_customer/can_open_opportunity/can_export与关联商机can_open，均独立按原规则投影；不授修改、原文件或他人录音权限。")
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
