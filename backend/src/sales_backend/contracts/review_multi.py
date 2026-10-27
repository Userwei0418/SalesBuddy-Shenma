"""Array-selected Review responses, separate from the legacy single selector."""

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from sales_backend.contracts.review_overview import (
    Coverage,
    ReviewMetric,
    ReviewOverview,
    ReviewPage,
    ReviewTarget,
)
from sales_backend.contracts.review_scope import ReviewResolvedScopeV2, ReviewScopeSelectionV2
from sales_backend.contracts.work_report_base import PeriodKind


class ReviewSelectionV2(ReviewScopeSelectionV2):
    anchor_date: date | None = None
    period_type: PeriodKind = "day"


class ReviewTargetComponentV2(BaseModel):
    scope_type: Literal["person", "team"]
    subject_id: UUID
    target_id: UUID | None
    version_no: int | None
    status: Literal["effective", "no_target", "permission_denied"]
    value: str | None
    note: str | None = None


class ReviewTargetV2(ReviewTarget):
    known_subtotal: str | None
    coverage: Coverage
    aggregation: Literal["sum_distinct_subject_targets"]
    components: list[ReviewTargetComponentV2]


class ReviewMetricV2(ReviewMetric):
    target: ReviewTargetV2


class ReviewOverviewV2(ReviewOverview):
    contract_version: Literal["review.overview.v2"] = "review.overview.v2"
    selection: ReviewSelectionV2
    scope: ReviewResolvedScopeV2
    metrics: list[ReviewMetricV2]


class ReviewPageV2(ReviewPage):
    contract_version: Literal["review.page.v2"] = "review.page.v2"
    selection: ReviewSelectionV2
    scope: ReviewResolvedScopeV2
    generation: dict[str, Any]
