"""Read-only creation history; unknown source facts remain nullable."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class HistoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["self", "team"] = "self"
    team_id: UUID | None = None
    member_id: UUID | None = None
    q: str = Field(default="", max_length=100)
    created_from: date | None = None
    created_to: date | None = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @field_validator("q")
    @classmethod
    def trim_query(cls, value):
        return value.strip()

    @model_validator(mode="after")
    def consistent_filters(self):
        if self.scope == "self" and (self.team_id is not None or self.member_id is not None):
            raise ValueError("本人范围不能同时指定部门或成员")
        if self.created_from and self.created_to and self.created_from > self.created_to:
            raise ValueError("创建日期起始不能晚于结束")
        return self


class HistoryOptionsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: UUID | None = None
    q: str = Field(default="", max_length=100)
    limit: int = Field(default=100, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class HistoryItem(BaseModel):
    id: UUID
    customer_id: UUID
    name: str
    customer_name: str | None
    creator_id: UUID | None
    creator_name: str | None
    business_created_on: date | None
    created_at: datetime | None
    owner_name: str | None
    status: str
    stage: str
    can_read_customer: bool


class HistoryPage(BaseModel):
    items: list[HistoryItem]
    total: int
    has_more: bool
    next_offset: int | None


class HistoryTeam(BaseModel):
    id: UUID
    name: str


class HistoryMember(BaseModel):
    id: UUID
    name: str
    is_active: bool
    team_ids: list[UUID]


class HistoryOptions(BaseModel):
    teams: list[HistoryTeam]
    members: list[HistoryMember]
    total: int
    has_more: bool
    next_offset: int | None
    default_scope: Literal["self"] = "self"
    team_basis: Literal["current_creator_membership"] = "current_creator_membership"
