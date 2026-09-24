from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.models import TaskBatchCreate, TaskCreate, TaskEventCreate
from sales_backend.contracts.task_links import TaskLinkPage
from sales_backend.contracts.types import UUIDString
from sales_backend.db import Database
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.domain.tasks import TaskConflict, TaskForbidden, TaskNotFound
from sales_backend.repositories.task_browse import TaskBrowseRepository
from sales_backend.repositories.task_links import TaskLinkRepository
from sales_backend.repositories.task_targets import TaskTargetRepository
from sales_backend.repositories.tasks import TaskRepository
from sales_backend.services.idempotency import execute_mutation
from sales_backend.services.tasks import TaskService

router = APIRouter(prefix="/api/v1/tasks", tags=["Tasks"])


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


@router.get("/recipients")
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
        rows = await TaskTargetRepository().recipients(
            connection, identity.actor, q=q.strip(), limit=page_size+1, offset=offset)
    more = len(rows) > page_size
    return {"items": rows[:page_size], "has_more": more, "next_offset": offset+page_size if more else None}



@router.get("")
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
    fde_view = None
    if identity.actor.role.value in {"fde", "fde_lead"} and not customer_id and not opportunity_id:
        fde_view = view
    async with database.transaction(identity.actor, readonly=True) as connection:
        if tab is not None:
            return await TaskBrowseRepository().page(connection, limit=page_size, offset=offset,
                status=task_status, customer_id=customer_id, opportunity_id=opportunity_id,
                fde_view=fde_view, inbox=inbox, tab=tab, overview=overview, order=order,
                member_id=member_id, team=team, member=member, completed_year=completed_year,
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
    view = "self" if identity.actor.role.value in {"fde", "fde_lead"} else None
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
                body.model_dump(),
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
                "tasks.create_batch", body.model_dump(), lambda: create(connection))
    except TaskNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except TaskForbidden as exc:
        raise HTTPException(403, str(exc)) from exc
    except TaskConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{task_id}")
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


@router.post("/{task_id}/events")
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
                body.model_dump(),
                lambda: TaskService().apply_event(
                    connection,
                    actor=identity.actor,
                    task_id=task_id,
                    event_type=body.event_type,
                    note=body.note,
                    expected_version=body.version_no,
                    assignee_account_code=body.assignee_account_code,
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
