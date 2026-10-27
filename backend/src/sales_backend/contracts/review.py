"""Additive Review contracts; legacy target/task bodies remain unchanged."""

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sales_backend.contracts.targets import TargetKind, TargetSave


class ReviewTargetSave(TargetSave):
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")

    @model_validator(mode="after")
    def count_and_scope(self):
        # Reuse the legacy scope checks. Only its six known kinds carry the
        # legacy precision rule; custom precision comes from the dictionary.
        data = self.model_dump()
        if self.kind not in get_args(TargetKind):
            if self.amount <= 0:
                raise ValueError("自定义指标目标须为正数")
            data["kind"] = "collection"
        TargetSave.model_validate(data)
        return self


class ReviewMetricCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^custom_[a-z0-9_]{1,57}$")
    name: str = Field(min_length=1, max_length=80)
    unit: str = Field(min_length=1, max_length=20)
    decimal_places: Literal[0, 2]
    definition: str = Field(min_length=1, max_length=2000)
    active: bool = True

    @field_validator("name", "unit", "definition")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("指标名称、单位和口径不能为空")
        return value.strip()


class ReviewMetricUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool
    version_no: int = Field(ge=1, le=2147483647)
