"""Boundary tests; PostgreSQL proof lives in the opt-in private script."""

from uuid import uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.opportunity_transfer import router
from sales_backend.contracts.opportunity_transfer import OpportunityTransfer
from sales_backend.domain.opportunity_access import OpportunityAccessError
from sales_backend.services.opportunity_transfer import transfer_owner


def request(**changes):
    return {
        "version_no": 1,
        "new_owner_user_ref_id": str(uuid4()),
        "new_owner_team_id": str(uuid4()),
        "reason": "明确交接",
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"version_no": True},
        {"version_no": "1"},
        {"version_no": 0},
        {"reason": "  "},
        {"new_owner_team_id": None},
        {"workspace_id": str(uuid4())},
        {"amount": "100"},
    ],
)
def test_strict_transfer_request(changes):
    with pytest.raises(ValidationError):
        OpportunityTransfer.model_validate(request(**changes))


class Failure:
    def __init__(self, error):
        self.error = error

    async def fetchval(self, *args):
        raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,status,code",
    [
        (asyncpg.InsufficientPrivilegeError("OPPORTUNITY_ACCESS_DENIED"), 403, "OPPORTUNITY_ACCESS_DENIED"),
        (asyncpg.SerializationError("TRANSFER_ATTRIBUTION_UNRESOLVED"), 409, "TRANSFER_ATTRIBUTION_UNRESOLVED"),
        (asyncpg.SerializationError("TRANSFER_STATE_NOT_ALLOWED"), 409, "TRANSFER_STATE_NOT_ALLOWED"),
        (asyncpg.SerializationError("IDEMPOTENCY_CONFLICT"), 409, "IDEMPOTENCY_CONFLICT"),
        (asyncpg.InvalidParameterValueError("INVALID_ACCESS_TARGET"), 422, "INVALID_ACCESS_TARGET"),
    ],
)
async def test_precise_domain_errors(error, status, code):
    with pytest.raises(OpportunityAccessError) as caught:
        await transfer_owner(Failure(error), uuid4(), OpportunityTransfer.model_validate(request()), uuid4())
    assert (caught.value.status, caught.value.code) == (status, code)


@pytest.mark.asyncio
async def test_missing_key_and_infrastructure_failure_are_not_success():
    body = OpportunityTransfer.model_validate(request())
    with pytest.raises(OpportunityAccessError, match="幂等"):
        await transfer_owner(None, uuid4(), body, None)
    with pytest.raises(asyncpg.ConnectionDoesNotExistError):
        await transfer_owner(Failure(asyncpg.ConnectionDoesNotExistError()), uuid4(), body, uuid4())


def test_http_requires_uuid_key_before_database_write():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_identity] = lambda: None
    app.dependency_overrides[get_database] = lambda: None
    with TestClient(app) as client:
        for headers in ({}, {"Idempotency-Key": "invalid"}):
            response = client.post(f"/api/v1/opportunities/{uuid4()}/transfer", json=request(), headers=headers)
            assert response.status_code == 422
