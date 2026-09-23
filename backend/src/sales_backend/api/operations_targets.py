from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.contracts.targets import (
    TargetBatchSave,
    TargetDecision,
    TargetDepartment,
    TargetKind,
    TargetPeriod,
    TargetSave,
    TargetScope,
)
from sales_backend.db import Database
from sales_backend.repositories.customer_assets import today
from sales_backend.repositories.targets import TargetRepository
from sales_backend.services.operations import management_write
from sales_backend.services.targets import read_targets, save_target, save_target_batch

router = APIRouter(prefix="/api/v1/console", tags=["Operations targets"])
repository = TargetRepository()


@router.get("/targets")
async def targets(
    period_type: TargetPeriod = "year",
    anchor_date: date | None = None,
    scope: TargetScope | None = None,
    user_id: UUID | None = None,
    team_id: UUID | None = None,
    department_code: TargetDepartment = "sales",
    kind: TargetKind | None = None,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        try:
            return await read_targets(
                connection,
                identity.actor,
                period_type=period_type,
                anchor_date=anchor_date or today(),
                scope=scope,
                user_id=user_id,
                team_id=team_id,
                department_code=department_code,
                kind=kind,
                limit=limit,
                offset=offset,
                q=q,
            )
        except (ValueError, PermissionError) as exc:
            raise HTTPException(422 if isinstance(exc, ValueError) else 403, str(exc)) from exc


@router.post("/targets")
async def save(
    body: TargetSave,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        "console.target.save",
        body.model_dump(),
        lambda c: save_target(c, identity.actor, body),
    )


@router.get("/target-requests")
async def requests(
    status: Literal["pending", "approved", "rejected", "cancelled"] | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await repository.requests(connection, identity.actor, status=status, limit=limit, offset=offset)


@router.post("/targets/batch")
async def save_batch(
    body: TargetBatchSave,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database, identity.actor, idempotency_key, "console.target.batch.save", body.model_dump(),
        lambda c: save_target_batch(c, identity.actor, body),
    )


@router.get("/target-batches")
async def batches(
    status: Literal["pending", "approved", "rejected", "cancelled"] | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await repository.batches(connection, identity.actor, status=status, limit=limit, offset=offset)


@router.post("/target-batches/{request_id}/decision")
async def decide_batch(
    request_id: UUID,
    body: TargetDecision,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database, identity.actor, idempotency_key, f"target.batch.review:{request_id}", body.model_dump(),
        lambda c: repository.decide_batch(c, request_id, body),
    )


@router.post("/target-requests/{request_id}/decision")
async def decide(
    request_id: UUID,
    body: TargetDecision,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"target.review:{request_id}",
        body.model_dump(),
        lambda c: repository.decide(c, request_id, body),
    )


@router.get("/targets/{target_id}/history")
async def history(
    target_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        try:
            return await repository.history(connection, identity.actor, target_id, limit=limit, offset=offset)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
