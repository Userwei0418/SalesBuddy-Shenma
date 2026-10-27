"""Read-only risk filters narrow the existing row-level authorization."""

from typing import Literal

from pydantic import BaseModel, model_validator

from sales_backend.contracts.models import PageResponse
from sales_backend.contracts.types import UUIDString


class RiskBrowseQuery(BaseModel):
    scope: Literal["all", "self", "person", "team"] = "all"
    member_id: UUIDString | None = None
    team_id: UUIDString | None = None
    opportunity_only: bool = False

    @model_validator(mode="after")
    def consistent_scope(self):
        if self.scope == "self" and (self.member_id or self.team_id):
            raise ValueError("本人范围不接受其他成员或团队条件")
        if self.scope == "person" and not self.member_id:
            raise ValueError("指定成员范围需要 member_id")
        if self.scope == "team" and not self.team_id:
            raise ValueError("指定团队范围需要 team_id")
        return self


class RiskBrowseSummary(BaseModel):
    total: int
    open_count: int
    resolved_count: int
    overdue_count: int


class RiskBrowsePage(PageResponse):
    total: int
    summary: RiskBrowseSummary
    offset: int
    page_size: int
    has_more: bool
    next_offset: int | None
