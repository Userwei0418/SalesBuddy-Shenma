from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, HttpUrl

from sales_backend.api.dependencies import RequestIdentity, get_database
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.api.exports import csv_response
from sales_backend.contracts.models import OpportunityCreate
from sales_backend.db import Database
from sales_backend.repositories.operations_opportunities import OperationsOpportunityRepository
from sales_backend.services.operations import management_write
from sales_backend.services.opportunities import save_opportunity

router = APIRouter(prefix="/api/v1/console/opportunities", tags=["Operations opportunities"])
repository = OperationsOpportunityRepository()


class OpportunitySave(OpportunityCreate):
    customer_id: UUID
    owner_user_ref_id: UUID | None = None


class QuoteReference(BaseModel):
    reference_no: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    url: HttpUrl | None = None
    amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)


@router.get("")
@router.get("/export")
async def opportunities(
    request: Request,
    q: str | None = Query(None, max_length=100),
    customer: UUID | None = None,
    owner: UUID | None = None,
    status: Literal["open", "won", "lost", "cancelled"] | None = None,
    probability: Literal[10, 30, 50, 70, 90, 100] | None = None,
    close_from: date | None = None,
    close_to: date | None = None,
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
            customer=customer,
            owner=owner,
            status=status,
            probability=probability,
            close_from=close_from,
            close_to=close_to,
            limit=10001 if exporting else limit,
            offset=0 if exporting else offset,
        )
    if exporting:
        return csv_response(
            result["items"],
            [
                ("name", "商机名称"),
                ("customer_name", "关联客户"),
                ("amount", "ACV元"),
                ("probability", "概率百分比"),
                ("stage_code", "阶段"),
                ("status", "状态"),
                ("owner_name", "负责人"),
                ("expected_close_date", "预计关单日期"),
                ("product_line", "产品线"),
                ("partner_name", "所属伙伴"),
            ],
            "opportunities",
        )
    return result


@router.get("/{opportunity_id}")
async def detail(
    opportunity_id: UUID,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        try:
            return await repository.detail(connection, opportunity_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc


@router.post("", status_code=201)
async def save(
    body: OpportunitySave,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    if body.action == "update" and body.version_no is None:
        raise HTTPException(422, "请刷新商机后再修改")
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"opportunity.save:{body.customer_id}",
        body.model_dump(),
        lambda c: save_opportunity(c, identity.actor, customer_id=str(body.customer_id), data=body.model_dump()),
    )


@router.post("/{opportunity_id}/quotes", status_code=201)
async def quote(
    opportunity_id: UUID,
    body: QuoteReference,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"opportunity.quote:{opportunity_id}",
        body.model_dump(),
        lambda c: repository.attach_quote(c, identity.actor, opportunity_id, body.model_dump()),
    )
