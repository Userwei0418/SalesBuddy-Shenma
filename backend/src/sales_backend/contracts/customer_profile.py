"""Operations-only customer master edits; identity/import links stay read-only."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CustomerProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version_no: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    industry: str | None = Field(default=None, max_length=100)
    customer_type: str | None = Field(default=None, max_length=100)
    level_code: Literal["Tier-1", "Tier-2", "Tier-3", ""] | None = None
    source: str | None = Field(default=None, max_length=100)
    partner_name: str | None = Field(default=None, max_length=200)
    operation_type: str | None = Field(default=None, max_length=200)
    cooperation_years: Decimal | None = Field(default=None, ge=0, max_digits=8, decimal_places=1)
    main_business: str | None = Field(default=None, max_length=5000)
    customer_budget: str | None = Field(default=None, max_length=2000)
    demand_summary: str | None = Field(default=None, max_length=5000)
    next_action: str | None = Field(default=None, max_length=5000)
    contact_name: str | None = Field(default=None, min_length=1, max_length=100)
    contact_title: str | None = Field(default=None, max_length=100)
    contact_role: Literal["使用者", "影响者", "决策者", ""] | None = None
    contact_phone: str | None = Field(default=None, max_length=80)
    contact_email: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def explicit_values(self):
        for field in self.model_fields_set - {"cooperation_years"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} 不接受 null；清空可选文本请传空字符串")
        if self.model_fields_set == {"version_no"}:
            raise ValueError("请至少修改一项客户信息")
        return self
