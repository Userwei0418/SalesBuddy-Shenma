"""Public detail read contracts; flexible legacy attributes stay at the object edge."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


def json_numbers(value):
    """Keep legacy dict-response numeric JSON, including extension/forecast values."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: json_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_numbers(item) for item in value]
    return value


class DetailObject(BaseModel):
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def preserve_numeric_json(cls, value):
        return json_numbers(value)


class OpportunityReference(DetailObject):
    id: UUID
    name: str
    stage_code: str
    status: str
    amount: float | None
    probability: int | None
    expected_close_date: date | None


class OpportunityCard(OpportunityReference):
    customer_id: UUID


class ContactSummary(DetailObject):
    id: UUID
    name: str
    title: str | None = None
    department: str | None = None
    contact_category_code: str | None = None
    relationship_role_code: str | None = None
    is_primary: bool


class LatestVisit(DetailObject):
    id: UUID
    status: str
    interaction_at: datetime | None
    created_at: datetime | None
    next_action: str | None
    visit_date: date | None
    created_date: date | None
    within_seven_days: bool | None


class RiskReference(DetailObject):
    id: UUID
    opportunity_id: UUID | None
    title: str
    severity_code: str
    status: str


class RelatedSummary(DetailObject):
    visit_count: int = Field(ge=0)
    confirmed_visit_count: int = Field(ge=0)
    latest_visit: LatestVisit | None
    task_count: int = Field(ge=0)
    task_status_counts: dict[str, int]
    risk_count: int = Field(ge=0)
    open_risk_severities: list[str | None]
    status_risk: RiskReference | None
    open_risk: RiskReference | None
    opportunity_count: int = Field(ge=0)
    open_opportunity_count: int = Field(ge=0)
    unknown_open_amount_count: int = Field(ge=0)
    open_amount: float | None = Field(description="元；无在推商机为0，任一在推金额未知则为null")


class CustomerSummary(RelatedSummary):
    contact_count: int = Field(ge=0)
    contact_roles: list[str]
    max_probability: int | None


class ProfileDimension(BaseModel):
    code: str
    label: str
    value: int | None
    basis: str


class CustomerProfile(BaseModel):
    version: Literal["customer_profile_v1"]
    dimensions: list[ProfileDimension]
    coverage: int = Field(ge=0, le=6)
    total: Literal[6]
    scope: Literal["current_authorized_records"]


class CustomerHeader(DetailObject):
    id: UUID
    name: str
    primary_contact: ContactSummary | None
    primary_opportunity: OpportunityReference | None
    read_model: Literal["detail_header_v1"]


class OpportunityDetailHeader(DetailObject):
    id: UUID = Field(description="所属客户ID")
    name: str = Field(description="所属客户名称")
    opportunities: list[OpportunityCard] = Field(min_length=1, max_length=1)
    primary_opportunity: OpportunityCard
    read_model: Literal["detail_header_v1"]


class CustomerOverview(DetailObject):
    id: UUID
    name: str
    summary: CustomerSummary
    profile: CustomerProfile
    primary_contact: ContactSummary | None
    primary_opportunity: OpportunityReference | None
    read_model: Literal["detail_overview_v1"]


class OpportunityDetailOverview(DetailObject):
    id: UUID = Field(description="所属客户ID")
    name: str = Field(description="所属客户名称")
    opportunities: list[OpportunityCard] = Field(min_length=1, max_length=1)
    primary_opportunity: OpportunityCard
    summary: RelatedSummary
    read_model: Literal["detail_overview_v1"]


class HistoryPage[Item](DetailObject):
    items: list[Item]
    has_more: bool
    next_offset: int | None = Field(description="兼容offset分页；游标请求及末页为null", ge=0)
    next_cursor: str | None = Field(default=None, description="拜访历史下一页游标；有值时优先使用，重新筛选时清空")


class VisitQualitySummary(BaseModel):
    """Persisted quality evidence only; no policy regrading or private review context."""

    follow_up_score: float | None = Field(description="已保存的质检分数；无有效数值为null，不代表销售目标达成")
    grade: str | None = Field(description="归档时保存的质检等级；不按当前评分规则重算")
    next_action_passed: bool | None = Field(description="已保存的下一步行动检查结果；未评估为null")


class VisitSummary(DetailObject):
    created_at: datetime | None = Field(default=None, description="系统实际录入时间，含时区；非人工填写日期或拜访发生时间")
    id: UUID
    opportunity_id: UUID | None
    status: str
    follow_up_score: float | None = Field(default=None, description="跟进记录已保存的质检分数；历史未评分为null")
    quality_review: VisitQualitySummary | None = Field(default=None, description="质检结果安全摘要；未保存结果为null")
    follow_up_record: str | None = Field(max_length=600)
    next_action: str | None = Field(max_length=600)
    is_summary: Literal[True]
    visit_date: date | None
    created_date: date | None
    within_seven_days: bool | None


class VisitHistoryPage(HistoryPage[VisitSummary]):
    sort: Literal["created_desc"] | None = Field(default=None, description="显式按系统录入时间倒序的服务端回执；缺省请求不返回此字段")


class TimelineEvent(DetailObject):
    key: str
    title: str
    at: datetime
    detail: str | None = Field(max_length=600)
    object_type: Literal["opportunity", "visit", "task"]
    object_id: UUID


class OpportunityPageSummary(BaseModel):
    total: int = Field(ge=0)
    open_count: int = Field(ge=0)
    unknown_open_amount_count: int = Field(ge=0)
    open_amount: float | None


class CustomerOpportunityPage(HistoryPage[OpportunityCard]):
    summary: OpportunityPageSummary
    facets: dict[str, list[Any]]
    offset: int = Field(ge=0)


class ActualQuarter(BaseModel):
    year: int
    quarter: int = Field(ge=1, le=4)
    recognized_amount: float | None = Field(description="实际确收，元；没有登记为null，明确登记0为0")
    collection_amount: float | None = Field(description="实际回款，元；没有登记为null，明确登记0为0")
    recognized_count: int = Field(ge=0)
    collection_count: int = Field(ge=0)
    entry_count: int = Field(ge=0)


class ActualQuarters(DetailObject):
    items: list[ActualQuarter]
    years: list[int]
    as_of: date
    data_source: Literal["database"]


class OperationsCustomerOverview(DetailObject):
    id: UUID
    name: str
    ownership: dict[str, Any] | None
    version_no: int
    customer_code: str | None = None
    external_customer_id: str | None = None
    company_reference: str | None = None
    industry_code: str | None = None
    customer_type_code: str | None = None
    level_code: str | None = None
    source_code: str | None = None
    primary_partner_name: str | None = None
    operation_type: str | None = None
    cooperation_years: float | None = None
    main_business: str | None = None
    customer_budget: str | None = None
    demand_summary: str | None = None
    next_action: str | None = None
    contact_name: str | None = None
    contact_title: str | None = None
    contact_role: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    lifecycle_status: str
    data_source: str
    data_kind: str
    team_name: str | None = None
    creator_name: str | None = None
    created_at: datetime
    updated_at: datetime
    company_verified_at: datetime | None = None


class OwnershipEvent(DetailObject):
    id: UUID
    event_type: str
    reason: str | None
    occurred_at: datetime
    operator: str
    previous_owner: str | None
    owner: str | None


class ClaimHistoryItem(DetailObject):
    id: UUID
    customer_id: UUID
    status: str
    requested_at: datetime
    reviewed_at: datetime | None
    decision_reason: str | None
    applicant_name: str
    account_code: str
    reviewer_name: str | None


class OperationsCustomerHistory(HistoryPage[OwnershipEvent | ClaimHistoryItem]):
    total: int = Field(ge=0, description="同一客户全部授权历史数量；空页仍保留total")
