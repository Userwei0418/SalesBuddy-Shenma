"""Explicit management preview and confirmed fill of empty formal fields."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sales_backend.api.dependencies import RequestIdentity, get_database
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.contracts.opportunity_initialization import InitializationPreview, InitializationApply
from sales_backend.db import Database
from sales_backend.services import opportunity_initialization as service
from sales_backend.services.operations import management_write
from sales_backend.repositories.opportunity_initialization import authorize
from sales_backend.domain.opportunity_field_catalog import field_catalog, PARTICIPANT_ROLES

router = APIRouter(prefix="/api/v1/operations/opportunity-fields", tags=["Opportunity initialization"])


@router.get("/options")
async def options(
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            await authorize(connection, [])
            return {"fields": field_catalog(), "participant_roles": PARTICIPANT_ROLES}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/preview")
async def preview(
    body: InitializationPreview,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    try:
        async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
            return await service.preview(connection, identity.actor, body.opportunity_ids)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/apply")
async def apply(
    body: InitializationApply,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key", description="必填UUID；同次确认与响应不确定后的重试使用同一标识，内容变化须重新确认。")],
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    if idempotency_key is None:
        raise HTTPException(422, "初始化确认必须携带Idempotency-Key")
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        "opportunity_fields.initialize",
        body.model_dump(mode="json"),
        lambda c: service.apply(c, identity.actor, body),
        authorize=lambda c: service.authorize_apply(c, body),
    )
