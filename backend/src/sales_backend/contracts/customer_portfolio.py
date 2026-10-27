"""Customer-grain portfolio: independent activity, pipeline and won sets."""

from datetime import date, datetime
from decimal import Decimal
from sales_backend.domain.customer_identity import BusinessIdentityFilter
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CustomerPortfolioQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_identity: BusinessIdentityFilter = "all"
    scope: Literal["self", "team"] = "team"
    team_id: UUID | None = None
    member_id: UUID | None = None
    activity_start: date | None = None
    activity_end: date | None = None
    segment: Literal["claimed", "active", "pipeline", "won"] = "claimed"
    min_probability: Literal[10, 30, 50, 70, 90] = 10
    q: str | None = Field(default=None, max_length=100)
    quadrant: Literal["attack", "asset", "spot", "resource"] | None = None
    level: str | None = Field(default=None, max_length=20)
    plan: Literal["current_year", "long_term"] | None = None
    amount_min: Decimal | None = Field(default=None, ge=0)
    amount_max: Decimal | None = Field(default=None, ge=0)
    unscored: bool = False
    offset: int = Field(default=0, ge=0, le=2_147_483_647)
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("min_probability", mode="before")
    @classmethod
    def probability_from_query(cls, value):
        # Query strings must still validate against the supported thresholds.
        if isinstance(value, str) and value in {"10", "30", "50", "70", "90"}:
            return int(value)
        return value

    @model_validator(mode="after")
    def validate_range(self):
        if self.scope == "self" and (self.team_id or self.member_id):
            raise ValueError("本人视角不能同时指定团队或成员")
        if self.activity_start and self.activity_end and self.activity_start > self.activity_end:
            raise ValueError("活动开始日期不得晚于结束日期")
        if self.amount_min is not None and self.amount_max is not None and self.amount_min > self.amount_max:
            raise ValueError("金额下限不得大于上限")
        if self.unscored and self.quadrant:
            raise ValueError("未评分与具体象限不能同时筛选")
        self.q = (self.q or "").strip() or None
        return self


class PortfolioAvailability(BaseModel):
    activity: bool
    opportunity: bool
    map: bool
    pool: bool


class PortfolioSummary(BaseModel):
    claimed: int
    active: int | None
    pipeline_ge_10: int | None
    pipeline_ge_30: int | None
    pipeline_ge_50: int | None
    pipeline_ge_70: int | None
    pipeline_ge_90: int | None
    won: int | None
    pool_total: int | None
    activity_unavailable_count: int
    opportunity_unavailable_count: int


class CustomerPortfolioItem(BaseModel):
    business_identities: list[str] = Field(default_factory=lambda: ["customer"])
    partner_profile_id: UUID | None = None
    primary_partner_id: UUID | None = None
    customer_type_code: str | None = None
    id: UUID
    name: str
    level_code: str | None = None
    owner_user_ref_id: UUID | None = None
    owner_team_id: UUID | None = None
    owner_name: str | None = None
    team_name: str | None = None
    ownership_state: str | None = None
    is_active: bool | None = None
    latest_visit_at: datetime | None = None
    max_open_probability: Decimal | None = None
    open_count: int | None = None
    has_won: bool | None = None
    amount: Decimal | None = None
    acv_amount: Decimal | None = None
    unknown_amount_count: int | None = None
    plan_segment: Literal["current_year", "long_term"] | None = None
    potential_score: Decimal | None = None
    relationship_score: Decimal | None = None
    quadrant_code: str | None = None
    activity_readable: bool
    opportunity_readable: bool
    map_readable: bool
    can_open: bool = True


class CustomerPortfolioPage(BaseModel):
    as_of: date
    filters_applied: dict
    availability: PortfolioAvailability
    summary: PortfolioSummary
    items: list[CustomerPortfolioItem]
    map_points: list[CustomerPortfolioItem]
    total: int
    unscored_count: int | None
    map_unavailable_count: int
    offset: int
    limit: int
    has_more: bool
