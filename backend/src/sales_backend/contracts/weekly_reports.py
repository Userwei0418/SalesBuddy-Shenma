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
    report_week: date
    report_week_end: date
    source_cutoff_at: datetime
    snapshot_at: datetime
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
    current_week: date
    items: list[WeeklySummary]
    has_more: bool


class WeeklySourceRecord(BaseModel):
    id: UUID
    recorder_id: UUID
    recorder_name: str | None
    customer_id: UUID
    customer_name: str | None
    opportunity_id: UUID | None
    opportunity_name: str | None
    created_at: datetime
    visit_date: date | None
    follow_up_record: str
    next_action: str | None
    status: str
    version_no: int


class WeeklySources(BaseModel):
    report_week: date
    report_week_end: date
    period: WeeklyPeriod
    source_cutoff_at: datetime
    snapshot_at: datetime
    statistics: WeeklyStatistics
    items: list[WeeklySourceRecord]
    total: int
    has_more: bool
    next_offset: int | None
