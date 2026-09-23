from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from sales_backend.api.dependencies import RequestIdentity, get_database
from sales_backend.api.exports import csv_response
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.contracts.customer_profile import CustomerProfileUpdate
from sales_backend.contracts.detail_reads import OperationsCustomerHistory, OperationsCustomerOverview
from sales_backend.db import Database
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.repositories.operations_customers import OperationsCustomerRepository
from sales_backend.services.operations import management_write

router = APIRouter(prefix="/api/v1/console", tags=["Operations customers"])
repository = OperationsCustomerRepository()


class ClaimDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    reason: str = Field(default="", max_length=2000)


class OwnershipRelease(BaseModel):
    version_no: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class LegacyResolution(OwnershipRelease):
    owner_user_ref_id: UUID


@router.get("/summary")
async def summary(
    identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database)
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await repository.summary(connection)


@router.get("/customers")
@router.get("/customers/export")
async def customers(
    request: Request,
    q: str | None = Query(None, max_length=100),
    industry: str | None = Query(None, max_length=100),
    level: str | None = Query(None, max_length=20),
    state: Literal["unclaimed", "claimed", "legacy_review"] | None = None,
    owner: UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    exporting = request.url.path.endswith("/export")
    async with database.transaction(identity.actor, readonly=True) as connection:
        result = await repository.list(
            connection,
            q=q,
            industry=industry,
            level=level,
            state=state,
            owner=owner,
            limit=10001 if exporting else limit,
            offset=0 if exporting else offset,
        )
    if exporting:
        return csv_response(
            result["items"],
            [
                ("name", "客户名称"),
                ("industry_code", "行业"),
                ("contact_name", "联系人"),
                ("contact_phone", "联系电话"),
                ("contact_email", "邮箱"),
                ("level_code", "客户优先级"),
                ("owner_name", "认领人"),
                ("ownership_state", "认领状态"),
                ("lifecycle_status", "客户状态"),
                ("team_name", "部门"),
                ("company_reference", "公司建档编号"),
                ("created_at", "创建时间"),
            ],
            "customers",
        )
    return result


@router.get("/customers/{customer_id}")
async def customer(
    customer_id: UUID,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        item = await CustomerRepository().detail(connection, customer_id=str(customer_id))
        if not item:
            raise HTTPException(404, "客户不存在")
        return {
            **item,
            "ownership": await repository.ownership(connection, customer_id),
            "claims": await repository.claims(connection, customer_id=customer_id),
            "ownership_history": await repository.events(connection, customer_id),
        }


@router.get("/customers/{customer_id}/overview", response_model=OperationsCustomerOverview)
async def customer_overview(
    customer_id: UUID,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        item = await repository.profile(connection, customer_id)
        if not item:
            raise HTTPException(404, "客户不存在")
        return {
            **(await CustomerRepository().base(connection, customer_id=str(customer_id)) or {}),
            **item, "ownership": await repository.ownership(connection, customer_id),
        }


@router.patch("/customers/{customer_id}")
async def update_profile(
    customer_id: UUID,
    body: CustomerProfileUpdate,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    payload = body.model_dump(exclude_unset=True)
    version = payload.pop("version_no")
    return await management_write(
        database, identity.actor, idempotency_key, f"console.customer.profile:{customer_id}",
        body.model_dump(mode="json", exclude_unset=True),
        lambda c: CustomerMutationRepository().update(
            c, identity.actor, customer_id=str(customer_id), data=payload,
            expected_version=version, management_profile=True,
        ),
    )


@router.get("/customers/{customer_id}/history", response_model=OperationsCustomerHistory)
async def customer_history(
    customer_id: UUID, kind: Literal["claims", "ownership"],
    page_size: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        if not await CustomerRepository().exists(connection, customer_id):
            raise HTTPException(404, "客户不存在")
        return await repository.customer_history(connection, customer_id, kind=kind, limit=page_size, offset=offset)


@router.get("/claims")
async def claims(
    status: Literal["pending", "approved", "rejected", "cancelled"] | None = None,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await repository.claims(connection, status=status, q=q, limit=limit, offset=offset)


@router.post("/claims/{request_id}/decision")
async def review(
    request_id: UUID,
    body: ClaimDecision,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"claim.review:{request_id}",
        body.model_dump(),
        lambda c: repository.review(c, request_id, body.decision, body.reason.strip(), actor=identity.actor),
    )


@router.post("/customers/{customer_id}/release")
async def release(
    customer_id: UUID,
    body: OwnershipRelease,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"customer.release:{customer_id}",
        body.model_dump(),
        lambda c: repository.release(c, customer_id, body.version_no, body.reason.strip()),
    )


@router.post("/customers/{customer_id}/resolve-owner")
async def resolve_owner(
    customer_id: UUID,
    body: LegacyResolution,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"customer.resolve-owner:{customer_id}",
        body.model_dump(),
        lambda c: repository.resolve_legacy(
            c, customer_id, body.version_no, body.owner_user_ref_id, body.reason.strip(), actor=identity.actor,
        ),
    )
