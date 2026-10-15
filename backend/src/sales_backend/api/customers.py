from __future__ import annotations

from sales_backend.domain.customer_identity import BusinessIdentityFilter, legacy_compatible_payload
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from sales_backend.contracts.customer_portfolio import CustomerPortfolioPage, CustomerPortfolioQuery
from sales_backend.repositories.customer_portfolio import CustomerPortfolioRepository

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey, require_material_key
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.api.models import (
    CustomerAssign,
    CustomerCreate,
    CustomerUpdate,
    OpportunityCreate,
    PageResponse,
)
from sales_backend.contracts.customer_directory import (
    ClaimDirectoryStatus, CustomerDirectoryOptions, CustomerDirectoryPage,
)
from sales_backend.contracts.customer_creation import (
    CustomerCreationRequestBody, CustomerCreationRequestPage,
)
from sales_backend.contracts.customer_request_history import CustomerRequestHistoryPage, CustomerRequestHistoryQuery
from sales_backend.contracts.types import UUIDString
from sales_backend.contracts.opportunity_save_receipt import OpportunitySaveReceipt
from sales_backend.db import Database
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.repositories.customer_members import CustomerMemberRepository
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.customer_creation_requests import CustomerCreationRequestRepository
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.repositories.opportunities import NAME_CONFLICT_MESSAGE, opportunity_name_available
from sales_backend.services.customer_claims import claim_customer
from sales_backend.services.idempotency import execute_mutation
from sales_backend.services.operations import management_write
from sales_backend.services.opportunities import save_opportunity
from sales_backend.services.opportunity_save_receipt import cancel_opportunity_save_request, read_opportunity_save_receipt
from sales_backend.services.visit_access import require_opportunity_mutation_scope

router = APIRouter(prefix="/api/v1/customers", tags=["Customers"])
creation_requests = CustomerCreationRequestRepository()


@router.get('/portfolio', response_model=CustomerPortfolioPage,
            description='同一授权客户集合的独立活跃/商机/赢单分层、精确分页和完整地图；认领目录总数使用独立目录权限。')
async def customer_portfolio(
    query: CustomerPortfolioQuery = Query(),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await CustomerPortfolioRepository().read(connection, identity.actor, query)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/creation-requests", status_code=201)
async def submit_customer_creation_request(
    body: CustomerCreationRequestBody,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
) -> dict:
    payload = body.model_dump(mode="json")
    return await management_write(
        database, identity.actor, idempotency_key, "customers.creation_request",
        legacy_compatible_payload(body, mode="json"), lambda connection: creation_requests.submit(connection, identity.actor, payload),
    )


@router.get("/creation-requests/mine", response_model=CustomerCreationRequestPage)
async def my_customer_creation_requests(
    request_status: Literal["all", "pending", "approved", "rejected", "cancelled"] | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await creation_requests.mine(connection, identity.actor, status=request_status, q=q, limit=limit, offset=offset)


@router.get("/requests/history", response_model=CustomerRequestHistoryPage,
            description="本人或完整授权成员范围的建档/认领申请摘要。team_id/member_id按当前有效任职取交集；不含联系人。summary 不受status或分页影响。")
async def customer_request_history(
    query: CustomerRequestHistoryQuery = Query(),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await creation_requests.history(connection, identity.actor, query=query)


@router.get("/claim-pool", response_model=CustomerDirectoryPage)
async def customer_claim_pool(
    business_identity: BusinessIdentityFilter = "all",
    q: str | None = Query(default=None, max_length=100),
    page_size: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=2_147_483_647),
    industry: str | None = Query(default=None, max_length=100),
    claim_status: ClaimDirectoryStatus | None = Query(default=None),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await CustomerMemberRepository().claim_pool_page(
            connection, query=q, limit=page_size, offset=offset, industry=industry or None, claim_status=claim_status, business_identity=business_identity
        )


@router.get("/claim-pool/options", response_model=CustomerDirectoryOptions)
async def customer_claim_pool_options(
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await CustomerMemberRepository().claim_pool_options(connection)


@router.get("/{customer_id}/reference")
async def reference(
    customer_id: UUID,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    from sales_backend.repositories.opportunity_mutations import customer_for_opportunity

    async with database.transaction(identity.actor, readonly=True) as connection:
        row = await customer_for_opportunity(connection, str(customer_id))
        if not row:
            raise HTTPException(404, "客户不存在")
        return row


@router.get("", response_model=PageResponse)
async def list_customers(
    business_identity: BusinessIdentityFilter = "all",
    scope: Literal["mine", "department", "company"] = "mine",
    q: str | None = Query(default=None, max_length=100),
    level: str | None = Query(default=None, max_length=20),
    unassigned: bool | None = Query(default=None),
    page_size: int = Query(default=50, ge=1, le=100),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> PageResponse:
    async with database.transaction(identity.actor, readonly=True) as connection:
        if scope in {"department", "company"} and (await AuthorizationRepository().effective(connection)).allows("customer.claim_directory"):
            items = (await CustomerMemberRepository().claim_pool_page(connection, query=q, limit=page_size,
                business_identity=business_identity))["items"]
            return PageResponse(items=items)
        items = await CustomerRepository().list(
            connection, query=q, level=level, unassigned=unassigned, limit=page_size, business_identity=business_identity
        )
    return PageResponse(items=items)


@router.post(
    "/{customer_id}/claims", status_code=201,
    description="依据当前账号的客户认领权限及数据范围申请；审批通过后生效。",
)
async def claim_existing_customer(
    customer_id: UUID,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
) -> dict:
    try:
        async with database.transaction(identity.actor) as connection:
            return await execute_mutation(
                connection,
                identity.actor,
                idempotency_key,
                f"customers.claim:{customer_id}",
                {},
                lambda: claim_customer(connection, identity.actor, str(customer_id)),
            )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/{customer_id}")
async def customer_detail(
    customer_id: UUIDString,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        item = await CustomerRepository().detail(connection, customer_id=customer_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "CUSTOMER_NOT_FOUND")
    return item


@router.post("", status_code=201)
async def create_customer(
    body: CustomerCreate,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
) -> dict:
    try:
        async with database.transaction(identity.actor) as connection:
            return await execute_mutation(
                connection,
                identity.actor,
                idempotency_key,
                "customers.create",
                legacy_compatible_payload(body),
                lambda: CustomerMutationRepository().create(connection, identity.actor, data=body.model_dump()),
            )
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "客户已存在，请核对后再建档") from exc


@router.patch("/{customer_id}")
async def update_customer(
    customer_id: UUIDString,
    body: CustomerUpdate,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
) -> dict:
    try:
        async with database.transaction(identity.actor) as connection:
            payload = body.model_dump(exclude_unset=True)
            expected_version = payload.pop("version_no", None)
            return await execute_mutation(
                connection,
                identity.actor,
                idempotency_key,
                f"customers.update:{customer_id}",
                legacy_compatible_payload(body),
                lambda: CustomerMutationRepository().update(
                    connection,
                    identity.actor,
                    customer_id=customer_id,
                    data=payload,
                    expected_version=expected_version,
                ),
            )
    except FileExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except VersionConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "客户已存在，请核对后再修改") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.post("/{customer_id}/assignments", status_code=201)
async def assign_customer(
    customer_id: UUIDString,
    body: CustomerAssign,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
) -> dict:
    raise HTTPException(410, "客户归属须经认领审批；交接请联系运营释放后重新认领")


@router.get("/{customer_id}/opportunities/check-name")
async def check_opportunity_name(
    customer_id: UUID,
    name: str = Query(min_length=1, max_length=200),
    exclude_id: UUID | None = Query(default=None),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        if not await CustomerRepository().reference(connection, customer_id):
            raise HTTPException(404, "CUSTOMER_NOT_FOUND")
        try:
            available = await opportunity_name_available(connection, customer_id, name, exclude_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"available": available, "message": "" if available else NAME_CONFLICT_MESSAGE}


@router.get(
    "/{customer_id}/opportunities/save-requests/{request_key}",
    response_model=OpportunitySaveReceipt,
    description=(
        "只读查询当前账号在该客户下原 Idempotency-Key 的商机保存回执，复核当前客户和商机读取权限。"
        "committed 仅返回完整保存标识；unresolved 不代表未保存，原请求可能仍在执行。"
        "cancelled 表示原 key 已被永久终止，迟到的原提交不能再写入。"
        "查询失败或权限撤回同样不能证明原提交失败，客户端须保留原 key，不得换 key 重建。"
        "仅适用于 POST /customers/{customer_id}/opportunities；无自动过期，无业务写入。"
    ),
    responses={404: {"description": "当前客户或结果商机不可读；不证明原提交失败"}},
)
async def opportunity_save_receipt(
    customer_id: UUID,
    request_key: UUID,
    response: Response,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    response.headers["Cache-Control"] = "no-store"
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await read_opportunity_save_receipt(connection, identity.actor, customer_id, request_key)
    except PermissionError as exc:
        raise HTTPException(404, "客户或商机不存在或不在当前权限范围内", headers={"Cache-Control": "no-store"}) from exc
    except asyncpg.InsufficientPrivilegeError as exc:
        raise HTTPException(403, "当前账号无权查询保存结果，请保留原提交标识",
                            headers={"Cache-Control": "no-store"}) from exc


@router.post(
    "/{customer_id}/opportunities/save-requests/{request_key}/cancel",
    response_model=OpportunitySaveReceipt,
    description=(
        "终止当前账号该客户下的原商机提交，使用与原保存相同的事务锁。"
        "已提交返回 committed 与原回执，不撤销业务；尚未提交则永久封住原 key 并返回 cancelled。"
        "后续同 key 商机 POST 被 409 OPPORTUNITY_SAVE_CANCELLED 拒绝。"
        "超时、权限拒绝或 unresolved 均不能证明终止成功，须保留 key 继续查询。"
        "无需原业务内容，不删除回执，不自动过期。"
    ),
    responses={404: {"description": "当前客户或已提交商机不可读；不证明终止成功"}},
)
async def cancel_customer_opportunity_save(
    customer_id: UUID,
    request_key: UUID,
    response: Response,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    response.headers["Cache-Control"] = "no-store"
    try:
        async with database.transaction(identity.actor) as connection:
            return await cancel_opportunity_save_request(connection, identity.actor, customer_id, request_key)
    except PermissionError as exc:
        raise HTTPException(404, "客户或商机不存在或不在当前权限范围内", headers={"Cache-Control": "no-store"}) from exc
    except asyncpg.InsufficientPrivilegeError as exc:
        raise HTTPException(403, "当前账号无权终止提交，请保留原提交标识并查询结果",
                            headers={"Cache-Control": "no-store"}) from exc


@router.post("/{customer_id}/opportunities", status_code=201)
async def create_customer_opportunity(
    customer_id: UUIDString,
    body: OpportunityCreate,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
) -> dict:
    data = body.model_dump()
    require_material_key(data, idempotency_key)
    if "forecast_schema_version" not in body.model_fields_set:
        data.pop("forecast_schema_version")
    for model, item in zip(body.quarterly_forecasts or [], data.get("quarterly_forecasts") or [], strict=True):
        if "collection_confidence" not in model.model_fields_set:
            item.pop("collection_confidence")
    try:
        async with database.transaction(identity.actor) as connection:
            await require_opportunity_mutation_scope(connection, data, hide_unreadable=True)
            return await execute_mutation(
                connection,
                identity.actor,
                idempotency_key,
                f"opportunities.save:{customer_id}",
                data,
                lambda: save_opportunity(
                    connection,
                    identity.actor,
                    customer_id=customer_id,
                    data=data,
                ),
            )
    except asyncpg.UniqueViolationError as exc:
        if exc.constraint_name == "uq_opportunity_customer_name":
            raise HTTPException(status.HTTP_409_CONFLICT, NAME_CONFLICT_MESSAGE) from exc
        raise
    except (asyncpg.SerializationError, asyncpg.DeadlockDetectedError) as exc:
        raise HTTPException(409, "商机或物料已变化，请刷新后重试") from exc
    except asyncpg.CheckViolationError as exc:
        raise HTTPException(422, exc.message) from exc
    except asyncpg.InsufficientPrivilegeError as exc:
        raise HTTPException(403, "当前账号无权执行此操作") from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except VersionConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
