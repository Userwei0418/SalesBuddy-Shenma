"""Quarterly monitoring under the existing dashboard selector and authority."""

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from sales_backend.contracts.review_overview import Coverage, ReviewMetric, ReviewTarget

ForecastMetricCode = Literal["forecast_collection", "collection_ppl", "forecast_recognized"]
ActualMetricCode = Literal["actual_recognized_exclusive", "actual_collection"]
MetricCode = Literal[
    "forecast_collection", "collection_ppl", "forecast_recognized",
    "actual_recognized_exclusive", "actual_collection",
]


class DashboardMetricSelection(BaseModel):
    personal: bool
    member_id: UUID | None
    team_groups: list[str]


class DashboardMetricScope(BaseModel):
    kind: Literal["self", "member", "team", "authorized"]
    label: str


class DashboardMetricQuarter(BaseModel):
    year: int
    quarter: int
    start: str
    end_exclusive: str
    time_progress: str
    basis: Literal["current_calendar_day_inclusive"] = "current_calendar_day_inclusive"
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"


class DashboardMetricTarget(ReviewTarget):
    coverage: Coverage
    known_subtotal: str | None
    aggregation: Literal["direct_effective_target", "sum_distinct_subject_targets"]
    components: list[dict[str, Any]] = Field(default_factory=list)


class DashboardMetric(ReviewMetric):
    # Dashboard extends its own schema; the shared Review contract stays intact.
    metric_code: MetricCode
    tax_basis: Literal["cash_collection_not_applicable", "tax_inclusive", "tax_exclusive"]
    basis: Literal["latest_sales_forecast", "adopted_actuals"]
    target_basis: str
    target: DashboardMetricTarget
    registered_period_count: int = Field(default=0, ge=0)
    registered_fact_count: int = Field(default=0, ge=0)
    incomplete_period_count: int = Field(default=0, ge=0)
    missing_material_count: int = Field(default=0, ge=0)
    tax_unknown_period_count: int = Field(default=0, ge=0)
    conflict_period_count: int = Field(default=0, ge=0)
    attribution_unknown_period_count: int = Field(default=0, ge=0)
    coverage_note: str = ""


class DashboardMetricEnvelope(BaseModel):
    data_as_of: str
    selection: DashboardMetricSelection
    scope: DashboardMetricScope
    quarter: DashboardMetricQuarter
    values_basis: Literal["current_authorized_quarter_snapshot"] = "current_authorized_quarter_snapshot"


class DashboardMetrics(DashboardMetricEnvelope):
    contract_version: Literal["dashboard.metrics.v1"] = "dashboard.metrics.v1"
    metrics: list[DashboardMetric]


class DashboardMetricItem(BaseModel):
    id: UUID
    name: str
    customer_id: UUID
    customer_name: str
    owner_id: UUID | None
    owner_name: str | None
    team_id: UUID | None
    status: str
    probability: str | None
    amount: str | None
    currency: str | None
    forecast_id: UUID | None
    collection_confidence: str | None
    collection_amount: str | None
    recognized_amount: str | None
    metric_contributions: dict[str, dict[str, Any]]


class DashboardMetricPage(DashboardMetricEnvelope):
    contract_version: Literal["dashboard.metric_page.v1"] = "dashboard.metric_page.v1"
    metric_code: ForecastMetricCode
    items: list[DashboardMetricItem]
    total: int | None
    known_total: int
    coverage: Coverage
    missing_reasons: list[str]
    limit: int
    offset: int
    has_more: bool


class DashboardActualItem(BaseModel):
    id: str  # Effective-period placeholders are stable strings, not UUIDs.
    customer_id: UUID
    customer_name: str
    opportunity_id: UUID | None
    opportunity_name: str | None
    material_line_id: UUID | None
    year: int
    quarter: int
    kind: Literal["recognized", "collection"]
    amount: str | None
    raw_amount: str | None
    tax_basis: str
    tax_unresolved: bool
    source: Literal["entries", "historical", "operations"] | None
    status: Literal[
        "missing", "single_source", "confirmed", "needs_confirmation", "stale_confirmation", "partial", "withdrawn"
    ]
    occurred_on: date | None
    period_start: date
    period_end: date
    period_precision: Literal["day", "quarter"]
    owner_id: UUID | None
    owner_name: str | None
    team_id: UUID | None
    complete: bool
    missing_material_count: int
    version_no: int
    attribution_status: Literal["frozen", "unresolved"] = "frozen"


class DashboardActualPage(DashboardMetricEnvelope):
    contract_version: Literal["dashboard.actual_page.v1"] = "dashboard.actual_page.v1"
    basis: Literal["adopted_actuals"] = "adopted_actuals"
    metric_code: ActualMetricCode
    items: list[DashboardActualItem]
    total: int | None
    known_total: int
    coverage: Coverage
    missing_reasons: list[str]
    attribution_unknown_period_count: int = Field(default=0, ge=0)
    coverage_note: str
    limit: int
    offset: int
    has_more: bool
