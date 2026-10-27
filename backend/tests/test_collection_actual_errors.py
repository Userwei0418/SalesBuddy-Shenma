"""Exercise the real save routes/service and error boundary without production I/O."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.testclient import TestClient

from sales_backend.api import customers, operations_opportunities
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.domain.collection_actual_errors import CollectionActualError
from sales_backend.error_contract import http_exception_handler
from sales_backend.main import collection_actual_error_handler, request_validation_error_handler
from sales_backend.observability import request_id_middleware, unhandled_exception_handler
from sales_backend.services import collection_actuals, operations

PRIVATE = "private-bank-account-do-not-display"
CUSTOMER_ID, OPPORTUNITY_ID = str(uuid4()), str(uuid4())
REVISION = "a" * 64


def pg_error(kind, message):
    return asyncpg.PostgresError.new({"C": kind.sqlstate, "M": message, "D": PRIVATE})


def payload():
    return dict(
        action="update", opportunity_id=OPPORTUNITY_ID, customer_id=CUSTOMER_ID,
        version_no=1, name="Example", probability=10, amount="10000000",
        expected_close_date="2024-10-21",
        collection_actuals=[dict(year=2026, quarter=4, amount="100000.00",
                                source_ref="Bank receipt", expected_actual_revision=REVISION)],
    )


@pytest.fixture
def route_client(monkeypatch):
    state = SimpleNamespace(error=None, can_edit=True, revision=REVISION, replace=False, rolled_back=False)

    class Connection:
        async def fetchrow(self, *_args):
            return {"id": OPPORTUNITY_ID, "can_edit": state.can_edit,
                    "actual": {"revision": state.revision, "requires_source_replacement": state.replace}}

        async def fetchval(self, *_args):
            if state.error:
                raise state.error
            return False

    class Database:
        @asynccontextmanager
        async def transaction(self, *_args, **_kwargs):
            try:
                yield Connection()
            except Exception:
                state.rolled_back = True
                raise

    async def execute(_connection, _actor, _key, _operation, _payload, action):
        return await action()

    async def save(connection, actor, *, customer_id, data):
        return await collection_actuals.save_collection_actuals(
            connection, actor, OPPORTUNITY_ID, {"id": customer_id}, data)

    async def scope(*_args, **_kwargs):
        return None

    monkeypatch.setattr(customers, "execute_mutation", execute)
    monkeypatch.setattr(operations, "execute_mutation", execute)
    monkeypatch.setattr(customers, "require_opportunity_mutation_scope", scope)
    monkeypatch.setattr(customers, "save_opportunity", save)
    monkeypatch.setattr(operations_opportunities, "save_opportunity", save)
    app = FastAPI()
    app.middleware("http")(request_id_middleware)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(CollectionActualError, collection_actual_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.include_router(customers.router)
    app.include_router(operations_opportunities.router)
    app.dependency_overrides[get_database] = lambda: Database()
    app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=object())
    app.dependency_overrides[get_management_identity] = lambda: SimpleNamespace(actor=object())
    return TestClient(app, raise_server_exceptions=False), state


def submit(client, *, body=None, operations_route=False, key=True):
    path = "/api/v1/console/opportunities" if operations_route else f"/api/v1/customers/{CUSTOMER_ID}/opportunities"
    data = payload() if body is None else body
    if not operations_route:
        data.pop("customer_id", None)
    return client.post(path, json=data, headers={"Idempotency-Key": str(uuid4())} if key else {})


@pytest.mark.parametrize("operations_route", [False, True])
@pytest.mark.parametrize("error,status,code", [
    (pg_error(asyncpg.CheckViolationError, "请先明确商机交易关系"), 422, "OPPORTUNITY_TRADE_RELATION_REQUIRED"),
    (pg_error(asyncpg.CheckViolationError, "下游商机不能重复登记我方回款，请核对合同关系与收款归属"),
     422, "OPPORTUNITY_COLLECTION_DOWNSTREAM_FORBIDDEN"),
    (pg_error(asyncpg.SerializationError, "实际回款或来源已变化，请刷新后重新核对"),
     409, "OPPORTUNITY_COLLECTION_VERSION_CONFLICT"),
    (pg_error(asyncpg.InvalidParameterValueError, "请核对实际回款金额、登记依据和备注"),
     422, "OPPORTUNITY_COLLECTION_INPUT_INVALID"),
    (pg_error(asyncpg.InvalidParameterValueError, "回款季度无效"), 422, "OPPORTUNITY_COLLECTION_PERIOD_INVALID"),
])
def test_exact_database_rejections_reach_both_save_routes(route_client, operations_route, error, status, code):
    client, state = route_client
    error.detail = PRIVATE
    state.error = error
    response = submit(client, operations_route=operations_route)
    assert response.status_code == status and response.json()["code"] == code
    assert state.rolled_back
    assert PRIVATE not in response.text and "23514" not in response.text
    assert response.json()["retryable"] is False
    assert response.headers["x-request-id"] == response.json()["request_id"]


@pytest.mark.parametrize("attribute,value,status,code", [
    ("can_edit", False, 403, "OPPORTUNITY_COLLECTION_FORBIDDEN"),
    ("revision", "b" * 64, 409, "OPPORTUNITY_COLLECTION_VERSION_CONFLICT"),
    ("replace", True, 409, "OPPORTUNITY_COLLECTION_SOURCE_CONFIRMATION_REQUIRED"),
])
def test_service_rejections_are_identifiable(route_client, attribute, value, status, code):
    client, state = route_client
    setattr(state, attribute, value)
    response = submit(client)
    assert response.status_code == status and response.json()["code"] == code
    assert response.json()["recovery_action"] == ("contact_admin" if status == 403 else "correct_request")
    assert response.json()["retryable"] is False
    assert state.rolled_back


def test_missing_mutation_key_has_safe_specific_message(route_client):
    response = submit(route_client[0], key=False)
    assert response.status_code == 422
    assert response.json()["code"] == "OPPORTUNITY_COLLECTION_IDEMPOTENCY_KEY_REQUIRED"
    assert "Idempotency-Key" not in response.json()["message"]


def test_pydantic_validation_preserves_location_without_echoing_bank_input(route_client):
    body = payload()
    body["collection_actuals"][0]["amount"] = PRIVATE
    response = submit(route_client[0], body=body)
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["detail"][0]["loc"] == ["body", "collection_actuals", 0, "amount"]
    assert PRIVATE not in response.text and '"input"' not in response.text


@pytest.mark.parametrize("error", [
    pg_error(asyncpg.CheckViolationError, "请先明确商机交易关系 " + PRIVATE),
    pg_error(asyncpg.InvalidParameterValueError, "请先明确商机交易关系"),
    RuntimeError("请先明确商机交易关系 " + PRIVATE),
])
def test_unknown_or_wrong_type_error_is_not_promoted_to_business_code(route_client, error):
    client, state = route_client
    state.error = error
    response = submit(client)
    assert response.json()["code"] in {"VALIDATION_ERROR", "INTERNAL_ERROR"}
    assert PRIVATE not in response.text
