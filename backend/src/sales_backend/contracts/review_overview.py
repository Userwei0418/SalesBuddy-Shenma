"""Additive read-only Review contracts; old report snapshots are unchanged."""

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sales_backend.contracts.work_report_base import PeriodKind, ResolvedPeriod
from sales_backend.contracts.work_report_overview import OverviewResolvedScope

ActivityCode = Literal[
    "new_claimed_customers",
    "formal_visits",
    "new_opportunities",
    "lost_opportunities",
    "updated_opportunities",
    "completed_tasks",
    "pending_tasks",
]
PoolFilter = Literal["all", "not_updated", "stale", "high_risk", "key_collection"]
PoolSort = Literal["last_follow_up_desc", "last_follow_up_asc", "updated_desc"]
Coverage = Literal["complete", "partial", "unavailable"]


class ReviewSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["self", "member", "team"] = "self"
    member_id: UUID | None = None
    team_id: UUID | None = None
    anchor_date: date | None = None
    period_type: PeriodKind = "day"

    @model_validator(mode="after")
    def exact_scope(self):
        if (
            (self.scope == "self" and (self.member_id or self.team_id))
            or (self.scope == "member" and (not self.member_id or self.team_id))
            or (self.scope == "team" and (not self.team_id or self.member_id))
        ):
            raise ValueError("范围和成员/团队标识不匹配")
        return self


class ReviewTarget(BaseModel):
    kind: str
    id: UUID | None = None
    version_no: int | None = None
    value: str | None = None
    note: str | None = None
    status: Literal["effective", "no_target", "permission_denied"]


class ReviewMetric(BaseModel):
    metric_code: Literal["forecast_collection", "collection_ppl", "forecast_recognized"]
    definition_version: int = 1
    unit: Literal["元"] = "元"
    currency: Literal["CNY"] = "CNY"
    tax_basis: Literal["cash_collection_not_applicable", "tax_inclusive"]
    value: str | None
    known_subtotal: str | None
    coverage: Coverage
    missing_amount_count: int = Field(ge=0)
    unknown_eligibility_count: int = Field(ge=0)
    missing_reasons: list[str]
    target: ReviewTarget
    gap: str | None
    rate: str | None
    rate_basis: Literal[
        "effective_target", "known_effective_targets", "known_subtotals", "known_subtotals_and_targets"
    ] | None = None
    status: Literal["expected_achieved", "on_track", "behind", "no_target", "incomplete", "target_unavailable"]
    day_change: str | None = None
    baseline: dict[str, Any]
    source_refs: list[dict[str, Any]]


class ReviewHistoryCoverage(BaseModel):
    status: Coverage
    complete_from: str | None = None
    reason_codes: list[str]
    message: str


class ReviewActivityCount(BaseModel):
    history_coverage: ReviewHistoryCoverage | None = None
    code: ActivityCode
    count: int | None
    known_count: int = Field(ge=0)
    coverage: Coverage
    missing_reasons: list[str]


class ReviewEnvelope(BaseModel):
    selection: ReviewSelection
    scope: OverviewResolvedScope
    period: ResolvedPeriod
    data_as_of: str


class ReviewOverview(ReviewEnvelope):
    contract_version: Literal["review.overview.v1"] = "review.overview.v1"
    quarter: dict[str, Any]
    values_basis: Literal["current_authorized_forecast_snapshot"] = "current_authorized_forecast_snapshot"
    metrics: list[ReviewMetric]
    activities: list[ReviewActivityCount]
    pool_counts: dict[str, int | None]
    filter_availability: dict[str, dict[str, Any]]
    members: list[dict[str, Any]]
    generation: dict[str, Any]


class ReviewPage(ReviewEnvelope):
    history_coverage: ReviewHistoryCoverage | None = None
    contract_version: Literal["review.page.v1"] = "review.page.v1"
    items: list[dict[str, Any]]
    total: int | None
    known_total: int
    coverage: Coverage
    missing_reasons: list[str]
    limit: int
    offset: int
    has_more: bool
    available: bool = True
    activity: ActivityCode | None = None
    filter: PoolFilter | None = None
    sort: PoolSort | None = None
    metric_code: str | None = None
    values_basis: Literal[
        "selected_period_events", "current_pending_inventory", "current_authorized_opportunity_snapshot"
    ]
    filter_availability: dict[str, dict[str, Any]] | None = None
