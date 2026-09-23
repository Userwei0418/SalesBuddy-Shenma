"""Authenticated administrator probes of loaded bindings only."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from sales_backend.api.dependencies import get_database, get_settings
from sales_backend.api.management_dependencies import get_system_identity
from sales_backend.api.model_api import PrivateValidationRoute
from sales_backend.services.connectivity import ConnectivityService

router = APIRouter(prefix="/api/v1/admin/ai-connectivity", tags=["AI connectivity"], route_class=PrivateValidationRoute)


class ProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


def manager(database=Depends(get_database), settings=Depends(get_settings)):
    return ConnectivityService(database, settings)


@router.post("/{kind}/{target}/tests")
async def test_current_connection(
    kind: Literal["direct", "agent"],
    target: str,
    body: ProbeRequest,
    identity=Depends(get_system_identity),
    service=Depends(manager),
):
    return await service.run(identity.actor, kind, target, body.request_id)


@router.get("/tests/{request_id}")
async def read_connection_test(request_id: UUID, identity=Depends(get_system_identity), service=Depends(manager)):
    return await service.get(identity.actor, request_id)
