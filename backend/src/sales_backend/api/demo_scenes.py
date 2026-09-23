from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.contracts.demo_scenes import DemoSceneBatch, DemoSceneCreate, DemoSceneDelete, DemoSceneUpdate
from sales_backend.db import Database
from sales_backend.repositories.demo_scenes import DemoSceneRepository
from sales_backend.services.operations import management_write

router = APIRouter(prefix="/api/v1", tags=["Demo scenes"])
repository = DemoSceneRepository()


@router.get("/opportunities/{opportunity_id}/demo-scenes")
async def scenes(
    opportunity_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        try:
            return await repository.list(connection, opportunity_id, limit=limit, offset=offset)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc


@router.post("/opportunities/{opportunity_id}/demo-scenes", status_code=201)
async def create(
    opportunity_id: UUID,
    body: DemoSceneCreate | DemoSceneBatch,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    action = repository.create_batch if isinstance(body, DemoSceneBatch) else repository.create
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"demo.create:{opportunity_id}",
        body.model_dump(),
        lambda c: action(c, identity.actor, opportunity_id, body),
    )


@router.get("/demo-scenes/{scene_id}")
async def detail(
    scene_id: UUID, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        try:
            return await repository.get(connection, scene_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc


@router.get("/demo-scenes/{scene_id}/history")
async def history(
    scene_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        # Validate visibility before the narrow security-definer history projection.
        await detail_check(connection, scene_id)
        return await repository.history(connection, scene_id, limit=limit, offset=offset)


async def detail_check(connection, scene_id):
    try:
        await repository.get(connection, scene_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.patch("/demo-scenes/{scene_id}")
async def update(
    scene_id: UUID,
    body: DemoSceneUpdate,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"demo.update:{scene_id}",
        body.model_dump(),
        lambda c: repository.update(c, scene_id, body),
    )


@router.delete("/demo-scenes/{scene_id}")
async def delete(
    scene_id: UUID,
    body: DemoSceneDelete,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"demo.delete:{scene_id}",
        body.model_dump(),
        lambda c: repository.update(c, scene_id, body, delete=True),
    )
