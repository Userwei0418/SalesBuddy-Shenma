"""Preserve domain errors; never turn infrastructure failures into empty history."""

import asyncpg

from sales_backend.domain.opportunity_access import OpportunityAccessError
from sales_backend.repositories.opportunity_transfer import OpportunityTransferRepository

MESSAGES = {
    "OPPORTUNITY_ACCESS_DENIED": (403, "来源商机或目标团队不在当前转交授权范围内"),
    "OPPORTUNITY_NOT_FOUND": (404, "商机不存在"),
    "VERSION_CONFLICT": (409, "商机已变化，请刷新后重新确认"),
    "IDEMPOTENCY_CONFLICT": (409, "该提交标识已用于不同内容"),
    "TRANSFER_STATE_NOT_ALLOWED": (409, "仅允许转交进行中的商机"),
    "TRANSFER_ATTRIBUTION_UNRESOLVED": (409, "历史业绩归属尚未确认，不能执行本次转交"),
    "INVALID_ACCESS_TARGET": (422, "请选择有效销售及其有效任职团队，并填写转交原因"),
    "IDEMPOTENCY_KEY_REQUIRED": (422, "请提供本次提交的幂等标识"),
}


async def transfer_owner(connection, opportunity_id, body, key):
    if key is None:
        raise OpportunityAccessError("IDEMPOTENCY_KEY_REQUIRED", MESSAGES["IDEMPOTENCY_KEY_REQUIRED"][1], status=422)
    return await _call(OpportunityTransferRepository().transfer(connection, opportunity_id, body, key))


async def transfer_capability(connection, opportunity_id):
    return await _call(OpportunityTransferRepository().capability(connection, opportunity_id))


async def transfer_candidates(connection, opportunity_id, *, query, limit, offset):
    return await _call(
        OpportunityTransferRepository().candidates(connection, opportunity_id, query=query, limit=limit, offset=offset)
    )


async def _call(operation):
    try:
        return await operation
    except asyncpg.PostgresError as exc:
        if exc.sqlstate not in {"42501", "P0002", "40001", "22023"}:
            raise
        code = str(exc).split("\n", 1)[0]
        if code not in MESSAGES:
            raise
        status, detail = MESSAGES[code]
        raise OpportunityAccessError(code, detail, status=status) from exc
