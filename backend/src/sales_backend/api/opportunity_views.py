from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.contracts.opportunity_views import (
    OpportunityViewCreate,
    OpportunityViewDelete,
    OpportunityViewDeleted,
    OpportunityViewItem,
    OpportunityViewList,
    OpportunityViewOptions,
    OpportunityViewUpdate,
)
from sales_backend.db import Database
from sales_backend.repositories.opportunity_views import OpportunityViewRepository
from sales_backend.services.operations import management_write

router = APIRouter(prefix="/api/v1/opportunity-views", tags=["Opportunity views"])
repository = OpportunityViewRepository()


def require_key(key):
    if key is None:
        raise HTTPException(422, "保存视图必须携带 Idempotency-Key，重试请沿用同一标识")


@router.get(
    "/options",
    response_model=OpportunityViewOptions,
    description="返回神码当前已有字段与安全默认列；本批仅个人视图，不授予业务数据权限。",
)
async def options(identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await repository.options(connection)


@router.get(
    "",
    response_model=OpportunityViewList,
    description="仅返回本人在当前公司的视图。配置仅保存列、筛选、排序及分组；读取商机仍由原商机接口校验当前授权。",
)
async def list_views(
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await repository.list(connection, limit=limit, offset=offset)


@router.get("/{view_id}", response_model=OpportunityViewItem)
async def detail(
    view_id: UUID, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        try:
            return await repository.get(connection, view_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc


@router.post(
    "",
    status_code=201,
    response_model=OpportunityViewItem,
    description="创建个人私有视图；最多 100 个。必须携带 Idempotency-Key。",
)
async def create(
    body: OpportunityViewCreate,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    require_key(idempotency_key)
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        "opportunity_view.create",
        body.model_dump(),
        lambda c: repository.create(c, identity.actor, body),
    )


@router.put(
    "/{view_id}",
    response_model=OpportunityViewItem,
    description="仅本人可修改；省略 config 仅重命名，保留原配置。version_no 冲突返回 409。必须携带 Idempotency-Key。",
)
async def update(
    view_id: UUID,
    body: OpportunityViewUpdate,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    require_key(idempotency_key)
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"opportunity_view.update:{view_id}",
        body.model_dump(),
        lambda c: repository.update(c, identity.actor, view_id, body),
    )


@router.delete(
    "/{view_id}",
    response_model=OpportunityViewDeleted,
    description="只有创建人可删除。版本冲突返回 409，必须携带 Idempotency-Key；删除保留审计记录。",
)
async def delete(
    view_id: UUID,
    body: OpportunityViewDelete,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    require_key(idempotency_key)
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"opportunity_view.delete:{view_id}",
        body.model_dump(),
        lambda c: repository.update(c, identity.actor, view_id, body, delete=True),
    )
