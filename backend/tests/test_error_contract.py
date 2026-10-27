"""Exercise the real exception boundary, without database/lifespan/provider calls."""
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.testclient import TestClient

from sales_backend.error_contract import error_response, http_exception_handler
from sales_backend.main import request_validation_error_handler, runtime_credential_unavailable_handler
from sales_backend.observability import request_id_middleware, unhandled_exception_handler
from sales_backend.security.runtime_credentials import RuntimeCredentialUnavailable
from sales_backend.domain.work_report import WorkReportError
from sales_backend.main import work_report_error_handler

TRACE = "00000000-0000-4000-a000-000000000011"
PRIVATE = "private-input-do-not-display"


@pytest.fixture
def client():
    app = FastAPI()
    app.middleware("http")(request_id_middleware)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(RuntimeCredentialUnavailable, runtime_credential_unavailable_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    class Input(BaseModel):
        customer_id: int
        password: str

    @app.post("/input")
    async def validate(body: Input):
        return {"ok": True}

    @app.api_route("/boom", methods=["GET", "POST"])
    async def boom():
        raise RuntimeError("SELECT private_column FROM internal_table " + PRIVATE)

    @app.post("/configuration")
    async def configuration():
        raise RuntimeCredentialUnavailable()

    @app.get("/domain")
    async def domain():
        raise HTTPException(403, {"code": "TASK_EXECUTION_FORBIDDEN"}, headers={"X-Domain": "kept"})

    @app.get("/low-level")
    async def low_level():
        raise HTTPException(422, "customer_id: Field required " + PRIVATE)

    @app.get("/nested-validation")
    async def nested_validation():
        raise HTTPException(422, {"code": "TASK_EXECUTION_INVALID_COMMAND", "errors": [
            {"loc": ["description"], "type": "missing", "msg": "Field required " + PRIVATE,
             "input": {"password": PRIVATE}, "ctx": {"error": PRIVATE}},
        ]})

    @app.get("/returned")
    async def returned():
        # Direct error responses use the same request context as exceptions.
        return error_response(status_code=409, code="VERSION_CONFLICT", detail="版本变化",
                              extra={"current_version": 4})

    @app.get("/rate-limit")
    async def rate_limit():
        raise HTTPException(429, "private limiter detail " + PRIVATE, headers={"Retry-After": "12"})

    return TestClient(app, raise_server_exceptions=False)


def assert_envelope(response):
    body = response.json()
    assert {"code", "message", "detail", "request_id", "retryable", "recovery_action"} <= body.keys()
    assert UUID(body["request_id"])
    assert body["request_id"] == response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"
    assert PRIVATE not in response.text and "SELECT" not in response.text and "Field required" not in response.text
    return body


def test_validation_keeps_machine_locations_but_never_private_input(client):
    response = client.post("/input", json={"password": PRIVATE}, headers={"X-Request-Id": TRACE})
    body = assert_envelope(response)
    assert response.status_code == 422 and body["code"] == "VALIDATION_ERROR"
    assert body["request_id"] == TRACE and body["retryable"] is False
    assert body["detail"] == [{"loc": ["body", "customer_id"], "type": "missing",
                               "msg": "请求字段不符合要求，请检查填写内容"}]
    assert "customer_id" not in body["message"]


@pytest.mark.parametrize("method,action,retryable", [("GET", "retry_read", True), ("POST", "check_status", False)])
def test_unhandled_failures_keep_legacy_code_and_correct_recovery(client, method, action, retryable):
    response = client.request(method, "/boom", headers={"X-Request-Id": TRACE})
    body = assert_envelope(response)
    assert response.status_code == 500
    assert body["code"] == body["detail"] == "INTERNAL_ERROR"
    assert body["recovery_action"] == action and body["retryable"] is retryable
    if method == "POST":
        assert "勿重复提交" in body["message"]


def test_business_code_nested_detail_and_headers_survive(client):
    response = client.get("/domain")
    body = assert_envelope(response)
    assert response.status_code == 403 and body["code"] == "TASK_EXECUTION_FORBIDDEN"
    assert body["detail"] == {"code": "TASK_EXECUTION_FORBIDDEN"}
    assert response.headers["x-domain"] == "kept"


def test_legacy_low_level_text_becomes_safe_validation_copy(client):
    response = client.get("/low-level")
    body = assert_envelope(response)
    assert body["code"] == "VALIDATION_ERROR" and "customer_id" not in response.text


def test_nested_validation_keeps_structural_contract_without_input_or_ctx(client):
    body = assert_envelope(client.get("/nested-validation"))
    assert body["code"] == "TASK_EXECUTION_INVALID_COMMAND"
    assert body["detail"]["errors"] == [{"loc": ["description"], "type": "missing",
                                         "msg": "请求字段不符合要求，请检查填写内容"}]


@pytest.mark.parametrize("path,method,status,code", [
    ("/missing", "GET", 404, "NOT_FOUND"), ("/domain", "POST", 405, "METHOD_NOT_ALLOWED"),
])
def test_framework_errors_also_use_contract(client, path, method, status, code):
    response = client.request(method, path)
    body = assert_envelope(response)
    assert response.status_code == status and body["code"] == code
    if status == 405:
        assert "GET" in response.headers["allow"]


def test_explicit_configuration_rejection_is_not_unknown_write(client):
    body = assert_envelope(client.post("/configuration"))
    assert body["code"] == "RUNTIME_CREDENTIAL_UNAVAILABLE"
    assert body["recovery_action"] == "contact_admin" and body["retryable"] is False
    assert "尚未确认" not in body["message"]


def test_direct_response_retains_trace_and_known_extensions(client):
    body = assert_envelope(client.get("/returned", headers={"X-Request-Id": TRACE}))
    assert body["request_id"] == TRACE and body["current_version"] == 4


def test_rate_limit_retains_server_cooldown(client):
    response = client.get("/rate-limit")
    body = assert_envelope(response)
    assert body["code"] == "RATE_LIMITED" and body["recovery_action"] == "retry_read"
    assert response.headers["retry-after"] == "12"


def test_safe_legacy_business_prose_remains_compatible():
    request = SimpleNamespace(method="POST", state=SimpleNamespace(request_id=TRACE))
    body = json.loads(error_response(request, status_code=409, detail="客户已存在，请核对后再建档").body)
    assert body["detail"] == "客户已存在，请核对后再建档"
    assert body["code"] == "CUSTOMER_ALREADY_EXISTS"


@pytest.mark.parametrize("detail,code", [
    ("内容或关联已修改，请重新审核", "VISIT_REVIEW_STALE"),
    ("本次参与人员已修改，请重新审核后归档", "VISIT_PARTICIPANTS_CHANGED"),
    ("请完成独立质检后再确认归档", "VISIT_REVIEW_REQUIRED"),
])
def test_visit_review_guidance_is_preserved(detail, code):
    body = json.loads(error_response(status_code=422, detail=detail).body)
    assert body["code"] == code and body["message"] == body["detail"] == detail


@pytest.mark.asyncio
async def test_domain_retry_prohibition_and_version_are_retained():
    request = SimpleNamespace(method="GET", state=SimpleNamespace(request_id=TRACE))
    response = await work_report_error_handler(request, WorkReportError(
        "WORK_REPORT_SOURCE_FAILED", "internal source diagnostic", status=503,
        retryable=False, current_version=3))
    body = json.loads(response.body)
    assert body["retryable"] is False and body["recovery_action"] == "correct_request"
    assert body["current_version"] == 3 and "diagnostic" not in body["message"]


def test_openapi_exposes_envelope_and_preserves_domain_schemas():
    from sales_backend.main import app
    schema = app.openapi()
    required = schema["components"]["schemas"]["PublicApiError"]["required"]
    assert {"code", "message", "request_id", "retryable", "recovery_action"} <= set(required)
    operation = schema["paths"]["/api/v1/visit-imports"]["post"]
    assert operation["responses"]["409"]["content"]["application/json"]["schema"]["$ref"].endswith("/UploadError")
    assert operation["responses"]["default"]["$ref"].endswith("/PublicApiFailure")
