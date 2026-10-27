"""Request receipts and their business writes share the caller's transaction.

An absent key preserves compatibility with old clients. New clients must reuse
the key after an uncertain response and allocate a new key for a new intent.
"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import asyncpg
from pydantic_core import to_jsonable_python

from sales_backend.db import json_value
from sales_backend.domain.agent import ActorContext


class IdempotencyConflict(Exception):
    pass


class OpportunitySaveCancelled(IdempotencyConflict):
    """A direct opportunity save was fenced before it could commit."""


OPPORTUNITY_SAVE_CANCEL_DIGEST = hashlib.sha256(b"opportunities.save:cancelled:v1").hexdigest()
OPPORTUNITY_SAVE_CANCEL_RESPONSE = {"status": "cancelled", "receipt_type": "opportunity_save_request.v1"}


def is_cancelled_opportunity_save(operation, receipt):
    return (
        operation.startswith("opportunities.save:")
        and receipt["request_digest"] == OPPORTUNITY_SAVE_CANCEL_DIGEST
        and json_value(receipt["response"]) == OPPORTUNITY_SAVE_CANCEL_RESPONSE
    )


def receipt_payload(operation: str, payload: Any) -> Any:
    """Ignore only request-time confirmation, preserving every business fact.

    A committed save can be retried after the company reminder threshold changes.
    Its response receipt must still replay even if the UI no longer asks for an
    acknowledgement. The original payload remains intact for an uncommitted save
    to validate; similarly named fields in other operations remain significant.
    """
    if not isinstance(payload, dict):
        return payload
    if operation.partition(":")[0] in {"opportunities.save", "opportunity.save"} and ":" in operation:
        return {key: value for key, value in payload.items() if key != "amount_confirmed_value"}
    if operation == "visits.archive":
        fields = payload.get("fields")
        if isinstance(fields, dict) and isinstance(fields.get("_opportunity_mutation"), dict):
            return {**payload, "fields": {**fields, "_opportunity_mutation": {
                key: value for key, value in fields["_opportunity_mutation"].items()
                if key != "amount_confirmed_value"
            }}}
    return payload


async def execute_mutation(
    connection: asyncpg.Connection,
    actor: ActorContext,
    key: UUID | None,
    operation: str,
    payload: Any,
    execute: Callable[[], Awaitable[Any]],
    *,
    authorize: Callable[[], Awaitable[None]] | None = None,
) -> Any:
    if key is None:
        if authorize is not None:
            await authorize()
        return await execute()
    if not connection.is_in_transaction():
        raise RuntimeError("Mutation receipts require a business transaction")
    document = to_jsonable_python({"payload": payload, "scope": actor.model_dump()})
    document["payload"] = receipt_payload(operation, document["payload"])
    digest = hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    lock = f"mutation:{actor.workspace_id}:{actor.user_id}:{operation}:{key}"
    # Serializes contenders until BOTH the business write and receipt commit.
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", lock)
    # An object-specific caller can refresh authority after waiting for a
    # competing request, before either receipt replay or a new business write.
    if authorize is not None:
        await authorize()
    receipt = await connection.fetchrow(
        """SELECT request_digest,response FROM ops.mutation_receipt
        WHERE workspace_id=$1::uuid AND actor_id=$2::uuid AND operation=$3 AND request_key=$4""",
        actor.workspace_id,
        actor.user_id,
        operation,
        key,
    )
    if receipt:
        if is_cancelled_opportunity_save(operation, receipt):
            raise OpportunitySaveCancelled("这次商机提交已终止，请重新填写并发起新的提交")
        if receipt["request_digest"] != digest:
            raise IdempotencyConflict("该提交标识已用于不同内容，请刷新后重新提交")
        return json_value(receipt["response"])
    result = to_jsonable_python(await execute())
    await connection.execute(
        """INSERT INTO ops.mutation_receipt(workspace_id,actor_id,operation,request_key,request_digest,response)
        VALUES($1::uuid,$2::uuid,$3,$4,$5,$6::jsonb)""",
        actor.workspace_id,
        actor.user_id,
        operation,
        key,
        digest,
        result,
    )
    return result
