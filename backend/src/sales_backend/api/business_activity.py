from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from sales_backend.api.dependencies import RequestIdentity, get_database
from sales_backend.api.exports import csv_response
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.api.operations_reports import window
from sales_backend.db import Database
from sales_backend.domain.business_activity import ACTIONS, CATEGORIES
from sales_backend.repositories.business_activity import BusinessActivityRepository

router = APIRouter(prefix="/api/v1/console/activities", tags=["Business operation records"])
repository = BusinessActivityRepository()
Category = Literal[
    "advice",
    "partner",
    "customer",
    "opportunity",
    "visit",
    "material",
    "task",
    "claim",
    "account",
    "department",
    "data",
    "actual",
    "target",
    "quote",
    "ai_rule",
    "company_rule",
]


@router.get("")
@router.get("/export")
async def activities(
    request: Request,
    bounds=Depends(window),
    actor: UUID | None = None,
    category: Category | None = None,
    outcome: Literal["success", "pending", "failed"] | None = None,
    department: UUID | None = None,
    action: str | None = Query(None, max_length=80),
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    if action is not None and action not in ACTIONS:
        raise HTTPException(422, "操作类型不支持")
    exporting = request.url.path.endswith("/export")
    async with database.transaction(identity.actor, readonly=True) as connection:
        result = await repository.list(
            connection,
            start=bounds[0],
            end=bounds[1],
            actor=actor,
            category=category,
            action=action,
            q=q,
            outcome=outcome,
            department=department,
            limit=10001 if exporting else limit,
            offset=0 if exporting else offset,
        )
    if exporting:
        return csv_response(
            result["items"],
            [
                ("occurred_at", "操作时间"),
                ("actor_name", "操作人"),
                ("actor_department", "当时部门"),
                ("category_label", "业务类型"),
                ("action_label", "业务动作"),
                ("customer_name", "客户"),
                ("object_name", "操作对象"),
                ("summary", "操作摘要"),
                ("result_label", "处理结果"),
                ("evidence_label", "记录依据"),
                ("event_id", "记录标识"),
            ],
            "business-activities",
        )
    return {**result, "categories": CATEGORIES, "actions": ACTIONS}


@router.get("/{event_id}")
async def activity_detail(
    event_id: str,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await repository.detail(connection, event_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
