"""V259 transfer intent and bounded response, separate from ordinary editing."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sales_backend.contracts.opportunity_access import OpportunityCollaborator, OpportunityOwner


class OpportunityTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_no: int = Field(ge=1, strict=True)
    new_owner_user_ref_id: UUID
    new_owner_team_id: UUID
    reason: str = Field(min_length=1, max_length=1000, strict=True)

    @field_validator("reason", mode="before")
    @classmethod
    def trim_reason(cls, value):
        return value.strip() if isinstance(value, str) else value


class OpportunityTransferResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changed: bool
    event_id: UUID | None
    version_no: int = Field(ge=1, strict=True)
    owner: OpportunityOwner
    collaborators: list[OpportunityCollaborator]
    sync_status: Literal["pending", "disabled", "not_configured", "unchanged"]
