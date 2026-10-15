"""Optional WEB005 facts; omission survives nested visit-review serialization."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_serializer, model_validator

CustomerSource = Literal[
    "company_lead", "partner_existing", "marketing_event", "customer_referral",
    "partner_stock", "digital_marketing", "sales_outbound",
]
TrialStatus = Literal["preparing", "requested", "enabled"]
PplStatus = Literal["created", "not_created"]
ExternalStatus = Literal["commit", "high", "medium", "low"]
SupportStage = Literal["trial_support", "demo_build", "project_delivery"]
ParticipantRole = Literal["sales", "fde", "other"]

SCALAR_FIELDS = frozenset({
    "customer_source", "priority", "is_key", "trial_status", "summary", "note",
    "ppl_status", "external_system_status", "fde_support_stage", "fde_progress",
})
NEW_FIELDS = SCALAR_FIELDS | {"primary_fde_id", "participants", "fields_schema_version", "intent_product_line_ids"}
PEOPLE_FIELDS = frozenset({"primary_fde_id", "fde_member_ids", "participants"})
INLINE_CORE_FIELDS = frozenset({"name", "product_line", "follow_up_plan"})


class ProjectParticipant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    role: ParticipantRole


class OpportunityBusinessFields(BaseModel):
    """Shared by full saves and inline input, never used as a read projection."""

    customer_source: list[CustomerSource] | None = Field(default=None, max_length=7)
    priority: Literal["p0"] | None = None
    is_key: StrictBool | None = None
    trial_status: TrialStatus | None = None
    summary: str | None = Field(default=None, max_length=500, strict=True)
    note: str | None = Field(default=None, max_length=5000, strict=True)
    ppl_status: PplStatus | None = None
    external_system_status: ExternalStatus | None = None
    fde_support_stage: SupportStage | None = None
    fde_progress: str | None = Field(default=None, max_length=5000, strict=True)
    intent_product_line_ids: list[UUID] | None = Field(default=None, max_length=100)

    @field_validator("intent_product_line_ids")
    @classmethod
    def unique_intent_lines(cls, values):
        if values is None:
            raise ValueError("意向产品线清空请提交空数组，不能提交null")
        if len(values) != len(set(values)):
            raise ValueError("意向产品线不能重复")
        return values

    @field_validator("summary", "note", "fde_progress", mode="before")
    @classmethod
    def normalize_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("customer_source")
    @classmethod
    def source_set(cls, values):
        # The default None is internal only. An explicit null cannot mean both
        # preserve and clear; [] is the single explicit-clear representation.
        if values is None:
            raise ValueError("客户来源清空请提交空数组，不能提交null")
        if len(values) != len(set(values)):
            raise ValueError("客户来源不能重复")
        return values

    @model_serializer(mode="wrap")
    def preserve_omission(self, handler):
        result = handler(self)
        for key in (NEW_FIELDS | {"product_line"}) - self.model_fields_set:
            result.pop(key, None)
        return result


class OpportunityPeopleFields(BaseModel):
    primary_fde_id: UUID | None = None
    participants: list[ProjectParticipant] | None = Field(default=None, max_length=30)

    @field_validator("participants")
    @classmethod
    def unique_people(cls, values):
        if values is None:
            raise ValueError("项目参与者清空请提交空数组，不能提交null")
        if len(values) != len({p.user_id for p in values}):
            raise ValueError("同一项目参与者不能重复")
        return values


class OpportunityFieldValues(OpportunityBusinessFields):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200, strict=True)
    product_line: str | None = Field(default=None, max_length=50000, strict=True)
    follow_up_plan: str | None = Field(default=None, max_length=5000, strict=True)

    @field_validator("name", "product_line", "follow_up_plan", mode="before")
    @classmethod
    def trimmed_core(cls, value, info):
        value = value.strip() or None if isinstance(value, str) else value
        if info.field_name == "name" and value is None:
            raise ValueError("商机名称不能为空")
        return value

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set:
            raise ValueError("至少提交一个要修改的字段")
        return self


class OpportunityFieldPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_no: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=500, strict=True)
    values: OpportunityFieldValues

    @field_validator("reason", mode="before")
    @classmethod
    def trim_reason(cls, value):
        return value.strip() if isinstance(value, str) else value


class OpportunityPeoplePatch(OpportunityPeopleFields):
    model_config = ConfigDict(extra="forbid")
    version_no: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=500, strict=True)
    fde_member_ids: list[UUID] | None = Field(default=None, max_length=30)

    _trim_reason = field_validator("reason", mode="before")(OpportunityFieldPatch.trim_reason.__func__)

    @model_validator(mode="after")
    def explicit_change(self):
        if not self.model_fields_set & PEOPLE_FIELDS:
            raise ValueError("至少提交一种人员关系")
        if 'fde_member_ids' in self.model_fields_set and self.fde_member_ids is None:
            raise ValueError("长期FDE清空请提交空数组，不能提交null")
        if self.fde_member_ids is not None and len(self.fde_member_ids) != len(set(self.fde_member_ids)):
            raise ValueError("FDE人员不能重复")
        return self


def explicit_business_values(data: dict[str, Any]) -> dict[str, Any]:
    """Whitelist for SQL/service adapters; false, null and [] must all survive."""
    return {key: value for key, value in data.items() if key in NEW_FIELDS}
