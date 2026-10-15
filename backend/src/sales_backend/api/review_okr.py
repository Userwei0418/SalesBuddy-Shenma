"""Additive O/KR endpoints; existing targets, task and Mini requests are untouched."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.contracts.review_okr import (
    KeyResultCreate,
    KeyResultUpdate,
    ManualProgress,
    ObjectiveCreate,
    ObjectiveUpdate,
    OkrMention,
)
from sales_backend.db import Database
from sales_backend.repositories.review_okr import ReviewOkrRepository
from sales_backend.services.operations import management_write
from sales_backend.services.review_okr import (
    authorize_key_result_mutation,
    authorize_objective_mutation,
    read_key_results,
    save_key_result,
    save_objective,
)

router = APIRouter(prefix="/api/v1/review", tags=["Review OKR"])
repository = ReviewOkrRepository()


@router.get("/objectives")
async def objectives(
    anchor_date: date | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await repository.objectives(connection, period=anchor_date, limit=limit, offset=offset)


@router.get("/objectives/{entity_id}")
async def objective(
    entity_id: UUID, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        result = await repository.objectives(connection, objective_id=entity_id)
        if not result["items"]:
            raise HTTPException(404, "记录不存在")
        return result["items"][0]


@router.post("/objectives")
async def create_objective(
    body: ObjectiveCreate,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        "review.objective.create",
        body.model_dump(),
        lambda c: save_objective(c, body),
        authorize=lambda c: authorize_objective_mutation(c, body=body),
    )


@router.patch("/objectives/{entity_id}")
async def update_objective(
    entity_id: UUID,
    body: ObjectiveUpdate,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"review.objective.update:{entity_id}",
        body.model_dump(),
        lambda c: save_objective(c, body, entity_id),
        authorize=lambda c: authorize_objective_mutation(c, entity_id=entity_id),
    )


@router.get("/key-results")
async def key_results(
    objective_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        return await read_key_results(connection, identity.actor, objective_id=objective_id, limit=limit, offset=offset)


@router.get("/key-results/{entity_id}")
async def key_result(
    entity_id: UUID, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)
):
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        result = await read_key_results(connection, identity.actor, key_result_id=entity_id)
        if not result["items"]:
            raise HTTPException(404, "记录不存在")
        return result["items"][0]


@router.post("/key-results")
async def create_key_result(
    body: KeyResultCreate,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        "review.key_result.create",
        body.model_dump(),
        lambda c: save_key_result(c, body),
        authorize=lambda c: authorize_key_result_mutation(c, objective_id=body.objective_id),
    )


@router.patch("/key-results/{entity_id}")
async def update_key_result(
    entity_id: UUID,
    body: KeyResultUpdate,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"review.key_result.update:{entity_id}",
        body.model_dump(),
        lambda c: save_key_result(c, body, entity_id),
        authorize=lambda c: authorize_key_result_mutation(c, entity_id=entity_id),
    )


@router.post("/key-results/{entity_id}/progress")
async def progress(
    entity_id: UUID,
    body: ManualProgress,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"review.key_result.progress:{entity_id}",
        body.model_dump(),
        lambda c: repository.progress(c, entity_id, body),
        authorize=lambda c: authorize_key_result_mutation(c, entity_id=entity_id, confirm_progress=True),
    )


@router.post("/objectives/{entity_id}/mentions")
async def mention_objective(
    entity_id: UUID,
    body: OkrMention,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"review.objective.mention:{entity_id}",
        body.model_dump(),
        lambda c: repository.mention(c, "objective", entity_id, body),
        authorize=lambda c: authorize_objective_mutation(c, entity_id=entity_id),
    )


@router.post("/key-results/{entity_id}/mentions")
async def mention_key_result(
    entity_id: UUID,
    body: OkrMention,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"review.key_result.mention:{entity_id}",
        body.model_dump(),
        lambda c: repository.mention(c, "key_result", entity_id, body),
        authorize=lambda c: authorize_key_result_mutation(c, entity_id=entity_id),
    )
