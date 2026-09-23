"""Company directory and selection. Authentication always remains the original account."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from sales_backend.api.dependencies import RequestIdentity, get_database
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.management_dependencies import get_management_identity, get_system_identity
from sales_backend.db import Database
from sales_backend.services.companies import CompanyService
from sales_backend.services.operations import management_write

router = APIRouter(prefix="/api/v1/console/companies", tags=["Company management"])


class CompanySelection(BaseModel):
    company_id: UUID


class CompanyName(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    version_no: int = Field(ge=1)


@router.get("")
async def directory(
    identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database)
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        companies = await CompanyService().directory(connection)
    return {"items": companies, "home_company_id": identity.actor.workspace_id}


@router.post("/select")
async def select_company(
    body: CompanySelection,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    async def select(connection):
        return await CompanyService().select(connection, identity.actor, body.company_id)

    return await management_write(
        database, identity.actor, idempotency_key, "company.select", body.model_dump(mode="json"), select
    )


@router.put("/{company_id}")
async def rename_company(
    company_id: UUID,
    body: CompanyName,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_system_identity),
    database: Database = Depends(get_database),
):
    if str(company_id) != identity.actor.workspace_id:
        raise HTTPException(403, "请先切换至需要管理的公司")

    async def rename(connection):
        return await CompanyService().rename(
            connection, identity.actor, company_id, body.name, body.version_no,
        )

    return await management_write(
        database, identity.actor, idempotency_key, "company.rename", body.model_dump(), rename
    )
