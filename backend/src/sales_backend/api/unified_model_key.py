"""The only customer-deployment Key form; validation never echoes its body."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from sales_backend.api.dependencies import get_settings
from sales_backend.api.management_dependencies import get_system_identity
from sales_backend.api.model_api import PrivateValidationRoute
from sales_backend.services.unified_model_key import UnifiedModelKeyService


class RotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr
    expected_revision: int = Field(ge=1)

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, value):
        key = value.get_secret_value()
        if not key.startswith("sk-") or not 16 <= len(key) <= 512 or any(not 33 <= ord(char) <= 126 for char in key):
            raise ValueError("invalid key")
        return value


router = APIRouter(prefix="/api/v1/admin/unified-model-key", route_class=PrivateValidationRoute,
                   tags=["Unified model credential"])


def service(settings=Depends(get_settings)):
    return UnifiedModelKeyService(settings)


@router.get("")
async def key_status(identity=Depends(get_system_identity), manager=Depends(service)):
    return manager.status(identity.actor)


@router.post("")
async def rotate_key(body: RotationRequest, identity=Depends(get_system_identity), manager=Depends(service)):
    return await manager.rotate(identity.actor, body.api_key.get_secret_value(), body.expected_revision)
