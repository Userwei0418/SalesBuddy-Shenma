"""Optional explanation of a backend-computed reference, never actual amounts."""
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sales_backend.contracts.materials import MaterialPreviewInput


class MaterialReferenceExplanationInput(MaterialPreviewInput):
    user_text: str = Field(default='', max_length=1000, strict=True)


class Projection(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ReferenceProvenance(Projection):
    source: Literal['authenticated_backend']
    reference_kind: Literal['sales_forecast_reference']
    opportunity_id: UUID
    opportunity_version: int = Field(ge=1)
    material_versions: dict[str, int]
    period: MaterialPreviewInput
    calculator_version: str
    reference_fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')


class ReferenceExplanation(Projection):
    status: Literal['available', 'unavailable']
    provider: Literal['agent_platform'] | None = None
    result: dict[str, Any] | None = None
    reason_code: str | None = None


class MaterialReferenceExplanationView(Projection):
    preview: dict[str, Any]
    provenance: ReferenceProvenance
    explanation: ReferenceExplanation
