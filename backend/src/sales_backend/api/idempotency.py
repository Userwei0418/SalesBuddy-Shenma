from typing import Annotated
from uuid import UUID

from fastapi import Header, Request, HTTPException
from fastapi.responses import JSONResponse

from sales_backend.services.idempotency import IdempotencyConflict, OpportunitySaveCancelled
from sales_backend.contracts.opportunity_fields import NEW_FIELDS
from sales_backend.error_contract import error_response

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
    if key is None and data.get("recognized_actuals"):
        from sales_backend.domain.recognized_actual_errors import RecognizedActualError
        raise RecognizedActualError("OPPORTUNITY_RECOGNIZED_IDEMPOTENCY_KEY_REQUIRED")
    if key is None and data.get("collection_actuals"):
        raise HTTPException(422, "OPPORTUNITY_COLLECTION_IDEMPOTENCY_KEY_REQUIRED")
    if key is None and NEW_FIELDS.intersection(data):
        raise HTTPException(422, "商机正式字段维护必须携带Idempotency-Key")
    if key is None and any(data.get(name) is not None for name in
                           ("material_lines", "trade_relation", "upstream_opportunity_id")):
        raise HTTPException(422, "交易关系与物料维护必须携带Idempotency-Key")


async def idempotency_conflict_handler(request: Request, exc: IdempotencyConflict) -> JSONResponse:
    code = "OPPORTUNITY_SAVE_CANCELLED" if isinstance(exc, OpportunitySaveCancelled) else "IDEMPOTENCY_CONFLICT"
    return error_response(request, status_code=409, detail=str(exc), code=code)
