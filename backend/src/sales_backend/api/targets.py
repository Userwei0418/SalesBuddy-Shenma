from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.contracts.targets import TargetBatchSave, TargetDepartment, TargetPeriod, TargetSave, TargetScope
from sales_backend.db import Database
from sales_backend.repositories.customer_assets import today
from sales_backend.services.operations import management_write
from sales_backend.services.targets import read_targets, save_target, save_target_batch

router = APIRouter(prefix="/api/v1/targets", tags=["Targets"])


@router.get("")
async def targets(
    period_type: TargetPeriod = "year",
    anchor_date: date | None = None,
    scope: TargetScope = "self",
    user_id: UUID | None = None,
    team_id: UUID | None = None,
    department_code: TargetDepartment = "sales",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_identity),
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
                limit=limit,
                offset=offset,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc


@router.post("")
async def save(
    body: TargetSave,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        "target.save",
        body.model_dump(),
        lambda c: save_target(c, identity.actor, body),
    )


@router.post("/batch")
async def save_batch(
    body: TargetBatchSave,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database, identity.actor, idempotency_key, "target.batch.save", body.model_dump(),
        lambda c: save_target_batch(c, identity.actor, body),
    )
