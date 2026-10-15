"""Follow-up list reads for the new Web; existing recording routes stay unchanged."""

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.contracts.visit_range import VisitRangePage
from sales_backend.db import Database
from sales_backend.repositories.visit_range import VisitRangeRepository

router = APIRouter(prefix="/api/v1/visits", tags=["Follow-up range"])


@router.get(
    "/range",
    response_model=VisitRangePage,
    description=(
        "当前公司 visit.read/RLS 范围内的跨客户跟进摘要，仅读 confirmed/archived 状态。"
        "日期、人员、团队等条件只缩小结果；不变更演示数据可见性、角色、录入和推送链路。"
    ),
)
async def list_visits(
    scope: Literal["all", "mine"] = "all",
    q: str = Query("", max_length=100),
    recorder_ids: list[UUID] = Query(default=[], max_length=100),
    team_id: UUID | None = None,
    customer_id: UUID | None = None,
    opportunity_id: UUID | None = None,
    date_basis: Literal["interaction", "created"] = "interaction",
    date_from: date | None = None,
    date_to: date | None = None,
    page_size: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    if date_to == date.max:
        raise HTTPException(422, "结束日期超出支持范围")
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await VisitRangeRepository().page(
            connection,
            scope=scope,
            q=q.strip(),
            recorder_ids=[str(value) for value in recorder_ids],
            team_id=str(team_id) if team_id else None,
            customer_id=str(customer_id) if customer_id else None,
            opportunity_id=str(opportunity_id) if opportunity_id else None,
            date_basis=date_basis,
            date_from=date_from,
            date_to=date_to,
            limit=page_size,
            offset=offset,
        )
