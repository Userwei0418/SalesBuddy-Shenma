"""请求追踪与兜底异常处理。

用一个最小 ASGI 应用验证中间件与处理器本身，不触发真实应用的 lifespan，
因此不需要数据库。
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from sales_backend.observability import (
    REQUEST_ID_HEADER,
    configure_logging,
    current_request_id,
    request_id_middleware,
    unhandled_exception_handler,
)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.middleware("http")(request_id_middleware)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/echo")
    async def echo() -> dict[str, str]:
        return {"request_id": current_request_id()}

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError("仓储层意外异常")

    return TestClient(app, raise_server_exceptions=False)


def test_response_carries_a_request_id(client: TestClient) -> None:
    response = client.get("/echo")
    assert response.status_code == 200
    request_id = response.headers[REQUEST_ID_HEADER]
    assert request_id and request_id != "-"
    assert response.json()["request_id"] == request_id


def test_upstream_request_id_is_preserved(client: TestClient) -> None:
    response = client.get("/echo", headers={REQUEST_ID_HEADER: "00000000-0000-4000-a000-000000000001"})
    assert response.headers[REQUEST_ID_HEADER] == "00000000-0000-4000-a000-000000000001"
    assert response.json()["request_id"] == "00000000-0000-4000-a000-000000000001"


def test_each_request_gets_a_distinct_id(client: TestClient) -> None:
    first = client.get("/echo").json()["request_id"]
    second = client.get("/echo").json()["request_id"]
    assert first != second


def test_unhandled_error_returns_traceable_500(client: TestClient) -> None:
    response = client.get("/boom", headers={REQUEST_ID_HEADER: "00000000-0000-4000-a000-000000000002"})
    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "INTERNAL_ERROR"
    assert body["request_id"] == "00000000-0000-4000-a000-000000000002"
    assert response.headers[REQUEST_ID_HEADER] == "00000000-0000-4000-a000-000000000002"


def test_unhandled_error_is_logged_with_request_id(client: TestClient, caplog) -> None:
    with caplog.at_level(logging.ERROR, logger="sales_backend.api"):
        client.get("/boom", headers={REQUEST_ID_HEADER: "00000000-0000-4000-a000-000000000003"})
    records = [r for r in caplog.records if r.name == "sales_backend.api"]
    assert records, "未处理异常没有留下日志"
    assert records[0].request_id == "00000000-0000-4000-a000-000000000003"
    assert records[0].exc_info is not None, "日志里缺少 traceback"


def test_log_format_renders_request_id_outside_a_request() -> None:
    """worker 等无请求上下文的场景也不能因为缺 request_id 而炸掉格式化。"""
    configure_logging()
    handler = logging.getLogger().handlers[0]
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None)
    for log_filter in handler.filters:
        log_filter.filter(record)
    assert "[-]" in handler.format(record)


def test_invalid_incoming_trace_is_replaced(client):
    from uuid import UUID

    response = client.get("/echo", headers={REQUEST_ID_HEADER: "arbitrary-text"})
    assert UUID(response.headers[REQUEST_ID_HEADER])


def test_timings_are_returned_without_payload_or_credentials(client):
    response = client.get('/echo', headers={'Authorization': 'Bearer private-value'})
    assert 'app;dur=' in response.headers['server-timing']
    assert 'pool;dur=' in response.headers['server-timing']
    assert response.headers['x-db-queries'] == '0'
    assert 'private-value' not in str(response.headers)
