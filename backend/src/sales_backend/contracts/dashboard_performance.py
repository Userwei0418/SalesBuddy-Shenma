"""Read-only quarterly performance rankings, separate from existing cards."""
from typing import Literal

from pydantic import BaseModel, Field

from sales_backend.contracts.dashboard_metrics import DashboardMetricTarget

PerformanceMetricCode = Literal[
    "forecast_collection", "collection_ppl", "forecast_recognized_exclusive",
    "recognized_pipeline_exclusive", "actual_recognized_exclusive", "actual_collection",
]
Coverage = Literal["complete", "partial", "unavailable"]


class PerformanceTarget(DashboardMetricTarget):
    status: Literal["effective", "no_target", "permission_denied", "not_configured"]
    reason: str | None = None


class PerformanceRow(BaseModel):
    id: str
    name: str
    team_id: str | None = None
    team_name: str | None = None
    value: str | None
    known_subtotal: str | None
    coverage: Coverage
    missing_amount_count: int = Field(ge=0)
    unknown_eligibility_count: int = Field(ge=0)
    missing_reasons: list[str]
    target: PerformanceTarget
    rate: str | None
    rank: int | None
    rank_basis: Literal["rate", "amount"]
    provisional: bool


class PerformanceCohort(BaseModel):
    scope: Literal["current_authorized_ranking_scope"] = "current_authorized_ranking_scope"
    departments_count: int
    people_count: int


class DashboardPerformanceRankings(BaseModel):
    contract_version: Literal["dashboard.performance_rankings.v1"] = "dashboard.performance_rankings.v1"
    data_as_of: str
    year: int
    quarter: int
    metric_code: PerformanceMetricCode
    label: str
    unit: Literal["元"] = "元"
    currency: Literal["CNY"] = "CNY"
    tax_basis: Literal["cash_collection_not_applicable", "tax_exclusive"]
    basis: Literal["latest_sales_forecast", "adopted_actuals", "material_reference"]
    departments: list[PerformanceRow]
    people: list[PerformanceRow]
    department_sort_basis: Literal["rate", "amount"]
    department_sort_reason: Literal["comparable_targets", "no_comparable_targets"]
    departments_coverage: Coverage
    people_coverage: Coverage
    cohort: PerformanceCohort
    own_user_id: str
    attribution_unknown_period_count: int = 0
    department_excluded_fact_count: int = 0
    people_excluded_fact_count: int = 0
    definitions: list[str]
