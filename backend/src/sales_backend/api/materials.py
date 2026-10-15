"""Catalog administration and opportunity material references."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.contracts.materials import (
    MaterialInput, MaterialUpdate, MaterialPreviewInput, ProductLinesUpdate,
    OpportunityMaterialsUpdate, MaterialDraftPreviewInput, MaterialDraftPreviewResult,
)
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.db import Database
from sales_backend.repositories.materials import MaterialRepository, OpportunityMaterialRepository
from sales_backend.repositories.material_draft import MaterialDraftRepository
from sales_backend.services.idempotency import execute_mutation
from sales_backend.services.operations import management_write
from sales_backend.services.opportunities import save_opportunity_materials
from sales_backend.repositories.authorization_checks import require_permission

router = APIRouter(tags=["Materials"])


async def read_catalog(database, identity, **filters):
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await MaterialRepository().catalog(connection, **filters)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/api/v1/materials")
async def materials(q: str | None = Query(None, max_length=100), product_line_id: UUID | None = None,
                    status: Literal["active", "all"] = "active", limit: int = Query(50, ge=1, le=100),
                    offset: int = Query(0, ge=0), identity: RequestIdentity = Depends(get_identity),
                    database: Database = Depends(get_database)):
    return await read_catalog(database, identity, q=q, product_line_id=product_line_id, status=status, limit=limit, offset=offset)


@router.get("/api/v1/console/materials")
async def management_materials(q: str | None = Query(None, max_length=100), product_line_id: UUID | None = None,
                               status: Literal["active", "all"] = "active", limit: int = Query(50, ge=1, le=100),
                               offset: int = Query(0, ge=0), identity: RequestIdentity = Depends(get_management_identity),
                               database: Database = Depends(get_database)):
    return await read_catalog(database, identity, q=q, product_line_id=product_line_id, status=status, limit=limit, offset=offset)


async def read_product_lines(database, identity, status):
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await MaterialRepository().product_lines(connection, status=status)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/api/v1/material-product-lines")
async def product_lines(status: Literal["active", "all"] = "active", identity: RequestIdentity = Depends(get_identity),
                        database: Database = Depends(get_database)):
    return await read_product_lines(database, identity, status)


@router.get("/api/v1/console/material-product-lines")
async def management_product_lines(status: Literal["active", "all"] = "active",
                                   identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database)):
    return await read_product_lines(database, identity, status)


async def catalog_write(database, identity, key, operation, payload, action):
    if key is None:
        raise HTTPException(422, "物料维护必须携带Idempotency-Key")
    async def authorized(connection):
        await MaterialRepository().require_manage(connection)
        return await execute_mutation(connection, identity.actor, key, operation, payload, lambda: action(connection))
    return await management_write(database, identity.actor, None, operation, payload, authorized)


@router.post("/api/v1/console/materials", status_code=201)
async def create_material(body: MaterialInput, idempotency_key: MutationKey = None,
                          identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database)):
    data = body.model_dump()
    return await catalog_write(database, identity, idempotency_key, "material.create", data,
                               lambda c: MaterialRepository().save(c, identity.actor, data))


@router.put("/api/v1/console/materials/{material_id}")
async def update_material(material_id: UUID, body: MaterialUpdate, idempotency_key: MutationKey = None,
                          identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database)):
    data = body.model_dump()
    return await catalog_write(database, identity, idempotency_key, f"material.update:{material_id}", data,
                               lambda c: MaterialRepository().save(c, identity.actor, data, material_id))


@router.put("/api/v1/console/material-product-lines")
async def update_product_lines(body: ProductLinesUpdate, idempotency_key: MutationKey = None,
                               identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database)):
    async def action(connection):
        return await MaterialRepository().save_product_lines(connection, body.model_dump(mode="json"))
    return await catalog_write(database, identity, idempotency_key, "material.product_lines.update", body.model_dump(), action)


@router.get("/api/v1/opportunities/{opportunity_id}/materials")
async def opportunity_materials(opportunity_id: UUID, identity: RequestIdentity = Depends(get_identity),
                                database: Database = Depends(get_database)):
    try:
        async with database.transaction(identity.actor, readonly=True, isolation='repeatable_read') as connection:
            return await OpportunityMaterialRepository().read(connection, opportunity_id)
    except LookupError as exc: raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc: raise HTTPException(403, str(exc)) from exc


@router.post("/api/v1/opportunities/{opportunity_id}/material-forecast-preview")
async def material_forecast_preview(opportunity_id: UUID, body: MaterialPreviewInput,
                                    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    try:
        async with database.transaction(identity.actor, readonly=True, isolation='repeatable_read') as connection:
            return await OpportunityMaterialRepository().preview(connection, opportunity_id, body.year, body.quarter)
    except LookupError as exc: raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc: raise HTTPException(403, str(exc)) from exc


@router.post("/api/v1/material-forecast-preview", response_model=MaterialDraftPreviewResult,
             description="未保存草稿只读试算；不保存商机、预测或实际，不调用模型。金额为元的字符串，版本/期间/序号为整数。")
async def material_draft_forecast_preview(body: MaterialDraftPreviewInput,
                                          identity: RequestIdentity = Depends(get_identity),
                                          database: Database = Depends(get_database)):
    try:
        async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
            return await MaterialDraftRepository().preview(connection, body.model_dump())
    except VersionConflict as exc: raise HTTPException(409, str(exc)) from exc
    except LookupError as exc: raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc: raise HTTPException(403, str(exc)) from exc
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc


@router.put("/api/v1/console/opportunities/{opportunity_id}/materials",
            description="只维护交易关系及物料；保留原ACV、预测、阶段和日期精度，不触发AI或通知。")
async def update_opportunity_materials(opportunity_id: UUID, body: OpportunityMaterialsUpdate,
                                      idempotency_key: MutationKey = None,
                                      identity: RequestIdentity = Depends(get_management_identity),
                                      database: Database = Depends(get_database)):
    if idempotency_key is None:
        raise HTTPException(422, "交易关系与物料维护必须携带Idempotency-Key")
    data = body.model_dump()
    async def authorized(connection):
        await require_permission(connection, "opportunity.update", opportunity_id=opportunity_id)
        return await execute_mutation(connection, identity.actor, idempotency_key,
            f"opportunity.materials:{opportunity_id}", data,
            lambda: save_opportunity_materials(connection, identity.actor, opportunity_id, data))
    return await management_write(database, identity.actor, None,
                                  f"opportunity.materials:{opportunity_id}", data, authorized)
