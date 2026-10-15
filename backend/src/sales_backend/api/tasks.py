from __future__ import annotations

from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.models import TaskBatchCreate, TaskCreate, TaskEventCreate
from sales_backend.contracts.task_alignment import TaskAlignmentUpdate, legacy_safe_task_payload
from sales_backend.contracts.task_links import TaskLinkPage
from sales_backend.contracts.task_management import TaskManagementOwnerPage, TaskManagementPage, management_filters
from sales_backend.contracts.types import UUIDString
from sales_backend.db import Database, DatabaseBusy
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.domain.tasks import TaskConflict, TaskForbidden, TaskNotFound
from sales_backend.repositories.task_browse import TaskBrowseRepository
from sales_backend.repositories.task_links import TaskLinkRepository
from sales_backend.repositories.task_management import TaskManagementRepository
from sales_backend.repositories.task_targets import TaskTargetRepository
from sales_backend.repositories.tasks import TaskRepository
from sales_backend.services.idempotency import execute_mutation
from sales_backend.services.tasks import TaskService

router = APIRouter(prefix="/api/v1/tasks", tags=["Tasks"])


@router.get('/management', response_model=TaskManagementPage, description='任务管理只读列表。all 是 task.read 当前授权范围，mine 限本人发起/负责/候选；不扩大权限。日期按北京时间闭区间与创建/正式派发至截止区间相交，undated 明示无截止任务。summary 是全部筛选结果，不是当前页。schedule_start_at 不是实际开工时间。')
async def management_tasks(
    filters: dict = Depends(management_filters),
    page_size: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=2_147_483_647),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        return await TaskManagementRepository().page(connection, limit=page_size, offset=offset, **filters)


@router.get('/management/owners', response_model=TaskManagementOwnerPage, description='按同一筛选下可见任务生成负责人目录，包含历史停用负责人；不需要创建任务或组织读取权限，不暴露范围外人员。unassigned_count 是当前无负责人任务数，q 仅搜索姓名/账号。')
async def management_task_owners(
    filters: dict = Depends(management_filters),
    q: str = Query('', max_length=100),
    page_size: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=2_147_483_647),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        return await TaskManagementRepository().owners(connection, query=q.strip(), limit=page_size, offset=offset, **filters)


@router.get("/customers", response_model=TaskLinkPage)
async def task_customer_choices(
    q: str = Query(default="", max_length=100),
    page_size: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=2_147_483_647),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    """Customers with at least one opportunity the creator can associate."""
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await TaskLinkRepository().customers(
            connection, identity.actor, query=q.strip(), limit=page_size, offset=offset,
        )


@router.get("/opportunities", response_model=TaskLinkPage)
async def task_opportunity_choices(
    customer_id: UUIDString,
    opportunity_id: UUIDString | None = None,
    q: str = Query(default="", max_length=100),
    page_size: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=2_147_483_647),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    """Existing opportunities only; this directory does not grant access."""
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await TaskLinkRepository().opportunities(
            connection, identity.actor, customer_id=customer_id, opportunity_id=opportunity_id,
            query=q.strip(), limit=page_size, offset=offset,
        )


@router.get("/recipients", description="创建权限内的公司接收人目录；items.team_ids 和 team 用于按有效业务任职及组织归属展示、筛选；team_id 仍为真实业务任职或 null，不用于从组织归属授予权限；role_name 为公司岗位显示名（role 代码不变），teams 为当前有效团队（含空团队），defaults.team_id 为当前人的有效主团队或 null。选人不授予业务访问权。")
async def task_recipients(
    q: str = Query(default="", max_length=100),
    page_size: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    from sales_backend.services.capabilities import require_capability
    async with database.transaction(identity.actor, readonly=True) as connection:
        try:
            await require_capability(connection, identity.actor, "task.create")
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        return await TaskTargetRepository().recipient_page(
            connection, identity.actor, q=q.strip(), limit=page_size, offset=offset)



@router.get("/{task_id}/reassignment-options", description="仅经该任务协调权限与状态校验后返回接收人目录；不依赖新建任务或组织读取权限。items.team_ids 和 team 用于按有效业务任职及组织归属展示、筛选；team_id 仍为真实业务任职或 null，不用于从组织归属授予权限；role_name 为公司岗位显示名（role 代码不变），teams 保留空团队，defaults.team_id 为当前人的有效主团队或 null。")
async def task_reassignment_options(
    task_id: UUIDString,
    q: str = Query(default="", max_length=100),
    page_size: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=2_147_483_647),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    from sales_backend.services.task_coordination import reassignment_options

    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await reassignment_options(connection, actor=identity.actor, task_id=task_id,
                                              query=q.strip(), limit=page_size, offset=offset)
    except TaskNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except TaskForbidden as exc:
        raise HTTPException(403, str(exc)) from exc
    except TaskConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("", description=("每个任务返回 can_cancel，按当前权限及状态计算。tab 模式下 member_id/team_id "
    "按同一正式 owner 任职的成员/团队 UUID 筛选，旧 team 参数仍为团队名称。view 按当前 task.read 范围收窄，"
    "view=self 为本人任务，view=team 在 task.read 授权内读取；member_id/team_id 仅进一步筛选。summary 是分页前完整基础筛选范围；filtered_total "
    "再应用 tab/overview，overdue_count 为当前未完成且已过截止时间，today_pending_count/today_completed_count "
    "按北京时间当天截止/完成统计。未传 tab 的旧列表行为不变，新 team_id 必须配合 tab。"))
async def list_tasks(
    task_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(pending_confirm|pending_execution|in_progress|pending_review|completed|deferred|cancelled)$",
    ),
    page_size: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    inbox: bool = False,
    customer_id: UUIDString | None = Query(default=None),
    opportunity_id: UUIDString | None = Query(default=None),
    view: str = Query(default="self", pattern="^(self|team)$"),
    tab: Literal["pending", "completed", "rejected", "all"] | None = None,
    overview: Literal["today_pending", "today_completed", "all_pending"] | None = None,
    order: Literal["today_first", "due_desc", "due_asc", "created_desc"] = "today_first",
    member_id: UUIDString | None = None,
    team_id: UUIDString | None = None,
    team: str | None = Query(None, max_length=100),
    member: str | None = Query(None, max_length=100),
    completed_year: int | None = Query(None, ge=2000, le=2100),
    completed_quarters: list[int] = Query([], max_length=4),
    opportunity_only: bool = False,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    if any(q not in {1, 2, 3, 4} for q in completed_quarters) or (completed_quarters and not completed_year):
        raise HTTPException(422, "请选择有效的完成年份和季度")
    if team_id and tab is None:
        raise HTTPException(422, "team_id 筛选需要指定 tab")
    fde_view = view if not customer_id and not opportunity_id else None
    async with database.transaction(identity.actor, readonly=True) as connection:
        if tab is not None:
            return await TaskBrowseRepository().page(connection, limit=page_size, offset=offset,
                status=task_status, customer_id=customer_id, opportunity_id=opportunity_id,
                fde_view=fde_view, inbox=inbox, tab=tab, overview=overview, order=order,
                member_id=member_id, team_id=team_id, team=team, member=member, completed_year=completed_year,
                completed_quarters=completed_quarters, opportunity_only=opportunity_only)
        items = await TaskRepository().list(
            connection, status=task_status, customer_id=customer_id, limit=page_size + 1, offset=offset, inbox=inbox,
            opportunity_id=opportunity_id, fde_view=fde_view
        )
    has_more = len(items) > page_size
    return {"items": items[:page_size], "has_more": has_more, "next_offset": offset + page_size if has_more else None}


class TaskOverviewMetrics(BaseModel):
    today_completed: int
    today_pending: int
    all_pending: int


class TaskCardState(BaseModel):
    id: str
    status: str
    completion_note: str | None = None
    attributes: dict
    handover_required: bool
    last_event_type: str | None = None
    last_event_note: str | None = None


class TaskOverviewResponse(BaseModel):
    metrics: TaskOverviewMetrics
    items: list[TaskCardState]


@router.get("/overview", response_model=TaskOverviewResponse)
async def task_overview(
    task_ids: list[UUID] = Query(default=[], max_length=100),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    # Summary is the full personal inbox; card states are bounded, RLS-checked IDs.
    view = "self"
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await TaskRepository().overview(connection, task_ids=task_ids, fde_view=view)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_task(
    body: TaskCreate,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
) -> dict:
    try:
        async with database.transaction(identity.actor) as connection:
            return await execute_mutation(
                connection,
                identity.actor,
                idempotency_key,
                "tasks.create",
                legacy_safe_task_payload(body.model_dump()),
                lambda: TaskService().create(
                    connection,
                    actor=identity.actor,
                    description=body.description,
                    assignee_account_code=body.assignee_account_code,
                    target_position=body.target_position,
                    due_at=body.due_at,
                    priority_code=body.priority_code,
                    association_kind=body.association_kind,
                    customer_id=body.customer_id,
                    opportunity_id=str(body.opportunity_id) if body.opportunity_id else None,
                    alignment=body.alignment,
                    alignment_contract_version=body.alignment_contract_version,
                ),
            )
    except TaskNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except TaskForbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except TaskConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/batch", status_code=status.HTTP_201_CREATED,
             description="为每位指定负责人创建独立待办。整批在同一事务提交；任一校验失败则全部回滚。支持 Idempotency-Key 重放。")
async def create_task_batch(
    body: TaskBatchCreate,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
) -> dict:
    async def create(connection):
        items = []
        for task in body.tasks:
            values = task.model_dump()
            if values["opportunity_id"]:
                values["opportunity_id"] = str(values["opportunity_id"])
            items.append(await TaskService().create(connection, actor=identity.actor, **values))
        return {"items": items}

    try:
        async with database.transaction(identity.actor) as connection:
            return await execute_mutation(connection, identity.actor, idempotency_key,
                "tasks.create_batch", legacy_safe_task_payload(body.model_dump()), lambda: create(connection))
    except TaskNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except TaskForbidden as exc:
        raise HTTPException(403, str(exc)) from exc
    except TaskConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/alignment-capabilities")
async def alignment_capabilities(
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    from sales_backend.repositories.task_alignment import TaskAlignmentRepository

    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            available = await TaskAlignmentRepository.supports_v2(connection)
    except (asyncpg.PostgresError, OSError, TimeoutError, DatabaseBusy) as exc:
        raise HTTPException(503, "任务关联能力暂时无法核实，请稍后重试") from exc
    return {"contract": "task_alignment", "supported_versions": [1, 2] if available else [1],
            "preferred_version": 2 if available else 1,
            "v2_requires": "target_or_key_result_or_other_reason" if available else None,
            "target_reference_type": "sales_target", "other_reason_max_length": 2000}


@router.get("/{task_id}", description="返回任务及事件历史；can_cancel 布尔值同时校验 task.cancel、任务协调资格和当前状态。已完成、已取消、待验收任务不可取消。")
async def task_detail(
    task_id: UUIDString,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        item = await TaskRepository().detail(connection, task_id=task_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "TASK_NOT_FOUND")
    return item


@router.patch("/{task_id}/alignment", description="按任务当前版本替换指标/KR关系；仅任务协调者可改。贡献为计划声明，不增加财务业绩。旧请求缺省alignment不创建任何关系。")
async def update_task_alignment(
    task_id: UUIDString,
    body: TaskAlignmentUpdate,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
) -> dict:
    from sales_backend.services.task_alignment import save_task_alignment

    async def save(connection):
        await save_task_alignment(connection, task_id, body.alignment, body.version_no,
                                  contract_version=body.alignment_contract_version)
        return await TaskRepository().detail(connection, task_id=task_id)

    try:
        async with database.transaction(identity.actor) as connection:
            # A committed receipt does not preserve permissions. Recheck the
            # current object scope before both execution and receipt replay;
            # version and lifecycle checks stay inside the mutation itself.
            access = await connection.fetchrow(
                "SELECT security.authorization_task('task.read',t.id) AS readable,"
                "security.fde_can_coordinate_task(t.id) AS editable FROM workflow.task t "
                "WHERE t.id=$1::uuid AND t.workspace_id=$2::uuid AND t.deleted_at IS NULL",
                task_id, identity.actor.workspace_id,
            )
            if access is None:
                raise TaskNotFound("TASK_NOT_FOUND")
            if not access["readable"] or not access["editable"]:
                raise TaskForbidden("当前账号无权修改此任务关联")
            return await execute_mutation(connection, identity.actor, idempotency_key,
                f"tasks.alignment:{task_id}", legacy_safe_task_payload(body.model_dump()), lambda: save(connection))
    except TaskNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except TaskForbidden as exc:
        raise HTTPException(403, str(exc)) from exc
    except TaskConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{task_id}/events", description=(
    "按当前版本执行任务事件。显式转交保留人工指定的新负责人；事件和通知 payload 包含 "
    "actor_name、previous_owners（user_id、role、team_id、name）及 new_owner"
    "（user_id、display_name、account_code、role_code、team_id）的操作时快照。"
    "同一负责人重复转交返回409，不改变任务。"
))
async def create_task_event(
    task_id: UUIDString,
    body: TaskEventCreate,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
) -> dict:
    try:
        async with database.transaction(identity.actor) as connection:
            return await execute_mutation(
                connection,
                identity.actor,
                idempotency_key,
                f"tasks.events:{task_id}",
                legacy_safe_task_payload(body.model_dump()),
                lambda: TaskService().apply_event(
                    connection,
                    actor=identity.actor,
                    task_id=task_id,
                    event_type=body.event_type,
                    note=body.note,
                    expected_version=body.version_no,
                    assignee_account_code=body.assignee_account_code,
                    actual_contribution=body.actual_contribution,
                ),
            )
    except TaskNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except TaskForbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except TaskConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except VersionConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
