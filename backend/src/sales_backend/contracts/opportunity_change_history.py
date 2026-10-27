"""Bounded display contract; saved change JSON is never a public response."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class HistoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HistoryRow(HistoryModel):
    name: str | None = Field(max_length=5000)
    id: str | None = Field(max_length=128)
    missing_name: bool
    id_label: str | None = Field(max_length=80)
    detail: str | None = Field(max_length=4000)


class HistoryValue(HistoryModel):
    kind: Literal["scalar", "list", "people", "materials", "unavailable"]
    status: Literal["recorded", "empty", "unavailable", "restricted"]
    rows: list[HistoryRow] = Field(max_length=100)
    empty_label: str = Field(max_length=200)
    truncated: bool


class HistoryField(HistoryModel):
    field: str = Field(max_length=80)
    label: str = Field(max_length=120)
    before: HistoryValue
    after: HistoryValue


class HistoryActor(HistoryModel):
    id: UUID | None
    name: str | None = Field(max_length=200)
    name_status: Literal["recorded", "current", "unavailable"]


class HistoryEvent(HistoryModel):
    id: UUID
    version_no: int = Field(ge=1)
    created_at: datetime
    actor: HistoryActor
    reason: str | None = Field(max_length=1000)
    fields: list[HistoryField] = Field(max_length=200)
    detail_status: Literal["recorded", "partial", "unavailable"]
    unavailable_field_count: int = Field(ge=0)


class OpportunityChangeHistoryPage(HistoryModel):
    items: list[HistoryEvent] = Field(max_length=100)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=100000)
    has_more: bool
    next_offset: int | None = Field(ge=0)
    order: Literal["created_at_desc_id_desc"]
    coverage_note: str = Field(max_length=1000)


class OpportunityChangeHistory(HistoryModel):
    schema_version: Literal[1]
    opportunity_id: UUID
    history_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    changes: OpportunityChangeHistoryPage
