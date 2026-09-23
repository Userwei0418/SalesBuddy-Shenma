from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sales_backend.contracts.types import UUIDString
from sales_backend.domain.agent import AgentMode


class ConversationCreate(BaseModel):
    mode: AgentMode = AgentMode.CHATBI
    customer_id: UUIDString | None = None
    opportunity_id: UUID | None = None

    @model_validator(mode="after")
    def customer_question_requires_customer(self):
        if self.mode is AgentMode.CUSTOMER_CHATBI and not (self.customer_id or "").strip():
            raise ValueError("客户问数必须先选择客户")
        return self


class ConversationResponse(BaseModel):
    id: str
    mode: str
    customer_id: str | None
    opportunity_id: str | None = None
    status: str
    created_at: datetime


class MessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=60_000)
    client_message_id: str | None = Field(default=None, max_length=128)
    input_source: str = Field(default="text", pattern="^(text|audio_transcript)$")


class RunAccepted(BaseModel):
    run_id: str
    status: str = "queued"


class MessageResponse(BaseModel):
    id: str
    sender_type: str
    content_type: str
    text_content: str | None
    structured_content: dict[str, Any] | None
    created_at: datetime


class AgentRunResponse(BaseModel):
    id: str
    status: str
    intent_code: str | None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_detail: str | None = None
    created_at: datetime
    completed_at: datetime | None


class PageResponse(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: str | None = None


class ArchivedVisitSummary(BaseModel):
    id: str
    customer_id: str
    customer_name: str
    opportunity_name: str | None
    interaction_at: datetime | None
    interaction_mode: str | None
    archived_at: datetime
    completed_count: int
    total_count: int
    score: int | None
    grade: str
    next_action: str


class FdeTeamTaskSummary(BaseModel):
    overdue: int
    claim: int
    handover: int


class AssistantHomeResponse(BaseModel):
    greeting: str
    actor: dict[str, Any]
    overview: list[dict[str, Any]]
    focus_cards: list[dict[str, Any]]
    archived_visits: list[ArchivedVisitSummary] = Field(default_factory=list)
    quick_actions: list[dict[str, str]]
    data_as_of: datetime
    team_summary: FdeTeamTaskSummary | None = None

    display_policy: dict[str, Any] = Field(default_factory=dict)


class TaskCreate(BaseModel):
    association_kind: Literal["daily", "customer"] | None = None
    description: str = Field(min_length=5, max_length=500)
    assignee_account_code: str | None = Field(default=None, min_length=3, max_length=64)
    target_position: Literal['self','supervisor','manager','operations','fde','fde_lead'] | None = None
    due_at: datetime
    priority_code: str = Field(default="normal", pattern="^(normal|medium|high|urgent)$")
    customer_id: UUIDString | None = None
    opportunity_id: UUID | None = None

    @model_validator(mode='after')
    def one_target(self):
        if bool(self.assignee_account_code) == bool(self.target_position):
            raise ValueError('请选择一个接收人或岗位，不能同时指定')
        return self

    @field_validator("customer_id", mode="before")
    @classmethod
    def empty_customer_id(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class TaskEventCreate(BaseModel):
    event_type: str = Field(pattern="^(accept|reject|complete|approve_completion|reject_completion|cancel|reassign)$")
    note: str | None = Field(default=None, max_length=2000)
    version_no: int | None = Field(default=None, ge=0)
    assignee_account_code: str | None = Field(default=None, min_length=3, max_length=64)

    @model_validator(mode="after")
    def coordination_fields(self):
        if self.event_type in {"approve_completion", "reject_completion"} and self.version_no is None:
            raise ValueError("验收须提供当前版本")
        if self.event_type in {"complete", "reject_completion"} and not (self.note or "").strip():
            raise ValueError("请填写完成说明或驳回原因")
        if self.event_type in {"cancel", "reassign"} and (not self.note or not self.note.strip() or self.version_no is None):
            raise ValueError("任务交接或取消须填写原因并提供当前版本")
        if (self.event_type == "reassign") != bool(self.assignee_account_code):
            raise ValueError("仅转交任务时必须指定新接收人")
        return self


class RiskResolve(BaseModel):
    resolution_note: str = Field(min_length=5, max_length=2000)
    version_no: int | None = Field(default=None, ge=0)


class CustomerCreate(BaseModel):
    company_reference: str | None = Field(default=None, min_length=1, max_length=200)
    contact_phone: str = Field(default="", max_length=80)
    contact_email: str = Field(default="", max_length=200)
    name: str = Field(min_length=1, max_length=200)
    industry: str = Field(default="", max_length=100)
    customer_type: Literal["潜在客户", "商机客户", "已成单客户"]
    level_code: Literal["Tier-1", "Tier-2", "Tier-3"]
    source: Literal["销售自拓", "客户转介绍", "市场活动", "销售线索", "合作伙伴", "其他"]
    target_team: str = Field(min_length=1, max_length=100)
    target_team_id: UUID | None = None
    partner_name: str = Field(default="", max_length=200)
    contact_name: str = Field(min_length=1, max_length=100)
    contact_title: str = Field(min_length=1, max_length=100)
    contact_role: Literal["使用者", "影响者", "决策者"]


class CustomerUpdate(BaseModel):
    contact_phone: str | None = Field(default=None, max_length=80)
    contact_email: str | None = Field(default=None, max_length=200)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    industry: str | None = Field(default=None, min_length=1, max_length=100)
    customer_type: str | None = Field(default=None, min_length=1, max_length=100)
    level_code: Literal["Tier-1", "Tier-2", "Tier-3"] | None = None
    source: str | None = Field(default=None, min_length=1, max_length=100)
    partner_name: str | None = Field(default=None, min_length=1, max_length=200)
    demand_summary: str | None = Field(default=None, min_length=1, max_length=5000)
    next_action: str | None = Field(default=None, min_length=1, max_length=5000)
    contact_name: str | None = Field(default=None, min_length=1, max_length=100)
    contact_title: str | None = Field(default=None, min_length=1, max_length=100)
    contact_role: Literal["决策者", "影响者", "使用者"] | None = None
    version_no: int | None = Field(default=None, ge=0)


class CustomerAssign(BaseModel):
    assignee_account_code: str = Field(min_length=3, max_length=64)
    first_action: str = Field(min_length=1, max_length=2000)
    due_at: datetime | None = None


class SalesTargetUpdate(BaseModel):
    scope: Literal["self", "person", "team", "department"]
    account_code: str | None = Field(default=None, max_length=64)
    team: str | None = Field(default=None, max_length=100)
    year: int = Field(ge=2000, le=2100)
    kind: Literal["collection", "recognized"]
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)


class QuarterForecast(BaseModel):
    year: int = Field(ge=2000, le=2100)
    quarter: int = Field(ge=1, le=4)
    recognized_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    collection_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)


class OpportunityCreate(BaseModel):
    fde_member_ids: list[UUID] | None = Field(default=None, max_length=30)
    sales_channel: Literal['direct', 'partner'] | None = None
    partner_id: UUID | None = None
    follow_up_plan: str | None = Field(default=None, max_length=5000)
    action: Literal["create", "update"] = "create"
    opportunity_id: UUIDString | None = None
    name: str = Field(min_length=1, max_length=200)
    probability: Literal[10, 30, 50, 70, 90, 100] | None = None
    status: Literal["open", "won", "lost"] = "open"
    closure_confirmed: bool = False
    reopen_confirmed: bool = False
    partner_name: str | None = Field(default=None, max_length=200)
    product_line: str | None = Field(default=None, max_length=200)
    quarterly_forecasts: list[QuarterForecast] | None = Field(default=None, max_length=100)
    expected_close_date: date
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    version_no: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_stage(self):
        if self.status == "open" and self.probability not in (10, 30, 50, 70, 90):
            raise ValueError("请选择商机阶段；100%需要明确选择赢单")
        if self.status == "won" and self.probability != 100:
            raise ValueError("赢单概率必须为100%")
        return self


class VisitCreate(BaseModel):
    fde_participant_ids: list[UUID] = Field(
        default_factory=list, max_length=30, description="与质检快照一致的本次FDE参与人员；不代表商机完整名单"
    )
    customer_id: UUIDString
    fields: dict[str, Any]

    @field_validator("fields")
    @classmethod
    def normalize_opportunity_reference(cls, fields: dict[str, Any]) -> dict[str, Any]:
        # FDE scope is checked before content/archival validation. Keep the open
        # historical field contract, but never pass an unchecked ID to that SQL.
        value = fields.get("opportunity_id")
        if value is None or value == "":
            return fields
        if not isinstance(value, str):
            raise ValueError("商机标识格式不正确")
        try:
            identifier = str(UUID(value))
        except ValueError:
            raise ValueError("商机标识格式不正确") from None
        return {**fields, "opportunity_id": identifier}


class AgentRuntimeConfigRollback(BaseModel):
    expected_version: int = Field(ge=0, le=2147483646)


class AgentRuntimeConfigUpdate(BaseModel):
    expected_version: int = Field(ge=0, le=2147483646)
    provider_base_url: str = Field(min_length=8, max_length=500, pattern=r"^https?://")
    api_key: str | None = Field(default=None, min_length=8, max_length=2000)
    llm_model: str = Field(min_length=1, max_length=200)
    asr_model: str = Field(min_length=1, max_length=200)
    tts_model: str = Field(min_length=1, max_length=200)
    prompt_overrides: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class AudioTranscriptionResponse(BaseModel):
    artifact_id: str
    text: str
    purpose: str
    duration_seconds: float | None = None
    trace_id: str | None = None


class ActualCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    customer_id: UUID
    opportunity_id: UUID | None = None
    kind: Literal["recognized", "collection"]
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    occurred_on: date
    source_ref: str = Field(min_length=1, max_length=160)
    note: str = Field(default="", max_length=1000)
    request_id: UUID
    confirmed: Literal[True]

    @field_validator("occurred_on")
    @classmethod
    def actual_date(cls, value):
        if value > datetime.now(ZoneInfo("Asia/Shanghai")).date():
            raise ValueError("实绩日期不能晚于今天；预计金额请填写季度预测")
        return value


class ActualVoid(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=300)
    confirmed: Literal[True]
