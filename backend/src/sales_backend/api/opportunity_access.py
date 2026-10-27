from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.contracts.opportunity_access import (
    AccessCandidates,
    CompanyFollowUpCustomers,
    CompanyOpportunityFollowUpOptions,
    CompanyOpportunityFollowUpReference,
    OpportunityAccessResponse,
    OpportunityCollaboratorsResult,
    OpportunityCollaboratorsUpdate,
    OpportunityFollowUpOptions,
)
from sales_backend.db import Database, json_value
from sales_backend.domain.opportunity_access import OpportunityAccessError
from sales_backend.services.opportunity_access import collaboration_retired, read_access, read_options, set_collaborators
from sales_backend.services.opportunity_transfer import transfer_candidates
from sales_backend.error_contract import error_response

router = APIRouter(prefix="/api/v1/opportunities", tags=["Opportunity access"])
RequiredKey = Annotated[
    UUID, Header(alias="Idempotency-Key", description="必填；响应未知时使用原键重试，同键异内容409。")
]
RETIRED_RESPONSE = {410: {
    "description": "OPPORTUNITY_COLLABORATION_RETIRED：商机协作名单设置已停用，不查询候选或修改名单。",
    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PublicApiError"}}},
}}


async def response(call):
    try:
        return await call()
    except OpportunityAccessError as exc:
        return error_response(status_code=exc.status, code=exc.code, detail=exc.detail)
    except asyncpg.InsufficientPrivilegeError:
        return error_response(status_code=403, code="OPPORTUNITY_ACCESS_DENIED", detail="商机不存在或当前动作无权")
    except (asyncpg.SerializationError, asyncpg.DeadlockDetectedError):
        return error_response(status_code=409, code="VERSION_CONFLICT", detail="商机已变化，请刷新后重新确认")
    except asyncpg.InvalidParameterValueError:
        return error_response(status_code=422, code="INVALID_ACCESS_TARGET", detail="请选择有效且不重复的协作成员")


@router.get("/company-follow-up-customers", response_model=CompanyFollowUpCustomers,
            description="跟进专用公司客户选择，只返回标识与名称；不改变客户模块或商机模块读取权限。")
async def company_follow_up_customers(
    q: str = Query("", max_length=100),
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=2_147_483_647),
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
):
    async def read():
        async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
            return json_value(await connection.fetchval(
                "SELECT security.company_opportunity_follow_up_customers($1,$2,$3)",
                q.strip(), limit, offset,
            ))
    return await response(read)


@router.get("/company-follow-up-options", response_model=CompanyOpportunityFollowUpOptions,
            description="公司商机跟进选择目录，仅返回商机及客户标识、名称；不授予详情或编辑权限。")
async def company_follow_up_options(
    q: str = Query("", max_length=100), customer_id: UUID | None = None,
    order: Literal["name", "recent_follow_up"] = Query(
        "recent_follow_up", description="recent_follow_up 按本人最近正式跟进日期倒序，无记录后置。"
    ),
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=2_147_483_647),
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
):
    async def read():
        async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
            return json_value(await connection.fetchval(
                "SELECT security.company_opportunity_follow_up_options($1,$2::uuid,$3,$4,$5)",
                q.strip(), customer_id, limit, offset, order,
            ))
    return await response(read)


@router.get("/{opportunity_id}/follow-up-access", response_model=CompanyOpportunityFollowUpReference,
            description="复核同公司商机及其客户关联，只提供新增本人跟进所需的最小引用。")
async def company_follow_up_access(
    opportunity_id: UUID,
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
):
    async def read():
        async with database.transaction(identity.actor, readonly=True) as connection:
            reference = json_value(await connection.fetchval(
                "SELECT security.company_opportunity_follow_up_reference($1::uuid)", opportunity_id,
            ))
            if not reference:
                raise OpportunityAccessError(
                    "NOT_FOUND", "商机不存在或当前不可跟进，请重新选择", status=404,
                )
            return reference
    return await response(read)


@router.get("/follow-up-options", response_model=OpportunityFollowUpOptions)
async def follow_up_options(
    q: str = Query("", max_length=100), customer_id: UUID | None = None,
    order: Literal["name", "recent_follow_up"] = Query(
        "name", description="recent_follow_up 按可见正式跟进日期倒序，包含协作商机；无记录后置。"
    ),
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=2_147_483_647),
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
):
    async def read():
        async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
            return await read_options(connection, query=q.strip(), customer_id=customer_id,
                                      limit=limit, offset=offset, order=order)
    return await response(read)


@router.get("/{opportunity_id}/access", response_model=OpportunityAccessResponse,
            description="最小对象引用与当前动作能力；转交能力由来源授权、状态及历史归属检查决定。")
async def access(
    opportunity_id: UUID,
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
):
    async def read():
        async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
            return await read_access(connection, opportunity_id)
    return await response(read)


@router.get("/{opportunity_id}/access-candidates", response_model=AccessCandidates, responses=RETIRED_RESPONSE,
            description="主责转交候选。兼容保留 purpose=collaborate，但该用途已弃用并返回410；transfer不变。")
async def access_candidates(
    opportunity_id: UUID, purpose: Literal["collaborate", "transfer"], q: str = Query("", max_length=100),
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=2_147_483_647),
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
):
    async def read():
        if purpose == "collaborate":
            collaboration_retired()
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await transfer_candidates(
                connection, opportunity_id, query=q.strip(), limit=limit, offset=offset)
    return await response(read)


@router.put("/{opportunity_id}/collaborators", response_model=OpportunityCollaboratorsResult,
            deprecated=True, responses=RETIRED_RESPONSE,
            description="已停用的兼容入口，返回410，不修改历史协作名单、授权或业务数据。")
async def update_collaborators(
    opportunity_id: UUID, body: OpportunityCollaboratorsUpdate, idempotency_key: RequiredKey,
    identity: RequestIdentity = Depends(get_identity),
):
    async def write():
        return await set_collaborators(None, identity.actor, opportunity_id, body, idempotency_key)
    return await response(write)
