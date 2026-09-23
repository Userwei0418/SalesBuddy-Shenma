from typing import Annotated
from uuid import UUID

from fastapi import Header, Request
from fastapi.responses import JSONResponse

from sales_backend.services.idempotency import IdempotencyConflict

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


async def idempotency_conflict_handler(request: Request, exc: IdempotencyConflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc), "code": "IDEMPOTENCY_CONFLICT"})
