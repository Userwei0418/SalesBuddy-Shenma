from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.contracts.opportunity_transfer import OpportunityTransfer, OpportunityTransferResult
from sales_backend.db import Database
from sales_backend.domain.opportunity_access import OpportunityAccessError
from sales_backend.services.opportunity_transfer import transfer_owner
from sales_backend.error_contract import error_response

router = APIRouter(prefix="/api/v1/opportunities", tags=["Opportunity transfer"])
RequiredKey = Annotated[UUID, Header(alias="Idempotency-Key", description="同次未知结果重试须使用原UUID与原请求内容。")]


@router.post("/{opportunity_id}/transfer", response_model=OpportunityTransferResult)
async def transfer(
    opportunity_id: UUID,
    body: OpportunityTransfer,
    idempotency_key: RequiredKey,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    try:
        async with database.transaction(identity.actor) as connection:
            return await transfer_owner(connection, opportunity_id, body, idempotency_key)
    except OpportunityAccessError as exc:
        return error_response(status_code=exc.status, code=exc.code, detail=exc.detail)
