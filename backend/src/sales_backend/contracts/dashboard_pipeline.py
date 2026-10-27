"""Read-only PPL stage and quarterly opportunity follow-up projections."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from sales_backend.contracts.dashboard_metrics import DashboardMetricEnvelope
from sales_backend.contracts.review_overview import Coverage


class PipelineAmount(BaseModel):
    eligible_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    unknown_eligibility_count: int = Field(ge=0)
    value: str | None
    known_subtotal: str | None
    known_amount_count: int = Field(ge=0)
    missing_amount_count: int = Field(ge=0)
    coverage: Coverage
    missing_reasons: list[str]
    unit: Literal["元"] = "元"
    currency: Literal["CNY"] = "CNY"
    tax_basis: Literal["cash_collection_not_applicable", "tax_inclusive"]


class PipelineStage(BaseModel):
    code: Literal["30", "50", "70", "90", "100", "unknown"]
    label: str
    probability: int | None
    count: int = Field(ge=0)
    metrics: dict[str, PipelineAmount]


class DashboardPipeline(DashboardMetricEnvelope):
    contract_version: Literal["dashboard.pipeline.v1"] = "dashboard.pipeline.v1"
    basis: Literal["latest_sales_forecast"] = "latest_sales_forecast"
    coverage: Coverage
    missing_reasons: list[str]
    eligible_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    unknown_eligibility_count: int = Field(ge=0)
    stages: list[PipelineStage]


class ActivityWindow(BaseModel):
    start: str
    end_exclusive: str
    as_of: str
    days: Literal[7] = 7
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"


class DashboardActivity(DashboardMetricEnvelope):
    contract_version: Literal["dashboard.activity.v1"] = "dashboard.activity.v1"
    basis: Literal["quarter_positive_sales_forecast"]
    coverage: Coverage
    missing_reasons: list[str]
    window: ActivityWindow
    active_count: int = Field(ge=0)
    recent_count: int = Field(ge=0)
    overdue_count: int = Field(ge=0)
    never_count: int = Field(ge=0)
    unknown_count: int = Field(ge=0)


class ActivityOpportunity(BaseModel):
    id: UUID
    name: str
    customer_id: UUID
    customer_name: str
    owner_id: UUID | None
    owner_name: str | None
    team_id: UUID | None
    stage_code: str | None
    probability: str | None
    status: str
    last_follow_up_at: str | None
    days_since_follow_up: int | None
    follow_up_status: Literal["overdue", "never"]
    collection_amount: str | None
    recognized_amount: str | None
    currency: str | None
    amount_reason: Literal["currency_unavailable"] | None
    can_read: Literal[True] = True


class DashboardActivityPage(DashboardMetricEnvelope):
    contract_version: Literal["dashboard.activity_page.v1"] = "dashboard.activity_page.v1"
    basis: Literal["quarter_positive_sales_forecast"]
    window: ActivityWindow
    items: list[ActivityOpportunity]
    total: int | None
    known_total: int = Field(ge=0)
    coverage: Coverage
    missing_reasons: list[str]
    limit: int
    offset: int
    has_more: bool
