"""请求追踪与日志。

在此之前 API 侧没有 logging 配置、没有请求标识、也没有兜底异常处理：
仓储抛出意外异常时客户端只拿到一个裸 500，日志里没有任何可关联的线索。
这里补上三件事，不改任何已有的业务错误映射：
- 每个请求有 request_id，入日志也回写到响应头，便于按 id 串起来查；
- 未被业务代码处理的异常统一记 traceback，再返回带 request_id 的 500；
- 统一日志格式，API 与 worker 共用。
"""

from __future__ import annotations

import logging
import ipaddress
import uuid
from time import perf_counter
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from sales_backend.domain.sanitization import redact_log
from sales_backend.request_metadata import RequestMetadata, current_actor, request_metadata
from sales_backend.services.request_audit import record_http_operation
from sales_backend.performance import RequestTimings, request_timings

REQUEST_ID_HEADER = "X-Request-Id"
LOG_FORMAT = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s %(message)s"

_request_id: ContextVar[str] = ContextVar("request_id", default="-")
logger = logging.getLogger("sales_backend.api")


def current_request_id() -> str:
    return _request_id.get()


class _RequestIdFilter(logging.Filter):
    """让 LOG_FORMAT 里的 %(request_id)s 在任何 logger 上都有值。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = current_request_id()
        return True


class SafeFormatter(logging.Formatter):
    def format(self, record):
        return redact_log(super().format(record))


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeFormatter(LOG_FORMAT))
    handler.addFilter(_RequestIdFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


async def request_id_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """沿用上游传入的 X-Request-Id，没有就生成一个，并回写到响应头。"""
    incoming = request.headers.get(REQUEST_ID_HEADER, "").strip()
    try:
        request_id = str(uuid.UUID(incoming))
    except ValueError:
        request_id = str(uuid.uuid4())
    try:
        client_ip = str(ipaddress.ip_address(request.client.host)) if request.client else ""
    except ValueError:
        client_ip = ""
    metadata_token = request_metadata.set(
        RequestMetadata(request_id, client_ip, request.headers.get("user-agent", "")[:500])
    )
    actor_token = current_actor.set(None)
    # 兜底异常处理器运行在本中间件的 contextvar 作用域之外（ServerErrorMiddleware
    # 在更外层），所以同时挂到 request.state 上供它读取。
    request.state.request_id = request_id
    token = _request_id.set(request_id)
    response = None
    started = perf_counter()
    timing = RequestTimings()
    timing_token = request_timings.set(timing)
    try:
        response = await call_next(request)
        identity = getattr(request.state, "identity", None)
        if identity:
            current_actor.set(identity.actor)
        if response.status_code in {401, 403}:
            logger.warning(
                "authentication or permission check rejected method=%s path=%s status=%s",
                request.method,
                request.url.path,
                response.status_code,
                extra={"event_type": "access_denied"},
            )
    finally:
        audit_started = perf_counter()
        if request.url.path.startswith("/api/v1/") and not request.url.path.startswith("/api/v1/health/"):
            await record_http_operation(
                getattr(request.app.state, "database", None),
                getattr(request.state, "identity", None),
                request.method,
                request.url.path,
                response.status_code if response else 500,
                export_count=int(response.headers["X-Export-Count"]) if response and response.headers.get("X-Export-Count", "").isdigit() else None,
                filters={key: value[:200] for key,value in request.query_params.items()
                         if key in {"period","start","end","actor","category","action","q","role","user","status","module","level","industry","state","owner","customer","outcome","department"}},
            )
        elapsed_ms = (perf_counter() - started) * 1000
        audit_ms = (perf_counter() - audit_started) * 1000
        if response is not None:
            response.headers["Server-Timing"] = (
                f"app;dur={elapsed_ms:.1f}, db;dur={timing.database_ms:.1f}, "
                f"pool;dur={timing.pool_wait_ms:.1f}, audit;dur={audit_ms:.1f}"
            )
            response.headers["X-DB-Queries"] = str(timing.query_count)
        if elapsed_ms >= 1000:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            logger.warning(
                "slow_request method=%s route=%s status=%s duration_ms=%.1f db_ms=%.1f "
                "pool_wait_ms=%.1f audit_ms=%.1f queries=%s",
                request.method, route, response.status_code if response else 500,
                elapsed_ms, timing.database_ms, timing.pool_wait_ms, audit_ms, timing.query_count,
                extra={"event_type": "slow_request"},
            )
        request_timings.reset(timing_token)
        _request_id.reset(token)
        request_metadata.reset(metadata_token)
        current_actor.reset(actor_token)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底：业务代码没处理的异常在这里留下可追溯的日志。

    只处理未被端点捕获的异常，因此不会影响任何既有的状态码与 detail 文案。
    """
    request_id = getattr(request.state, "request_id", None) or current_request_id()
    logger.exception(
        "unhandled request error",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "actor": getattr(getattr(request.state, "identity", None), "actor", None),
        },
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "INTERNAL_ERROR", "request_id": request_id},
        headers={REQUEST_ID_HEADER: request_id},
    )
