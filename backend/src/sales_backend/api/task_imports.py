"""Meeting imports are reviewable drafts, not model-authorized task creation."""

from contextlib import asynccontextmanager, contextmanager
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity, get_settings
from sales_backend.api.idempotency import MutationKey
from sales_backend.config import Settings
from sales_backend.contracts.task_imports import (
    TaskImportConfirm,
    TaskImportDraft,
    TaskImportPreview,
    TaskImportReparse,
)
from sales_backend.contracts.types import UUIDString
from sales_backend.db import Database
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.domain.tasks import TaskConflict, TaskNotFound
from sales_backend.repositories.task_notification_origin import task_group_notification_scope
from sales_backend.services.idempotency import execute_mutation
from sales_backend.contracts.task_alignment import legacy_safe_task_payload
from sales_backend.services.task_imports import TaskImportService

router = APIRouter(prefix="/api/v1/task-imports", tags=["Meeting task imports"])


@asynccontextmanager
async def task_dispatch_notification_origin(connection, identity: RequestIdentity):
    """Only persisted administrator console sessions may announce weekly dispatches.

    Neither request fields nor headers select this origin. On failure the outer
    business transaction rolls back the LOCAL setting and all newly made tasks;
    do not run a restore query in an aborted transaction and mask its exception.
    """
    origin = (
        "admin_weekly_dispatch"
        if identity.client_channel == "web" and identity.auth_method == "password"
        and identity.actor.role.value == "administrator" else ""
    )
    async with task_group_notification_scope(connection, origin):
        yield


@contextmanager
def errors():
    try:
        yield
    except TaskNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except (TaskConflict, VersionConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post(
    "/preview",
    description="保存周会任务草稿；AI仅整理，失败明确回退逐行规则。原文与会议日期相同的本人批次返回既有草稿或派发记录。",
)
async def preview_task_import(
    body: TaskImportPreview,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
    idempotency_key: MutationKey = None,
):
    with errors():
        return await TaskImportService(database, settings).preview(identity.actor, body)


@router.post(
    "/{batch_id}/reparse",
    description="显式重新AI整理本人未派发且未人工编辑的草稿；版本冲突或已编辑返回409。原文/会议日期不变，AI失败保留原稿，不创建任务。",
)
async def reparse_task_import(
    batch_id: UUIDString,
    body: TaskImportReparse,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
):
    with errors():
        return await TaskImportService(database, settings).reparse(identity.actor, batch_id, body)


@router.get("", description="本人草稿及当前任务读取权限内的已派发批次；所有完成率与人数只按可见任务计算。")
async def list_task_imports(
    status: Literal["draft", "confirmed"] | None = None,
    page_size: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
):
    with errors():
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await TaskImportService(database, settings).list(
                connection, identity.actor, status=status, limit=page_size, offset=offset
            )


@router.get("/{batch_id}", description="草稿原文只对创建者可见；其他人只取得其task.read范围内已派发任务及汇总。")
async def task_import_detail(
    batch_id: UUIDString,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
):
    with errors():
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await TaskImportService(database, settings).detail(connection, identity.actor, batch_id)


@router.patch("/{batch_id}/draft", description="只更新本人未派发批次的编辑清单；保留原文和初始解析。版本冲突返回409。")
async def save_task_import_draft(
    batch_id: UUIDString,
    body: TaskImportDraft,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
    idempotency_key: MutationKey = None,
):
    with errors():
        async with database.transaction(identity.actor) as connection:
            service = TaskImportService(database, settings)

            async def save():
                await service.save_draft(connection, identity.actor, batch_id, body)
                return {"batch_id": batch_id}

            receipt = await execute_mutation(
                connection, identity.actor, idempotency_key, f"task-imports.draft:{batch_id}", body.model_dump(), save
            )
            return await service.detail(connection, identity.actor, receipt["batch_id"])


@router.post(
    "/{batch_id}/confirm",
    description="确认选中事项，逐条检查当前创建/派发/关联权限并整批原子创建。必须Idempotency-Key；同批同确认内容回放，已派发后不同内容返回409。",
)
async def confirm_task_import(
    batch_id: UUIDString,
    body: TaskImportConfirm,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
    idempotency_key: MutationKey = None,
):
    if idempotency_key is None:
        raise HTTPException(422, "确认派发必须提供Idempotency-Key，响应不确定时使用同一标识重试")
    with errors():
        async with database.transaction(identity.actor) as connection:
            service = TaskImportService(database, settings)

            async def confirm():
                async with task_dispatch_notification_origin(connection, identity):
                    await service.confirm(connection, identity.actor, batch_id, body)
                return {"batch_id": batch_id}

            receipt = await execute_mutation(
                connection,
                identity.actor,
                idempotency_key,
                f"task-imports.confirm:{batch_id}",
                legacy_safe_task_payload(body.model_dump()),
                confirm,
            )
            return await service.detail(connection, identity.actor, receipt["batch_id"])
