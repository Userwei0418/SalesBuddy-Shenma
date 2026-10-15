"""Console views of formally saved follow-ups, with explicit public projections."""
from datetime import date, datetime
from typing import Literal

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

from sales_backend.contracts.detail_reads import VisitQualitySummary
from sales_backend.contracts.types import UUIDString


def visit_management_filters(
    scope: Literal['all', 'mine'] = 'all', q: str = Query('', max_length=100),
    recorder_id: UUIDString | None = None,
    date_basis: Literal['interaction', 'created'] = 'interaction',
    date_from: date | None = None, date_to: date | None = None,
    score_min: float | None = Query(None, ge=0, le=100, allow_inf_nan=False),
    score_max: float | None = Query(None, ge=0, le=100, allow_inf_nan=False),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, '开始日期不能晚于结束日期')
    if date_to == date.max:
        raise HTTPException(422, '结束日期超出支持范围')
    if score_min is not None and score_max is not None and score_min > score_max:
        raise HTTPException(422, '最低质检分数不能高于最高分数')
    return dict(scope=scope, q=q.strip(), recorder_id=recorder_id, date_basis=date_basis,
                date_from=date_from, date_to=date_to, score_min=score_min, score_max=score_max)


class VisitManagementOpportunity(BaseModel):
    id: UUIDString
    name: str
    customer_id: UUIDString | None
    customer_name: str | None
    can_open: bool


class VisitManagementRecord(BaseModel):
    id: UUIDString
    status: Literal['confirmed', 'archived']
    subject_kind: Literal['customer', 'partner', 'opportunity', 'unknown']
    subject_id: UUIDString | None
    subject_name: str
    customer_id: UUIDString | None
    customer_name: str | None
    partner_id: UUIDString | None
    partner_name: str | None
    opportunity_id: UUIDString | None
    opportunity_name: str | None
    linked_opportunities: list[VisitManagementOpportunity]
    recorder_id: UUIDString | None
    recorder_name: str | None
    recorder_active: bool | None
    recording_role_code_snapshot: str | None = Field(default=None,
        description='记录创建时的可信岗位快照；缺失为null，不从当前目录推断')
    recording_team_id_snapshot: UUIDString | None = Field(default=None,
        description='记录创建时的部门快照；缺失为null')
    record_source: Literal['crm_history', 'recorded', 'unknown'] = Field(default='unknown',
        description='显式CRM历史导入、具有录入身份快照的记录或来源证据缺失')
    original_recorder_name: str | None
    manager_id: UUIDString | None
    manager_name: str | None
    creator_id: UUIDString | None
    creator_name: str | None
    interaction_at: datetime | None
    visit_date: date | None = Field(description='实际跟进日期，北京日期；历史未知不造日期')
    recorded_on: date | None = Field(description='人工填写日期，不等于系统录入时间')
    created_date: date | None = Field(description='兼容旧展示：人工填写日期，否则系统创建日期')
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


class VisitManagementItem(VisitManagementRecord):
    follow_up_record: str | None = Field(max_length=600)
    next_action: str | None = Field(max_length=600)
    is_summary: Literal[True]


class VisitManagementSummary(BaseModel):
    total: int = Field(ge=0, description='全部筛选范围的正式记录数，多商机关联不重复计数')
    subject_count: int = Field(ge=0, description='关联客户及伙伴ID去重后的数量，不将未知主体名称猜为客户')
    scored_count: int = Field(ge=0, description='已保存有效质检分数的记录数，不把缺评分当零分')


class VisitManagementPage(BaseModel):
    items: list[VisitManagementItem]
    summary: VisitManagementSummary
    has_more: bool
    next_offset: int | None
    as_of: datetime


class VisitManagementRecorder(BaseModel):
    id: UUIDString
    name: str
    account_code: str | None
    active: bool
    record_count: int = Field(ge=0)


class VisitManagementRecorderPage(BaseModel):
    items: list[VisitManagementRecorder]
    unmapped_count: int = Field(ge=0, description='筛选范围内没有匹配当前账号的历史实际跟进人记录数')
    has_more: bool
    next_offset: int | None
    as_of: datetime


class VisitOriginalMaterial(BaseModel):
    id: UUIDString
    filename: str
    file_size: int
    can_download: bool
    download_url: str | None


class VisitParticipant(BaseModel):
    id: UUIDString
    name: str
    team: str | None = None
    team_id: UUIDString | None = None
    role: str | None = None


class VisitManagementDetail(VisitManagementRecord):
    visit_goal: str | None
    expectation_code: str | None
    duration_minutes: int | None
    customer_type: str | None
    partner_name_snapshot: str | None
    contact_title: str | None
    interaction_mode: str | None
    visit_location: str | None
    version_no: int
    customer_main_business: str | None = None
    customer_needs: str | None = None
    customer_budget: str | None = None
    contact_role: str | None = None
    collaborators: list[VisitParticipant]
    fde_participants: list[VisitParticipant]
    can_supplement: bool
    supplement_fields: list[str]
    supplement_unavailable_reason: str | None
    original_materials: list[VisitOriginalMaterial]
    original_material_status: Literal['none', 'unavailable', 'available']
