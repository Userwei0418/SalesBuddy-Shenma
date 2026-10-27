"""Quarterly cumulative net recognition on existing saved material lines."""

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sales_backend.contracts.collection_actuals import CollectionActualPeriod


class RecognizedActualEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material_line_id: UUID
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    source_ref: str = Field(min_length=1, max_length=160)
    note: str = Field(default="", max_length=1000)

    @field_validator("source_ref", "note")
    @classmethod
    def trim_text(cls, value, info):
        value = value.strip()
        if info.field_name == "source_ref" and not value:
            raise ValueError("请填写实际确收的登记依据")
        return value


class RecognizedActualInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int = Field(ge=2000, le=2100)
    quarter: int = Field(ge=1, le=4)
    expected_actual_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    replace_source: bool = False
    entries: list[RecognizedActualEntry] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_lines(self):
        ids = [item.material_line_id for item in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("同一季度同一物料只能填写一次累计确收")
        return self


class RecognizedActual(BaseModel):
    amount: str | None
    known_subtotal: str | None
    status: Literal[
        "missing", "single_source", "confirmed", "needs_confirmation", "stale_confirmation", "partial", "withdrawn"
    ]
    source: Literal["entries", "historical", "operations"] | None
    revision: str
    requires_source_replacement: bool
    attribution_status: Literal["frozen", "unresolved", "not_registered"]
    missing_material_count: int


class RecognizedActualLine(BaseModel):
    material_line_id: UUID
    material_code: str
    material_name: str
    is_active: bool
    eligible: bool
    amount: str | None
    status: Literal["missing", "confirmed", "withdrawn"]
    source_ref: str
    note: str


class RecognizedActualResult(BaseModel):
    opportunity_id: str
    period: CollectionActualPeriod
    can_edit: bool
    actual: RecognizedActual
    items: list[RecognizedActualLine]
    editable_reason: str | None
