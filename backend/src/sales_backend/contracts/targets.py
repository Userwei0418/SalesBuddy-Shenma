from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TargetPeriod = Literal["week", "month", "quarter", "year"]
TargetScope = Literal["self", "person", "team", "department"]
TargetKind = Literal["collection", "recognized", "acv", "opportunity_count", "visit_count", "demo_count"]
TargetDepartment = Literal["sales", "fde"]


class TargetSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: TargetScope = "self"
    user_id: UUID | None = None
    team_id: UUID | None = None
    period_type: TargetPeriod = "year"
    anchor_date: date
    kind: TargetKind
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    version_no: int | None = Field(default=None, ge=1, le=2147483647)
    reason: str = Field(min_length=1, max_length=2000)
    department_code: TargetDepartment = "sales"

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value):
        if not value.strip():
            raise ValueError("请填写目标设置或调整原因")
        return value.strip()

    @model_validator(mode="after")
    def count_and_scope(self):
        if self.scope != "department" and self.department_code != "sales":
            raise ValueError("部门类别只用于部门目标")
        if self.kind.endswith("_count") and self.amount != self.amount.to_integral_value():
            raise ValueError("数量目标必须为正整数")
        if not 2000 <= self.anchor_date.year <= 2100:
            raise ValueError("目标年份应在2000至2100之间")
        if self.scope == "person" and self.user_id is None:
            raise ValueError("请选择目标所属人员")
        if self.scope == "team" and self.team_id is None:
            raise ValueError("请选择目标所属团队")
        if self.scope in {"self", "department"} and (self.user_id or self.team_id):
            raise ValueError("当前目标范围不接受人员或部门参数")
        if self.scope == "person" and self.team_id or self.scope == "team" and self.user_id:
            raise ValueError("个人目标与部门目标不可混用")
        return self


class TargetItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: TargetKind
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    version_no: int | None = Field(default=None, ge=1, le=2147483647)

    @model_validator(mode="after")
    def integer_counts(self):
        if self.kind.endswith("_count") and self.amount != self.amount.to_integral_value():
            raise ValueError("数量目标必须为正整数")
        return self


class TargetBatchSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: TargetScope = "self"
    user_id: UUID | None = None
    team_id: UUID | None = None
    department_code: TargetDepartment = "sales"
    period_type: TargetPeriod = "quarter"
    anchor_date: date
    reason: str = Field(min_length=1, max_length=2000)
    items: list[TargetItem] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_batch(self):
        kinds = [item.kind for item in self.items]
        if len(set(kinds)) != len(kinds):
            raise ValueError("每种目标指标只能填写一次")
        # Keep the same strict scope and value contract for one item or a whole form.
        first = self.items[0]
        checked = TargetSave(
            scope=self.scope, user_id=self.user_id, team_id=self.team_id,
            department_code=self.department_code, period_type=self.period_type,
            anchor_date=self.anchor_date, reason=self.reason, **first.model_dump(),
        )
        self.reason = checked.reason
        return self


class TargetDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approved", "rejected"]
    reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def rejection_reason(self):
        if self.decision == "rejected" and not self.reason.strip():
            raise ValueError("驳回必须填写原因")
        return self
