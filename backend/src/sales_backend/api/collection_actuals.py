from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.contracts.collection_actuals import CollectionActualResult
from sales_backend.db import Database
from sales_backend.repositories.collection_actuals import read_collection_actual

router = APIRouter(tags=["Opportunity collection actuals"])


@router.get(
    "/api/v1/opportunities/{opportunity_id}/collection-actuals",
    response_model=CollectionActualResult,
    description="当前商机季度累计实际回款及并发revision。只读商机范围投影，不授予财务管理权。保存沿用商机修改接口collection_actuals，立即正式生效。",
)
async def collection_actual(
    opportunity_id: UUID,
    year: int = Query(ge=2000, le=2100),
    quarter: int = Query(ge=1, le=4),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        try:
            return await read_collection_actual(connection, opportunity_id, year, quarter)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
