"""Review dictionary and row targets: additive to the legacy Mini API."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.contracts.review import ReviewMetricCreate, ReviewMetricUpdate, ReviewTargetSave
from sales_backend.contracts.targets import TargetDepartment, TargetPeriod, TargetScope
from sales_backend.db import Database
from sales_backend.repositories.customer_assets import today
from sales_backend.repositories.review import ReviewRepository
from sales_backend.services.operations import management_write
from sales_backend.services.review import (
    authorize_metric_management,
    authorize_target_write,
    save_target_row,
    target_rows,
)

router = APIRouter(prefix="/api/v1/review", tags=["Review"])
repository = ReviewRepository()


@router.get("/metrics")
async def metrics(identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await repository.metrics(connection, identity.actor)


@router.post("/metrics")
async def create_metric(body: ReviewMetricCreate, idempotency_key: MutationKey = None,
                        identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    return await management_write(database, identity.actor, idempotency_key, "review.metric.create", body.model_dump(),
        lambda c: repository.save_metric(c, body.code, body.model_dump(exclude={"code"})),
        authorize=authorize_metric_management)


@router.patch("/metrics/{code}")
async def update_metric(code: str, body: ReviewMetricUpdate, idempotency_key: MutationKey = None,
                        identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    return await management_write(database, identity.actor, idempotency_key, f"review.metric.update:{code}",
        body.model_dump(), lambda c: repository.save_metric(c, code, {"active": body.active}, body.version_no),
        authorize=authorize_metric_management)


@router.get("/targets")
async def targets(period_type: TargetPeriod = "quarter", anchor_date: date | None = None,
                  scope: TargetScope | None = None, user_id: UUID | None = None, team_id: UUID | None = None,
                  department_code: TargetDepartment = "sales", kind: str | None = Query(None, max_length=64),
                  q: str | None = Query(None, max_length=100), limit: int = Query(50, ge=1, le=100),
                  offset: int = Query(0, ge=0), identity: RequestIdentity = Depends(get_identity),
                  database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as connection:
        try:
            return await target_rows(connection, identity.actor, period_type=period_type, anchor_date=anchor_date or today(),
                scope=scope, user_id=user_id, team_id=team_id, department_code=department_code,
                kind=kind, q=q, limit=limit, offset=offset)
        except (ValueError, PermissionError) as exc:
            raise HTTPException(403 if isinstance(exc, PermissionError) else 422, str(exc)) from exc


@router.get("/targets/options")
async def target_options(identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await repository.target_options(connection, identity.actor)


@router.post("/targets")
async def save_target(body: ReviewTargetSave, idempotency_key: MutationKey = None,
                      identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    return await management_write(database, identity.actor, idempotency_key, "review.target.save", body.model_dump(),
        lambda c: save_target_row(c, identity.actor, body),
        authorize=lambda c: authorize_target_write(c, identity.actor, body))
