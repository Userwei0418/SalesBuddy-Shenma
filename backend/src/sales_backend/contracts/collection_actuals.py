"""Quarterly cumulative collection edited with the opportunity; not a forecast."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CollectionActualInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int = Field(ge=2000, le=2100)
    quarter: int = Field(ge=1, le=4)
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    source_ref: str = Field(min_length=1, max_length=160)
    note: str = Field(default="", max_length=1000)
    expected_actual_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    replace_source: bool = False

    @field_validator("source_ref", "note")
    @classmethod
    def trim_text(cls, value, info):
        value = value.strip()
        if info.field_name == "source_ref" and not value:
            raise ValueError("请填写实际回款的登记依据")
        return value


class CollectionActual(BaseModel):
    amount: str | None
    status: Literal[
        "missing", "single_source", "confirmed", "needs_confirmation", "stale_confirmation", "partial", "withdrawn"
    ]
    source: Literal["entries", "historical", "operations"] | None
    revision: str
    source_ref: str
    note: str
    requires_source_replacement: bool


class CollectionActualPeriod(BaseModel):
    year: int
    quarter: int


class CollectionActualResult(BaseModel):
    opportunity_id: str
    period: CollectionActualPeriod
    can_edit: bool
    actual: CollectionActual
