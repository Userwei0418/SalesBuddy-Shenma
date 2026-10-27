"""Lightweight O/KR contracts; target money is never accepted in this API."""

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sales_backend.contracts.targets import TargetPeriod


class Participants(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    owner_user_id: UUID
    collaborator_user_ids: list[UUID] = Field(default_factory=list, max_length=50)
    parent_id: UUID | None = None

    @field_validator("title")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("标题不能为空")
        return value.strip()

    @model_validator(mode="after")
    def participants(self):
        self.collaborator_user_ids = sorted(set(self.collaborator_user_ids) - {self.owner_user_id}, key=str)
        return self


class ObjectiveCreate(Participants):
    period_type: TargetPeriod = "quarter"
    anchor_date: date
    scope_type: Literal["person", "team", "department"]
    user_id: UUID | None = None
    team_id: UUID | None = None
    department_code: Literal["sales", "fde"] = "sales"

    @model_validator(mode="after")
    def valid_scope(self):
        valid = (
            (self.scope_type == "person" and self.user_id is not None and self.team_id is None)
            or (self.scope_type == "team" and self.team_id is not None and self.user_id is None)
            or (self.scope_type == "department" and self.user_id is None and self.team_id is None)
        )
        if not valid or (self.scope_type != "department" and self.department_code != "sales"):
            raise ValueError("请选择有效的目标归属")
        return self


class ObjectiveUpdate(Participants):
    """Full mutable representation. Period and scope cannot drift after linking."""

    version_no: int = Field(ge=1, le=2147483647)
    status: Literal["active", "archived"] = "active"


class KeyResultCreate(Participants):
    objective_id: UUID
    metric_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    target_id: UUID | None = None


class KeyResultUpdate(ObjectiveUpdate):
    pass


class ManualProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_no: int = Field(ge=1, le=2147483647)
    value: Decimal = Field(ge=0, lt=Decimal("10000000000000000"), max_digits=18, decimal_places=2)
    evidence: str = Field(min_length=1, max_length=2000)
    confirmed: Literal[True]

    @field_validator("evidence")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请填写可复核的进展证据")
        return value.strip()


class OkrMention(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_no: int = Field(ge=1, le=2147483647)
    recipient_user_ids: list[UUID] = Field(min_length=1, max_length=20)

    @field_validator("recipient_user_ids")
    @classmethod
    def deduplicate(cls, value):
        return sorted(set(value), key=str)
