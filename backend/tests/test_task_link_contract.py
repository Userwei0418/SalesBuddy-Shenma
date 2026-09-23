"""Task selectors remain paginated, minimal, and separate from task details."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.tasks import router
from sales_backend.domain.agent import ActorContext


@pytest.fixture
def boundary():
    class Database:
        def __init__(self):
            self.calls = []
            self.rows = []

        @asynccontextmanager
        async def transaction(self, actor, readonly=False):
            assert readonly
            yield self

        async def fetch(self, sql, *args):
            self.calls.append(args)
            return self.rows

    database = Database()
    actor = ActorContext(workspace_id=str(uuid4()), user_id=str(uuid4()), role="sales", data_scope="self")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=actor)
    with TestClient(app) as client:
        yield client, database


def test_customer_page_preserves_cursor_and_only_returns_minimal_references(boundary):
    client, database = boundary
    database.rows = [{"id": str(uuid4()), "name": "客户", "private_extra": "not exposed"} for _ in range(3)]
    response = client.get("/api/v1/tasks/customers", params={"q": "  客户  ", "page_size": 2, "offset": 4})
    assert response.status_code == 200
    page = response.json()
    assert len(page["items"]) == 2 and page["has_more"] and page["next_offset"] == 6
    assert "private_extra" not in page["items"][0]
    assert database.calls[0][-3:] == ("客户", 3, 4)


def test_opportunity_choice_includes_exact_source_filter_and_empty_page_is_valid(boundary):
    client, database = boundary
    customer, opportunity = str(uuid4()), str(uuid4())
    response = client.get("/api/v1/tasks/opportunities", params={
        "customer_id": customer, "opportunity_id": opportunity, "q": " 客户项目 ", "offset": 20,
    })
    assert response.status_code == 200
    assert response.json() == {"items": [], "has_more": False, "next_offset": None}
    assert database.calls[0][3:] == (customer, "客户项目", 21, 20, opportunity)


@pytest.mark.parametrize("params", [
    {"offset": -1}, {"offset": 2_147_483_648}, {"page_size": 0}, {"page_size": 101}, {"q": "客" * 101},
])
def test_invalid_page_is_rejected_before_query(boundary, params):
    client, database = boundary
    assert client.get("/api/v1/tasks/customers", params=params).status_code == 422
    assert not database.calls


def test_opportunity_choice_requires_valid_customer_id(boundary):
    client, database = boundary
    for params in ({}, {"customer_id": "not-an-id"}, {"customer_id": str(uuid4()), "opportunity_id": "bad"}):
        assert client.get("/api/v1/tasks/opportunities", params=params).status_code == 422
    assert not database.calls
