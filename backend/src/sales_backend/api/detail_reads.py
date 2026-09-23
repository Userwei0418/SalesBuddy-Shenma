"""Interactive detail contracts: bounded overviews and independent history pages."""

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.contracts.detail_reads import (
    ActualQuarters,
    ContactSummary,
    CustomerHeader,
    CustomerOpportunityPage,
    CustomerOverview,
    HistoryPage,
    OpportunityDetailHeader,
    OpportunityDetailOverview,
    TimelineEvent,
    VisitHistoryPage,
)
from sales_backend.db import Database
from sales_backend.repositories.customer_assets import today
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.repositories.detail_history import DetailHistoryRepository
from sales_backend.repositories.detail_overviews import DetailOverviewRepository, opportunity_risk_summaries
from sales_backend.repositories.opportunities import OpportunityRepository

router = APIRouter(prefix="/api/v1", tags=["Detail read models"])


async def require_customer(connection, customer_id):
    if not await CustomerRepository().exists(connection, customer_id):
        raise HTTPException(404, "客户不存在或无权查看")


async def require_opportunity(connection, actor, opportunity_id, customer_id=None):
    row = await DetailOverviewRepository().opportunity_reference(
        connection, actor, opportunity_id, customer_id=customer_id,
    )
    if row is None:
        raise HTTPException(404, "商机不存在或无权查看")
    return dict(row)


@router.get("/customers/{customer_id}/overview", response_model=CustomerOverview)
async def customer_overview(
    customer_id: UUID, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        item = await DetailOverviewRepository().customer(connection, str(customer_id))
    if item is None:
        raise HTTPException(404, "客户不存在或无权查看")
    return item


@router.get("/customers/{customer_id}/header", response_model=CustomerHeader)
async def customer_header(
    customer_id: UUID, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        item = await DetailOverviewRepository().customer_header(connection, str(customer_id))
    if item is None:
        raise HTTPException(404, "客户不存在或无权查看")
    return item


@router.get("/opportunities/{opportunity_id}/header", response_model=OpportunityDetailHeader)
async def opportunity_header(
    opportunity_id: UUID, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        item = await DetailOverviewRepository().opportunity_header(connection, identity.actor, str(opportunity_id))
    if item is None:
        raise HTTPException(404, "商机不存在或无权查看")
    return item


@router.get("/customers/{customer_id}/opportunities/{opportunity_id}/header", response_model=OpportunityDetailHeader)
async def customer_opportunity_header(
    customer_id: UUID, opportunity_id: UUID,
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        await require_customer(connection, customer_id)
        # Preserve the customer-panorama RLS scope, distinct from personal opportunity detail.
        item = await DetailOverviewRepository().opportunity_header(
            connection, None, str(opportunity_id), customer_id=str(customer_id),
        )
    if item is None:
        raise HTTPException(404, "商机不存在或无权查看")
    return item


@router.get("/opportunities/{opportunity_id}/overview", response_model=OpportunityDetailOverview,
            description="商机有界概览。associated_partners 为普通关联伙伴，与转售伙伴及签单方式独立；"
            "historical_period_actuals 保留历史季度原金额、单位和税口径，不计入逐笔实绩。")
async def opportunity_overview(
    opportunity_id: UUID, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        item = await DetailOverviewRepository().opportunity(connection, identity.actor, str(opportunity_id))
    if item is None:
        raise HTTPException(404, "商机不存在或无权查看")
    return item


@router.get("/customers/{customer_id}/opportunities", response_model=CustomerOpportunityPage)
async def customer_opportunities(
    customer_id: UUID, q: str | None = Query(None, max_length=100),
    page_size: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        await require_customer(connection, customer_id)
        # Legacy customer detail used actor=None: RLS defines the customer panorama.
        # This is deliberately different from the actor's personal project list.
        result = await OpportunityRepository().page(
            connection, None, customer_id=customer_id, query=q.strip() if q else None,
            limit=page_size, offset=offset, include_closed=True,
        )
        risks = await opportunity_risk_summaries(connection, [row["id"] for row in result["items"]])
        for row in result["items"]:
            row["risk_summary"] = risks.get(row["id"], {})
    return result


@router.get(
    "/customers/{customer_id}/opportunities/{opportunity_id}/overview", response_model=OpportunityDetailOverview,
)
async def customer_opportunity_overview(
    customer_id: UUID, opportunity_id: UUID,
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        await require_customer(connection, customer_id)
        # Customer panorama has always used RLS without the personal-list owner filter.
        # Both IDs participate in the database predicate; knowing an ID grants no access.
        item = await DetailOverviewRepository().opportunity(
            connection, None, str(opportunity_id), customer_id=str(customer_id),
        )
    if item is None:
        raise HTTPException(404, "商机不存在或无权查看")
    return item


@router.get("/visits", response_model=VisitHistoryPage, response_model_exclude_unset=True)
async def visit_history(
    customer_id: UUID, opportunity_id: UUID | None = None,
    page_size: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    cursor: str | None = Query(None, max_length=1024),
    sort: Literal["created_desc"] | None = Query(
        None, description="created_desc按系统录入时间倒序；缺省保持拜访日期排序"),
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        await require_customer(connection, customer_id)
        if opportunity_id:
            await require_opportunity(connection, None, opportunity_id, customer_id)
        try:
            return await DetailHistoryRepository().visits(
                connection, customer_id, opportunity_id=opportunity_id, limit=page_size,
                offset=offset, cursor=cursor, sort=sort,
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error


@router.get("/customers/{customer_id}/contacts", response_model=HistoryPage[ContactSummary])
async def contact_history(
    customer_id: UUID, page_size: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        await require_customer(connection, customer_id)
        return await DetailHistoryRepository().contacts(connection, customer_id, limit=page_size, offset=offset)


@router.get("/opportunities/{opportunity_id}/timeline", response_model=HistoryPage[TimelineEvent])
async def opportunity_timeline(
    opportunity_id: UUID, customer_id: UUID | None = None,
    page_size: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        if customer_id:
            await require_customer(connection, customer_id)
            await require_opportunity(connection, None, opportunity_id, customer_id)
        else:
            await require_opportunity(connection, identity.actor, opportunity_id)
        return await DetailHistoryRepository().timeline(connection, opportunity_id, limit=page_size, offset=offset)


@router.get("/customer-assets/quarters", response_model=ActualQuarters)
async def actual_quarters(
    customer_id: UUID, opportunity_id: UUID, as_of: date | None = None,
    team_id: UUID | None = None, owner_id: UUID | None = None,
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
) -> dict:
    day = as_of or today()
    if day > today():
        raise HTTPException(422, "统计截止日期不能晚于今天")
    async with database.transaction(identity.actor, readonly=True) as connection:
        await require_customer(connection, customer_id)
        await require_opportunity(connection, None, opportunity_id, customer_id)
        return await DetailHistoryRepository().actual_quarters(
            connection, customer_id, opportunity_id, as_of=day, team_id=team_id, owner_id=owner_id,
        )
