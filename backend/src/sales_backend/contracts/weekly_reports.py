"""Public business Web contract. Agent credentials and input snapshots stay server-side."""
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class WeeklyPeriod(BaseModel):
    start_date: date
    end_date: date
    timezone: Literal['Asia/Shanghai']
    date_basis: Literal['created_at']


class WeeklyStatistics(BaseModel):
    record_count: int
    customer_count: int
    opportunity_count: int


class WeeklySummary(BaseModel):
    id: UUID
    request_id: UUID
    status: Literal['queued', 'running', 'succeeded', 'failed', 'cancelled']
    result_status: Literal['ready', 'insufficient_data', 'invalid_input'] | None
    period: WeeklyPeriod
    statistics: WeeklyStatistics
    input_sha256: str
    draft_version: int
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    runtime_snapshot_verified: Literal[False]
    actual_snapshot_id: None


class WeeklyDetail(WeeklySummary):
    title: str | None
    body_markdown: str | None
    original_result: dict[str, Any] | None
    draft_source: Literal['agent', 'manual'] | None
    draft_references_validated: bool
    runtime_metadata: dict[str, Any]


class WeeklyList(BaseModel):
    items: list[WeeklySummary]
    has_more: bool
