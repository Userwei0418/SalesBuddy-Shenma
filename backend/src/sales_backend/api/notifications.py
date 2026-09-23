from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.models import PageResponse
from sales_backend.contracts.types import UUIDString
from sales_backend.db import Database
from sales_backend.repositories.notifications import NotificationRepository

router = APIRouter(prefix="/api/v1/notifications", tags=["Notifications"])


@router.get(
    "", response_model=PageResponse,
    description=(
        "读取当前收件人的通知。customer_claim 类型的 payload.customer_name 为当前授权客户引用名称，"
        "不可读取时为 null；审批结果及原因仍由原通知 title/body 表示。读取不标记已读。"
    ),
)
async def list_notifications(
    unread_only: bool = False,
    page_size: int = Query(default=50, ge=1, le=100),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> PageResponse:
    async with database.transaction(identity.actor, readonly=True) as connection:
        items = await NotificationRepository().list(connection, unread_only=unread_only, limit=page_size)
    return PageResponse(items=items)


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_notification_read(
    notification_id: UUIDString,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> Response:
    async with database.transaction(identity.actor) as connection:
        await NotificationRepository().mark_read(connection, notification_id=notification_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
