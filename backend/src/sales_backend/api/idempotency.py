from typing import Annotated
from uuid import UUID

from fastapi import Header, Request, HTTPException
from fastapi.responses import JSONResponse

from sales_backend.services.idempotency import IdempotencyConflict, OpportunitySaveCancelled
from sales_backend.contracts.opportunity_fields import NEW_FIELDS

MutationKey = Annotated[
    UUID | None,
    Header(
        alias="Idempotency-Key",
        description=(
            "同一次提交及响应不确定后的重试使用同一 UUID；新的提交使用新 UUID。"
            "相同标识和内容回放结果，标识相同但内容不同返回 409。旧客户端可省略。"
        ),
    ),
]


def require_material_key(data: dict, key: UUID | None):
    if key is None and NEW_FIELDS.intersection(data):
        raise HTTPException(422, "商机正式字段维护必须携带Idempotency-Key")
    if key is None and any(data.get(name) is not None for name in
                           ("material_lines", "trade_relation", "upstream_opportunity_id")):
        raise HTTPException(422, "交易关系与物料维护必须携带Idempotency-Key")


async def idempotency_conflict_handler(request: Request, exc: IdempotencyConflict) -> JSONResponse:
    code = "OPPORTUNITY_SAVE_CANCELLED" if isinstance(exc, OpportunitySaveCancelled) else "IDEMPOTENCY_CONFLICT"
    return JSONResponse(status_code=409, content={"detail": str(exc), "code": code},
                        headers={"Cache-Control": "no-store"})
