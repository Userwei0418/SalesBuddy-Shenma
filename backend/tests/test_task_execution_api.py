"""HTTP boundary tests; persistence/RLS is separately verified by the PG script."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.task_execution import router


class NoWrites:
    @asynccontextmanager
    async def transaction(self, actor, readonly=False):
        yield self

    async def fetchval(self, *args):
        raise AssertionError("invalid HTTP request reached database mutation")


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=object())
    app.dependency_overrides[get_database] = NoWrites
    return TestClient(app)


BASE = dict(version_no=1, requirements_version=1, responsibility_version=1)


@pytest.mark.parametrize(
    "patch",
    [
        {"percent": 100},
        {"version_no": True},
        {"requirements_version": None},
        {"responsibility_version": "1"},
        {"text": "  "},
        {"text": 3},
    ],
)
def test_invalid_feedback_never_reaches_database(client, patch):
    response = client.post(
        f"/api/v1/tasks/{uuid4()}/execution/append_feedback",
        json={**BASE, "text": "hello", **patch},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 422


def test_mutation_key_is_required(client):
    response = client.post(f"/api/v1/tasks/{uuid4()}/execution/append_feedback", json={**BASE, "text": "hello"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "TASK_EXECUTION_IDEMPOTENCY_KEY_REQUIRED"


def test_requirements_response_cannot_smuggle_handover_identifier(client):
    response = client.post(
        f"/api/v1/tasks/{uuid4()}/execution/respond_requirements",
        json={**BASE, "change_id": str(uuid4()), "decision": "accept", "handover_id": str(uuid4())},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 422


def test_unknown_action_has_specific_code(client):
    response = client.post(
        f"/api/v1/tasks/{uuid4()}/execution/fake_action", json=BASE, headers={"Idempotency-Key": str(uuid4())}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "TASK_EXECUTION_UNKNOWN_ACTION"


@pytest.mark.parametrize(
    "contribution",
    [
        {"metric_code": "collection", "value": 1, "note": "evidence"},
        {"metric_code": "collection", "value": "1.001", "note": "evidence"},
        {"metric_code": "collection", "value": "1", "note": " "},
        {"metric_code": "collection", "value": "1", "note": "evidence", "counts_toward_financial_actuals": True},
    ],
)
def test_invalid_contribution_never_reaches_database(client, contribution):
    response = client.post(
        f"/api/v1/tasks/{uuid4()}/execution/submit_completion",
        json={**BASE, "note": "delivered", "actual_contribution": contribution},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 422
