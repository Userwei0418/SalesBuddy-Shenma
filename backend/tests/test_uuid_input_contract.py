"""API validation before any business transaction; only identity is stubbed."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from sales_backend.api import assistant, business, customers, notifications, risks, tasks
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.contracts.models import ConversationCreate, MessageCreate, TaskCreate, VisitCreate
from sales_backend.contracts.types import UUIDString
from sales_backend.domain.agent import RoleCode

VALID = "00a00bb0-0000-4000-8000-000000000001"


@pytest.fixture
def client():
    class ForbiddenDatabase:
        calls = 0

        def transaction(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("Invalid UUID reached a business transaction")

    database = ForbiddenDatabase()
    app = FastAPI()
    for module in (assistant, business, customers, tasks, notifications, risks):
        app.include_router(module.router)
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=SimpleNamespace(role=RoleCode.SALES))
    with TestClient(app) as result:
        yield result, database


PATH_CASES = [
    ("GET", "/agent/runs/{id}", None),
    ("GET", "/conversations/{id}/messages", None),
    ("POST", "/conversations/{id}/messages", {"text": "测试消息"}),
    ("GET", "/customers/{id}", None),
    ("PATCH", "/customers/{id}", {"name": "已核实名称"}),
    (
        "POST",
        "/customers/{id}/opportunities",
        {"name": "商机", "probability": 10, "amount": 10, "expected_close_date": "2026-12-01"},
    ),
    ("GET", "/tasks/{id}", None),
    ("POST", "/tasks/{id}/events", {"event_type": "accept"}),
    ("GET", "/risks/{id}", None),
    ("POST", "/risks/{id}/resolve", {"resolution_note": "已核实解决情况"}),
    ("POST", "/notifications/{id}/read", None),
    ("GET", "/visits/{id}", None),
    ("PATCH", "/visits/{id}", {}),
]


@pytest.mark.parametrize("invalid", ["not-a-uuid", "null"])
@pytest.mark.parametrize("method,path,body", PATH_CASES)
def test_invalid_uuid_path_returns_422_before_business_database(client, method, path, body, invalid):
    http, database = client
    response = http.request(method, "/api/v1" + path.format(id=invalid), json=body)
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"][0] == "path"
    assert database.calls == 0


@pytest.mark.parametrize("name", ["customer_id", "opportunity_id"])
@pytest.mark.parametrize("invalid", ["", "undefined", "123"])
def test_invalid_task_filter_is_not_passed_to_uuid_cast(client, name, invalid):
    http, database = client
    response = http.get("/api/v1/tasks", params={name: invalid})
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["query", name]
    assert database.calls == 0


@pytest.mark.parametrize(
    "path,body,field",
    [
        ("/conversations", {"mode": "visit_entry", "customer_id": "invalid"}, "customer_id"),
        (
            "/tasks",
            {
                "description": "测试接收人待办",
                "target_position": "self",
                "due_at": "2026-12-01T12:00:00+08:00",
                "customer_id": "invalid",
            },
            "customer_id",
        ),
        ("/visits", {"customer_id": "invalid", "fields": {}}, "customer_id"),
        (
            f"/customers/{VALID}/opportunities",
            {
                "action": "update",
                "opportunity_id": "invalid",
                "name": "已核实商机",
                "probability": 10,
                "amount": 10,
                "expected_close_date": "2026-12-01",
            },
            "opportunity_id",
        ),
    ],
)
def test_invalid_body_uuid_is_request_validation_error(client, path, body, field):
    http, database = client
    response = http.post("/api/v1" + path, json=body)
    assert response.status_code == 422, response.text
    assert any(error["loc"] == ["body", field] for error in response.json()["detail"])
    assert database.calls == 0


@pytest.mark.parametrize("role", [RoleCode.SALES, RoleCode.FDE, RoleCode.FDE_LEAD])
@pytest.mark.parametrize("invalid", ["not-a-uuid", "null", 12, [], {}])
def test_nested_visit_opportunity_is_validated_before_scope_database(client, role, invalid):
    http, database = client
    http.app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=SimpleNamespace(role=role))
    response = http.post("/api/v1/visits", json={"customer_id": VALID, "fields": {"opportunity_id": invalid}})
    assert response.status_code == 422, response.text
    assert any(error["loc"] == ["body", "fields"] for error in response.json()["detail"])
    assert database.calls == 0


@pytest.mark.parametrize("reference", [{}, {"opportunity_id": None}, {"opportunity_id": ""}])
def test_missing_visit_opportunity_and_historical_fields_are_preserved(reference):
    fields = {**reference, "visit_goal": "历史目标", "future_field": {"opaque": "client:id"}}
    assert VisitCreate(customer_id=VALID, fields=fields).fields == fields


def test_uuid_strings_canonicalize_and_preserve_opaque_client_message_id():
    value = TypeAdapter(UUIDString).validate_python(VALID.upper())
    assert value == VALID and isinstance(value, str)
    assert TypeAdapter(UUIDString).json_schema() == {"format": "uuid", "type": "string"}
    assert VisitCreate(customer_id=VALID.upper(), fields={}).customer_id == VALID
    fields = {"opportunity_id": VALID.upper(), "visit_goal": "历史目标", "opaque_reference": "client:request-1"}
    assert VisitCreate(customer_id=VALID, fields=fields).fields == {**fields, "opportunity_id": VALID}
    assert ConversationCreate(customer_id=VALID.upper()).customer_id == VALID
    assert (
        TaskCreate(
            description="无需关联客户的待办", target_position="self", due_at="2026-12-01", customer_id=""
        ).customer_id
        is None
    )
    with pytest.raises(ValidationError):
        VisitCreate(customer_id="", fields={})
    assert (
        MessageCreate(text="测试", client_message_id="device:opaque-request-1").client_message_id
        == "device:opaque-request-1"
    )
