from fastapi import APIRouter, Depends, HTTPException

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.models import RunAccepted
from sales_backend.contracts.visit_flow import QualityRequest, StructureRequest
from sales_backend.db import Database
from sales_backend.services.idempotency import execute_mutation
from sales_backend.services.visit_flow import prepare_visit_run

router = APIRouter(prefix="/api/v1/visit-flow", tags=["Visit flow"])


async def enqueue(body, stage, identity, database, key):
    try:
        async with database.transaction(identity.actor) as connection:

            async def create():
                return await prepare_visit_run(connection, identity.actor, body, stage)

            return await execute_mutation(
                connection, identity.actor, key, "visit-flow." + stage, body.model_dump(mode="json"), create
            )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, LookupError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/structure", response_model=RunAccepted, status_code=202)
async def structure(
    body: StructureRequest,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
):
    return await enqueue(body, "structure", identity, database, idempotency_key)


@router.post("/quality", response_model=RunAccepted, status_code=202)
async def quality(
    body: QualityRequest,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
):
    return await enqueue(body, "quality", identity, database, idempotency_key)
