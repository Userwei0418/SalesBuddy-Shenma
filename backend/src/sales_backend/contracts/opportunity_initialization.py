"""Explicit administrator-selected initialization; no financial or owner writes."""

from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sales_backend.contracts.opportunity_fields import OpportunityBusinessFields, OpportunityPeopleFields


class InitializationValues(OpportunityBusinessFields, OpportunityPeopleFields):
    model_config = ConfigDict(extra="forbid")
    fde_member_ids: list[UUID] | None = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def fills_only(self):
        values = self.model_dump(mode="json", exclude_unset=True)
        if not values or any(v is None or v == [] or v == "" for v in values.values()):
            raise ValueError("初始化仅接受明确非空补充值；清空请使用字段维护")
        if self.fde_member_ids is not None and len(self.fde_member_ids) != len(set(self.fde_member_ids)):
            raise ValueError("长期FDE不能重复")
        return self


class InitializationPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opportunity_ids: list[UUID] = Field(min_length=1, max_length=100)

    @field_validator("opportunity_ids")
    @classmethod
    def unique(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("商机不能重复")
        return value


class InitializationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opportunity_id: UUID
    version_no: int = Field(ge=1, strict=True)
    values: InitializationValues


class InitializationApply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500, strict=True)
    items: list[InitializationItem] = Field(min_length=1, max_length=100)

    @field_validator("reason", mode="before")
    @classmethod
    def trim_reason(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("items")
    @classmethod
    def unique(cls, values):
        if len(values) != len({v.opportunity_id for v in values}):
            raise ValueError("商机不能重复")
        return values
