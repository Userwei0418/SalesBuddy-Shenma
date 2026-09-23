from __future__ import annotations

from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.api.models import (
    CustomerAssign,
    CustomerCreate,
    CustomerUpdate,
    OpportunityCreate,
    PageResponse,
)
from sales_backend.contracts.customer_directory import CustomerDirectoryPage
from sales_backend.contracts.types import UUIDString
from sales_backend.db import Database
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.domain.policy import (
    AgentModeForbidden,
    assert_opportunity_create_allowed,
)
from sales_backend.repositories.customer_members import CustomerMemberRepository
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.repositories.opportunities import NAME_CONFLICT_MESSAGE, opportunity_name_available
from sales_backend.services.customer_claims import claim_customer
from sales_backend.services.idempotency import execute_mutation
from sales_backend.services.opportunities import save_opportunity

router = APIRouter(prefix="/api/v1/customers", tags=["Customers"])


@router.get("/claim-pool", response_model=CustomerDirectoryPage)
async def customer_claim_pool(
    q: str | None = Query(default=None, max_length=100),
    page_size: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=2_147_483_647),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    # Match the existing company-directory API boundary. FDE customer selection
    # remains limited to its authorized opportunities, never the company pool.
    if identity.actor.role.value in {"fde", "fde_lead"}:
        raise HTTPException(403, "当前身份不可访问客户认领目录")
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await CustomerMemberRepository().claim_pool_page(
            connection, query=q, limit=page_size, offset=offset
        )


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
    scope: Literal["mine", "department", "company"] = "mine",
    q: str | None = Query(default=None, max_length=100),
    level: str | None = Query(default=None, max_length=20),
    unassigned: bool | None = Query(default=None),
    page_size: int = Query(default=50, ge=1, le=100),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> PageResponse:
    async with database.transaction(identity.actor, readonly=True) as connection:
        if scope in {"department", "company"} and identity.actor.role.value not in {"fde", "fde_lead"}:
            items = await CustomerMemberRepository().claim_pool(connection, query=q, limit=page_size)
            return PageResponse(items=items)
        items = await CustomerRepository().list(
            connection, query=q, level=level, unassigned=unassigned, limit=page_size
        )
    return PageResponse(items=items)


@router.post(
    "/{customer_id}/claims", status_code=201,
    description="一线销售、销售主管、销售总经理可为本人申请未认领客户；运营审批通过后生效。FDE 不可认领。",
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
                body.model_dump(),
                lambda: CustomerMutationRepository().create(connection, identity.actor, data=body.model_dump()),
            )
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


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
                body.model_dump(),
                lambda: CustomerMutationRepository().update(
                    connection,
                    identity.actor,
                    customer_id=customer_id,
                    data=payload,
                    expected_version=expected_version,
                ),
            )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except VersionConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
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


@router.post("/{customer_id}/opportunities", status_code=201)
async def create_customer_opportunity(
    customer_id: UUIDString,
    body: OpportunityCreate,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
) -> dict:
    if body.action == "create":
        try:
            assert_opportunity_create_allowed(identity.actor)
        except AgentModeForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "OPPORTUNITY_CREATE_FORBIDDEN") from exc
    try:
        async with database.transaction(identity.actor) as connection:
            return await execute_mutation(
                connection,
                identity.actor,
                idempotency_key,
                f"opportunities.save:{customer_id}",
                body.model_dump(),
                lambda: save_opportunity(
                    connection,
                    identity.actor,
                    customer_id=customer_id,
                    data=body.model_dump(),
                ),
            )
    except asyncpg.UniqueViolationError as exc:
        if exc.constraint_name == "uq_opportunity_customer_name":
            raise HTTPException(status.HTTP_409_CONFLICT, NAME_CONFLICT_MESSAGE) from exc
        raise
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
