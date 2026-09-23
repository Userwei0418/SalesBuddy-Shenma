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


async def execute_mutation(
    connection: asyncpg.Connection,
    actor: ActorContext,
    key: UUID | None,
    operation: str,
    payload: Any,
    execute: Callable[[], Awaitable[Any]],
) -> Any:
    if key is None:
        return await execute()
    if not connection.is_in_transaction():
        raise RuntimeError("Mutation receipts require a business transaction")
    document = to_jsonable_python({"payload": payload, "scope": actor.model_dump()})
    digest = hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    lock = f"mutation:{actor.workspace_id}:{actor.user_id}:{operation}:{key}"
    # Serializes contenders until BOTH the business write and receipt commit.
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", lock)
    receipt = await connection.fetchrow(
        """SELECT request_digest,response FROM ops.mutation_receipt
        WHERE workspace_id=$1::uuid AND actor_id=$2::uuid AND operation=$3 AND request_key=$4""",
        actor.workspace_id,
        actor.user_id,
        operation,
        key,
    )
    if receipt:
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
