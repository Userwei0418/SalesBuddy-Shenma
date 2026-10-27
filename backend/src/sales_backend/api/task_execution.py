"""Versioned task execution routes. Registration and catalog are wired by Backend."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.contracts.task_execution import COMMAND_MODELS, command_payload
from sales_backend.contracts.task_execution_v3 import COMMAND_MODELS_V3, command_payload_v3
from sales_backend.services.task_execution import (
    TaskExecutionError,
    execute_command,
    execute_command_v3,
    list_candidates,
    read_execution,
    read_history,
)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.post("/{task_id}/execution-v3/{action}", description="四状态任务命令；三版本与幂等键必填，完成是否需验收由服务端真实身份决定。")
async def execution_command_v3(
    task_id: UUID, action: str, body: dict, idempotency_key: MutationKey = None,
    identity=Depends(get_identity), database=Depends(get_database),
):
    model = COMMAND_MODELS_V3.get(action)
    if model is None:
        raise HTTPException(404, {"code": "TASK_EXECUTION_UNKNOWN_ACTION"})
    try:
        command = model.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(422, {"code": "TASK_EXECUTION_INVALID_COMMAND"}) from exc
    try:
        async with database.transaction(identity.actor) as connection:
            return await execute_command_v3(connection, task_id=str(task_id), action=action,
                body=command_payload_v3(command), key=idempotency_key)
    except TaskExecutionError as exc:
        raise HTTPException(exc.status, {"code": exc.code}) from exc


@router.get("/{task_id}/execution", description=(
    "执行详情与最近历史。legacy/V1 返回 initialize_versions 四版本基线，仅用于 initialize_execution；"
    "普通 V1 命令仍使用原三版本。能否接入以 capabilities.initialize_execution 为准。"
))
async def execution_detail(task_id: UUID, identity=Depends(get_identity), database=Depends(get_database)):
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await read_execution(connection, str(task_id))
    except TaskExecutionError as exc:
        raise HTTPException(exc.status, {"code": exc.code}) from exc


@router.post("/{task_id}/execution/{action}")
async def execution_command(
    task_id: UUID,
    action: str,
    body: dict,
    idempotency_key: MutationKey = None,
    identity=Depends(get_identity),
    database=Depends(get_database),
):
    model = COMMAND_MODELS.get(action)
    if model is None:
        raise HTTPException(404, {"code": "TASK_EXECUTION_UNKNOWN_ACTION"})
    try:
        command = model.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(
            422, {"code": "TASK_EXECUTION_INVALID_COMMAND", "errors": exc.errors(include_context=False)}
        ) from exc
    try:
        async with database.transaction(identity.actor) as connection:
            return await execute_command(
                connection,
                task_id=str(task_id),
                action=action,
                body=command_payload(command),
                key=idempotency_key,
            )
    except TaskExecutionError as exc:
        raise HTTPException(exc.status, {"code": exc.code}) from exc


@router.get("/{task_id}/execution/history")
async def execution_history(
    task_id: UUID,
    kind: Literal["events", "submissions"] = "events",
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=50, ge=1, le=100),
    identity=Depends(get_identity),
    database=Depends(get_database),
):
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await read_history(connection, str(task_id), kind, cursor, limit)
    except TaskExecutionError as exc:
        raise HTTPException(exc.status, {"code": exc.code}) from exc


@router.get("/{task_id}/execution/candidates")
async def execution_candidates(
    task_id: UUID,
    purpose: Literal["executor", "reviewer"],
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity=Depends(get_identity),
    database=Depends(get_database),
):
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await list_candidates(connection, str(task_id), purpose, q, limit, offset)
    except TaskExecutionError as exc:
        raise HTTPException(exc.status, {"code": exc.code}) from exc
