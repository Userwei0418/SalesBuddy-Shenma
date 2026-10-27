"""Minimal company customer references for recording, never customer profiles."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.contracts.opportunity_access import CompanyCustomerFollowUpReference, CompanyFollowUpCustomers
from sales_backend.db import Database, json_value

router = APIRouter(prefix="/api/v1/customers", tags=["Customer follow-up"])


@router.get("/company-follow-up-options", response_model=CompanyFollowUpCustomers,
            description="同公司有效账号新增本人客户跟进的最小目录，仅返回标识与名称，不授予客户或商机档案读取。")
async def options(
    q: str = Query("", max_length=100),
    order: Literal["name", "recent_follow_up"] = Query(
        "recent_follow_up", description="按本人最近正式跟进日期倒序，无记录后置。"
    ),
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=2_147_483_647),
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        return json_value(await connection.fetchval(
            "SELECT security.company_customer_follow_up_options($1,$2,$3,$4)", q.strip(), limit, offset, order,
        ))


@router.get("/{customer_id}/follow-up-access", response_model=CompanyCustomerFollowUpReference,
            description="复核未删除的同公司客户，仅提供本人新增跟进所需引用，不授予档案读取或编辑。")
async def access(
    customer_id: UUID,
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        reference = json_value(await connection.fetchval(
            "SELECT security.company_customer_follow_up_reference($1::uuid)", customer_id,
        ))
        if not reference:
            raise HTTPException(404, "客户不存在或当前不可跟进，请重新选择")
        return reference
