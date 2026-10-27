"""Read only the current actor's direct-save receipt under current read grants.

J09: a lost or incomplete HTTP response must not release the original key.
Receipts commit with the business transaction and have no automatic expiry.
No receipt can mean not sent, in flight or rolled back: all stay unresolved.
"""

from uuid import UUID

from pydantic import ValidationError

from sales_backend.contracts.opportunity_save_receipt import (
    OpportunitySaveCancelled,
    OpportunitySaveCommitted,
    OpportunitySaveResult,
    OpportunitySaveUnresolved,
)
from sales_backend.db import json_value
from sales_backend.domain.agent import ActorContext
from sales_backend.services.authorization import require_permission
from sales_backend.services.idempotency import (
    OPPORTUNITY_SAVE_CANCEL_DIGEST,
    OPPORTUNITY_SAVE_CANCEL_RESPONSE,
    is_cancelled_opportunity_save,
)


async def _receipt(connection, actor, customer_id, request_key):
    return await connection.fetchrow(
        """SELECT request_digest,response FROM ops.mutation_receipt
        WHERE workspace_id=$1::uuid AND actor_id=$2::uuid AND operation=$3 AND request_key=$4""",
        actor.workspace_id,
        actor.user_id,
        f"opportunities.save:{customer_id}",
        request_key,
    )


async def _project(connection, actor, customer_id, request_key, row):
    unresolved = OpportunitySaveUnresolved(request_key=request_key)
    if row is None:
        return unresolved
    if is_cancelled_opportunity_save(f"opportunities.save:{customer_id}", row):
        return OpportunitySaveCancelled(request_key=request_key)
    try:
        result = OpportunitySaveResult.model_validate(json_value(row["response"]))
    except ValidationError:
        # Historical malformed receipts are not proof that a write failed.
        return unresolved
    if result.customer_id != customer_id:
        return unresolved
    await require_permission(connection, "opportunity.read", opportunity_id=result.id)
    if not await connection.fetchval(
        """SELECT EXISTS(SELECT 1 FROM crm.opportunity
        WHERE workspace_id=$1::uuid AND customer_id=$2::uuid AND id=$3::uuid AND deleted_at IS NULL)""",
        actor.workspace_id,
        str(customer_id),
        str(result.id),
    ):
        raise PermissionError("Saved opportunity is no longer readable in this customer")
    return OpportunitySaveCommitted(request_key=request_key, result=result)


async def read_opportunity_save_receipt(connection, actor: ActorContext, customer_id: UUID, request_key: UUID):
    await require_permission(connection, "customer.read", customer_id=customer_id)
    return await _project(connection, actor, customer_id, request_key,
                          await _receipt(connection, actor, customer_id, request_key))


async def cancel_opportunity_save_request(connection, actor: ActorContext, customer_id: UUID, request_key: UUID):
    if not connection.is_in_transaction():
        raise RuntimeError("Cancelling a save requires a transaction")
    await require_permission(connection, "customer.read", customer_id=customer_id)
    operation = f"opportunities.save:{customer_id}"
    # The exact write lock fences requests that have not yet reached their receipt
    # check, including ones still in transit. Existing committed writes stay intact.
    lock = f"mutation:{actor.workspace_id}:{actor.user_id}:{operation}:{request_key}"
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", lock)
    row = await _receipt(connection, actor, customer_id, request_key)
    if row is not None:
        return await _project(connection, actor, customer_id, request_key, row)
    await connection.execute(
        """INSERT INTO ops.mutation_receipt(workspace_id,actor_id,operation,request_key,request_digest,response)
        VALUES($1::uuid,$2::uuid,$3,$4,$5,$6::jsonb)""",
        actor.workspace_id, actor.user_id, operation, request_key,
        OPPORTUNITY_SAVE_CANCEL_DIGEST, OPPORTUNITY_SAVE_CANCEL_RESPONSE,
    )
    return OpportunitySaveCancelled(request_key=request_key)
