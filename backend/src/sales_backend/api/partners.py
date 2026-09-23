from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.db import Database
from sales_backend.repositories.partners import PartnerRepository
from sales_backend.services.operations import management_write

router = APIRouter(prefix="/api/v1", tags=["Partner directory"])
repository = PartnerRepository()


class PartnerSave(BaseModel):
    id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    status: Literal["active", "inactive"] = "active"
    version_no: int | None = Field(None, ge=1)


@router.get("/directory/partners")
async def directory(
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await repository.list(connection, q=q, limit=limit, offset=offset)


@router.get("/console/partners")
async def list_partners(
    q: str | None = Query(None, max_length=100),
    status: Literal["active", "inactive"] | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await repository.list(connection, q=q, status=status, limit=limit, offset=offset)


@router.post("/console/partners")
async def save_partner(
    body: PartnerSave,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        "partner.save",
        body.model_dump(),
        lambda c: repository.save(c, identity.actor, body.model_dump()),
    )
