"""Explicit ownership and collaboration intents; never ordinary opportunity edits."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AccessMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_no: int = Field(ge=1, strict=True)


class OpportunityTransfer(AccessMutation):
    new_owner_user_ref_id: UUID
    new_owner_team_id: UUID
    reason: str = Field(min_length=1, max_length=1000, strict=True)

    @field_validator("reason", mode="before")
    @classmethod
    def trim_reason(cls, value):
        return value.strip() if isinstance(value, str) else value


class OpportunityCollaboratorsUpdate(AccessMutation):
    member_ids: list[UUID] = Field(max_length=30)

    @field_validator("member_ids")
    @classmethod
    def distinct_members(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("协作人不能重复")
        # Membership is a set: reordering a retry must not change its intent.
        return sorted(values)


class AccessProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomerReference(AccessProjection):
    id: UUID
    name: str


class CompanyCustomerFollowUpReference(AccessProjection):
    customer_reference: CustomerReference
    can_follow_up: bool = Field(strict=True)


class CompanyOpportunityFollowUpReference(AccessProjection):
    """Recording reference only; conveys no opportunity detail/edit authority."""

    opportunity_id: UUID
    name: str
    customer_reference: CustomerReference
    can_follow_up: bool = Field(strict=True)


class CompanyOpportunityFollowUpOptions(AccessProjection):
    items: list[CompanyOpportunityFollowUpReference]
    total: int = Field(ge=0, strict=True)
    limit: int = Field(ge=1, le=100, strict=True)
    offset: int = Field(ge=0, strict=True)


class CompanyFollowUpCustomers(AccessProjection):
    items: list[CustomerReference]
    total: int = Field(ge=0, strict=True)
    limit: int = Field(ge=1, le=100, strict=True)
    offset: int = Field(ge=0, strict=True)


class OpportunityOwner(AccessProjection):
    # Legacy unresolved attribution remains explicit, never inferred from customer.
    user_id: UUID | None
    display_name: str | None
    team_id: UUID | None
    team_name: str | None


class OpportunityCollaborator(AccessProjection):
    user_id: UUID
    display_name: str


class OpportunityCapabilities(AccessProjection):
    can_transfer: bool = Field(strict=True)
    can_manage_collaborators: bool = Field(strict=True, deprecated=True,
        description="弃用兼容字段，固定为false；旧商机协作名单设置已停用。")
    can_follow_up: bool = Field(strict=True)
    can_edit: bool = Field(strict=True)


class OpportunityCapabilityReasons(AccessProjection):
    transfer: Literal[
        "OPPORTUNITY_ACCESS_DENIED", "TRANSFER_STATE_NOT_ALLOWED", "TRANSFER_ATTRIBUTION_UNRESOLVED",
        "TRANSFER_NOT_READY",
    ] | None


class OpportunityFollowUpOption(AccessProjection):
    contract_version: Literal[1] = 1
    opportunity_id: UUID
    name: str
    stage_code: str | None
    status: str
    version_no: int = Field(ge=1, strict=True)
    owner: OpportunityOwner
    customer_reference: CustomerReference
    capabilities: OpportunityCapabilities
    capability_reasons: OpportunityCapabilityReasons


class OpportunityAccessResponse(OpportunityFollowUpOption):
    collaborators: list[OpportunityCollaborator] = Field(deprecated=True,
        description="历史协作名单的兼容只读字段，保留已有数据和可见性，不再提供名单设置。")


class OpportunityFollowUpOptions(AccessProjection):
    contract_version: Literal[1] = 1
    items: list[OpportunityFollowUpOption]
    total: int = Field(ge=0, strict=True)
    limit: int = Field(ge=1, le=100, strict=True)
    offset: int = Field(ge=0, strict=True)


class OpportunityCollaboratorsResult(AccessProjection):
    changed: bool
    event_id: UUID | None
    version_no: int = Field(ge=1, strict=True)
    collaborators: list[OpportunityCollaborator]


class AccessCandidateTeam(AccessProjection):
    team_id: UUID
    name: str


class AccessCandidate(AccessProjection):
    user_id: UUID
    display_name: str
    teams: list[AccessCandidateTeam]


class AccessCandidates(AccessProjection):
    items: list[AccessCandidate]
    total: int = Field(ge=0, strict=True)
