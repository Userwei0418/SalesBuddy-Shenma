from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.contracts.recognized_actuals import RecognizedActualResult
from sales_backend.db import Database
from sales_backend.domain.recognized_actual_errors import RECOGNIZED_ACTUAL_ERRORS, RecognizedActualError
from sales_backend.repositories.recognized_actuals import read_recognized_actual

router = APIRouter(tags=["Opportunity recognized actuals"])


@router.get(
    "/api/v1/opportunities/{opportunity_id}/recognized-actuals",
    response_model=RecognizedActualResult,
    description="按物料读取季度累计实际不含税确收。销售用原商机编辑权限直接修改；保存沿用商机接口recognized_actuals，立即生效，无新增审批。历史总额不自动分摊。",
)
async def recognized_actual(
    opportunity_id: UUID,
    year: int = Query(ge=2000, le=2100),
    quarter: int = Query(ge=1, le=4),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        try:
            return await read_recognized_actual(connection, opportunity_id, year, quarter)
        except asyncpg.PostgresError as exc:
            if exc.message in RECOGNIZED_ACTUAL_ERRORS and exc.sqlstate in {"22023", "42501"}:
                raise RecognizedActualError(exc.message) from exc
            raise
