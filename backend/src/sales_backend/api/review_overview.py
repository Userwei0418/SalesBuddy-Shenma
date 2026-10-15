"""Review facts do not call the model or modify report generation bindings."""

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.contracts.review_multi import ReviewOverviewV2, ReviewPageV2, ReviewSelectionV2
from sales_backend.contracts.review_overview import (
    ActivityCode,
    PoolFilter,
    PoolSort,
    ReviewOverview,
    ReviewPage,
    ReviewSelection,
)
from sales_backend.contracts.work_report_base import PeriodKind
from sales_backend.db import Database
from sales_backend.domain.work_report import WorkReportError
from sales_backend.services import review_multi as multi_service
from sales_backend.services import review_overview as service

router = APIRouter(prefix="/api/v1/review", tags=["Review"])


def selection(
    scope: Literal["self", "member", "team"] = "self",
    member_id: UUID | None = None,
    team_id: UUID | None = None,
    anchor_date: date | None = None,
    period_type: PeriodKind = "day",
    member_ids: list[UUID] | None = Query(None),
    team_ids: list[UUID] | None = Query(None),
    expected_scope_revision: str | None = None,
):
    try:
        if member_ids is not None or team_ids is not None or (scope == "self" and expected_scope_revision is not None):
            return ReviewSelectionV2(
                scope=scope, member_id=member_id, team_id=team_id, member_ids=member_ids, team_ids=team_ids,
                expected_scope_revision=expected_scope_revision, anchor_date=anchor_date, period_type=period_type,
            )
        if expected_scope_revision is not None:
            raise WorkReportError("WORK_REPORT_INVALID_INPUT", "范围修订校验请使用member_ids或team_ids数组选择",
                                  status=422)
        return ReviewSelection(
            scope=scope, member_id=member_id, team_id=team_id, anchor_date=anchor_date, period_type=period_type
        )
    except ValidationError:
        raise WorkReportError("WORK_REPORT_INVALID_INPUT", "周期、范围或对象不匹配", status=422) from None


@router.get("/overview", response_model=ReviewOverview | ReviewOverviewV2)
async def overview(
    selection: ReviewSelection | ReviewSelectionV2 = Depends(selection),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        reader = multi_service if isinstance(selection, ReviewSelectionV2) else service
        return await reader.read_overview(connection, identity.actor, selection)


@router.get("/activities", response_model=ReviewPage | ReviewPageV2)
async def activities(
    activity: ActivityCode,
    selection: ReviewSelection | ReviewSelectionV2 = Depends(selection),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        reader = multi_service if isinstance(selection, ReviewSelectionV2) else service
        return await reader.read_activities(
            connection, identity.actor, selection, activity=activity, limit=limit, offset=offset
        )


@router.get("/opportunities", response_model=ReviewPage | ReviewPageV2)
async def opportunities(
    selection: ReviewSelection | ReviewSelectionV2 = Depends(selection),
    filter: PoolFilter = "all",
    q: str | None = Query(None, max_length=100),
    sort: PoolSort = "last_follow_up_desc",
    metric_code: Literal["forecast_collection", "collection_ppl", "forecast_recognized"] | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        reader = multi_service if isinstance(selection, ReviewSelectionV2) else service
        return await reader.read_opportunities(
            connection,
            identity.actor,
            selection,
            filter=filter,
            q=q,
            sort=sort,
            metric_code=metric_code,
            limit=limit,
            offset=offset,
        )
