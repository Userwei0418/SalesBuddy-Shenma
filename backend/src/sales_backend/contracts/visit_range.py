"""Read-only cross-customer follow-up summaries; dates retain their business meaning."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from sales_backend.contracts.detail_reads import VisitQualitySummary
from sales_backend.contracts.types import UUIDString


class VisitRangeOpportunity(BaseModel):
    id: UUIDString
    name: str
    customer_id: UUIDString | None
    customer_name: str | None
    can_open: bool


class VisitRangeRecord(BaseModel):
    id: UUIDString
    status: Literal["confirmed", "archived"]
    subject_kind: Literal["customer", "partner", "opportunity", "unknown"]
    subject_id: UUIDString | None
    subject_name: str
    customer_id: UUIDString | None
    customer_name: str | None
    partner_id: UUIDString | None
    partner_name: str | None
    opportunity_id: UUIDString | None
    opportunity_name: str | None
    linked_opportunities: list[VisitRangeOpportunity]
    recorder_id: UUIDString | None
    recorder_name: str | None
    recorder_active: bool | None
    recording_role_code_snapshot: str | None = Field(
        default=None, description="记录创建时的可信岗位快照；缺失为null，不从当前目录推断"
    )
    recording_team_id_snapshot: UUIDString | None = Field(default=None, description="记录创建时的部门快照；缺失为null")
    record_source: Literal["crm_history", "recorded", "unknown"] = Field(
        default="unknown", description="显式CRM历史导入、具有录入身份快照的记录或来源证据缺失"
    )
    original_recorder_name: str | None
    manager_id: UUIDString | None
    manager_name: str | None
    creator_id: UUIDString | None
    creator_name: str | None
    interaction_at: datetime | None
    visit_date: date | None = Field(description="实际跟进日期，北京日期；历史未知不造日期")
    recorded_on: date | None = Field(description="人工填写日期，不等于系统录入时间")
    created_date: date | None = Field(description="兼容旧展示：人工填写日期，否则系统创建日期")
    created_at: datetime
    interaction_mode_code: str | None
    contact_name: str | None
    is_first_visit: bool | None
    follow_up_record: str | None
    next_action: str | None
    follow_up_score: float | None
    quality_review: VisitQualitySummary | None
    can_open_customer: bool
    can_open_opportunity: bool
    can_read_detail: bool


class VisitRangeItem(VisitRangeRecord):
    follow_up_record: str | None = Field(max_length=600)
    next_action: str | None = Field(max_length=600)
    is_summary: Literal[True]


class VisitRangeSummary(BaseModel):
    total: int = Field(ge=0, description="全部筛选范围的正式记录数，多商机关联不重复计数")
    subject_count: int = Field(ge=0, description="关联客户及伙伴ID去重后的数量，不将未知主体名称猜为客户")
    scored_count: int = Field(ge=0, description="已保存有效质检分数的记录数，不把缺评分当零分")


class VisitRangePage(BaseModel):
    items: list[VisitRangeItem]
    summary: VisitRangeSummary
    has_more: bool
    next_offset: int | None
    as_of: datetime
