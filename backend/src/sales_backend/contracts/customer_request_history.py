"""Bounded customer request summaries; shared history never includes form contacts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

RequestStatus = Literal["pending", "approved", "rejected", "cancelled"]


class CustomerRequestHistoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["self", "team"] = "self"
    team_id: UUID | None = None
    member_id: UUID | None = None
    kind: Literal["creation", "claim"] | None = None
    status: Literal["all", "pending", "approved", "rejected", "cancelled"] | None = None
    q: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=2_147_483_647)

    @model_validator(mode="after")
    def require_exact_scope(self):
        if self.scope == "self" and (self.team_id is not None or self.member_id is not None):
            raise ValueError("本人历史不能附带团队或成员筛选")
        self.q = (self.q or "").strip() or None
        if self.status == "all":
            self.status = None
        return self


class CustomerRequestHistoryItem(BaseModel):
    id: UUID
    kind: Literal["creation", "claim"]
    customer_id: UUID | None = None
    customer_name: str
    applicant_user_ref_id: UUID
    applicant_name: str
    status: RequestStatus
    requested_at: datetime
    reviewed_at: datetime | None = None
    reviewer_user_ref_id: UUID | None = None
    reviewer_name: str | None = None
    decision_reason: str = ""
    can_read_customer: bool = False


class CustomerRequestHistorySummary(BaseModel):
    total: int = 0
    pending: int = 0
    approved: int = 0
    rejected: int = 0
    cancelled: int = 0


class CustomerRequestHistoryPage(BaseModel):
    items: list[CustomerRequestHistoryItem]
    total: int
    summary: CustomerRequestHistorySummary = Field(
        description="按范围、类型和关键词统计所有状态；不随 status 筛选改变，绝不按分页估算",
    )
    scope: Literal["self", "team"]
    team_id: UUID | None = None
    member_id: UUID | None = None
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None = None
