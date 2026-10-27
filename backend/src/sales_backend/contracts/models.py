from __future__ import annotations

from sales_backend.contracts.collection_actuals import CollectionActualInput
from sales_backend.contracts.recognized_actuals import RecognizedActualInput

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from sales_backend.contracts.materials import OpportunityMaterialInput, TradeRelation
from sales_backend.contracts.opportunity_fields import NEW_FIELDS, OpportunityBusinessFields, OpportunityPeopleFields
from sales_backend.contracts.task_alignment import (
    TaskActualContribution,
    TaskAlignmentInput,
    TaskAlignmentV2Input,
    alignment_request,
)
from sales_backend.contracts.types import UUIDString
from sales_backend.contracts.task_creation import TaskCreationConfirmation
from sales_backend.domain.agent import AgentMode
from sales_backend.domain.customer_creation_options import CustomerCreationSource, CustomerCreationType
from sales_backend.domain.customer_identity import BusinessIdentities


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
    can_read_customer: bool = Field(
        default=False, description="当前用户能否打开客户完整档案；按客户详情权限计算，不代表客户引用权限。",
    )
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


class TaskCreate(TaskCreationConfirmation):
    alignment_contract_version: int | None = Field(default=None, strict=True, ge=2, le=2)
    alignment: TaskAlignmentInput | TaskAlignmentV2Input | None = None
    association_kind: Literal["daily", "customer"] | None = None
    description: str = Field(min_length=5, max_length=500)
    assignee_account_code: str | None = Field(default=None, min_length=3, max_length=64)
    target_position: Literal['self','supervisor','manager','operations','fde','fde_lead'] | None = None
    due_at: datetime
    priority_code: str = Field(default="normal", pattern="^(normal|medium|high|urgent)$")
    customer_id: UUIDString | None = None
    opportunity_id: UUID | None = None

    @model_serializer(mode="wrap")
    def legacy_protocol_dump(self, handler):
        result = handler(self)
        if self.alignment_contract_version is None:
            result.pop("alignment_contract_version", None)
        return result

    @model_validator(mode="before")
    @classmethod
    def alignment_protocol(cls, values):
        return alignment_request(values)

    @model_validator(mode='after')
    def one_target(self):
        if bool(self.assignee_account_code) == bool(self.target_position):
            raise ValueError('请选择一个接收人或岗位，不能同时指定')
        if self.execution_protocol_version == 3 and self.target_position not in (None, 'self'):
            raise ValueError('轻量任务请明确选择执行人，不使用岗位领取')
        return self

    @field_validator("customer_id", mode="before")
    @classmethod
    def empty_customer_id(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class TaskBatchCreate(BaseModel):
    """One human-confirmed assignment, one independently owned task per colleague."""
    tasks: list[TaskCreate] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def distinct_people_same_assignment(self):
        accounts = set()
        def comparable(task):
            value = task.model_dump(exclude={"assignee_account_code", "target_position"})
            value["checklist"] = [{key: item for key, item in definition.items() if key != "item_id"}
                                  for definition in value["checklist"]]
            return value

        common = comparable(self.tasks[0])
        for task in self.tasks:
            account = (task.assignee_account_code or "").strip().casefold()
            if not account or task.target_position:
                raise ValueError("多人派发须逐人指定账号，不支持岗位领取")
            if account in accounts:
                raise ValueError("同一负责人不能重复选择")
            accounts.add(account)
            if comparable(task) != common:
                raise ValueError("同一次派发的任务内容、关联和执行设置必须一致")
        return self


class TaskEventCreate(BaseModel):
    actual_contribution: TaskActualContribution | None = None
    event_type: str = Field(pattern="^(accept|reject|complete|approve_completion|reject_completion|cancel|reassign)$")
    note: str | None = Field(default=None, max_length=2000)
    version_no: int | None = Field(default=None, ge=0)
    assignee_account_code: str | None = Field(default=None, min_length=3, max_length=64)

    @model_validator(mode="after")
    def coordination_fields(self):
        if self.actual_contribution is not None and (self.event_type != "complete" or self.version_no is None):
            raise ValueError("实际贡献声明仅用于完成任务且须提供当前版本")
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
    version_no: int | None = Field(default=None, ge=1, strict=True, description=(
        "当前风险版本；提供时严格校验。省略或 null 为旧客户端兼容：仅可关闭当前版本为1的未关闭风险；"
        "其他开放版本返回409并要求升级客户端。已关闭风险只读返回，不覆盖原解除记录。"
    ))

    @field_validator("resolution_note")
    @classmethod
    def meaningful_resolution(cls, value):
        value = value.strip()
        if len(value) < 5:
            raise ValueError("解除风险须填写至少5个字的说明")
        return value


class CustomerCreate(BaseModel):
    company_reference: str | None = Field(default=None, max_length=200)
    contact_phone: str = Field(default="", max_length=80)
    contact_email: str = Field(default="", max_length=200)
    name: str = Field(min_length=1, max_length=200)
    industry: str = Field(default="", max_length=100)
    business_identities: BusinessIdentities = Field(default_factory=lambda: ["customer"])
    partner_profile_id: UUID | None = None
    partner_id: UUID | None = None
    customer_type: CustomerCreationType | None = None
    level_code: Literal["Tier-1", "Tier-2", "Tier-3"]
    source: CustomerCreationSource
    target_team: str = Field(min_length=1, max_length=100)
    target_team_id: UUID | None = None
    partner_name: str = Field(default="", max_length=200)
    contact_name: str = Field(min_length=1, max_length=100)
    contact_title: str = Field(min_length=1, max_length=100)
    contact_role: Literal["使用者", "影响者", "决策者"]

    @model_validator(mode="after")
    def validate_business_identity(self):
        if "customer" in self.business_identities and self.customer_type is None:
            raise ValueError("客户身份必须填写客户类型")
        if "customer" not in self.business_identities and self.customer_type is not None:
            raise ValueError("纯合作伙伴不填写客户类型")
        if self.partner_profile_id and "partner" not in self.business_identities:
            raise ValueError("关联自身伙伴档案需要合作伙伴业务身份")
        return self

    @field_validator("name")
    @classmethod
    def nonblank_customer_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("请填写客户名称")
        return value

    @field_validator("company_reference")
    @classmethod
    def optional_company_reference(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


class CustomerUpdate(BaseModel):
    business_identities: BusinessIdentities | None = None
    partner_profile_id: UUID | None = None
    partner_id: UUID | None = None
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

    @model_validator(mode="after")
    def identity_not_null(self):
        if "business_identities" in self.model_fields_set and self.business_identities is None:
            raise ValueError("业务身份不接受 null")
        return self


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
    collection_confidence: Literal["high", "low"] | None = None
    year: int = Field(ge=2000, le=2100)
    quarter: int = Field(ge=1, le=4)
    recognized_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    collection_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)


class OpportunityCreate(OpportunityBusinessFields, OpportunityPeopleFields):
    recognized_actuals: list[RecognizedActualInput] | None = Field(default=None, max_length=40, description="已有商机按物料登记季度累计不含税实际确收；单位元，0有效，省略不改，依据必填；沿用商机幂等键与实绩revision。")
    collection_actuals: list[CollectionActualInput] | None = Field(default=None, max_length=40, description="商机修改时直接登记季度累计实际回款；省略不改实绩。单位元；需要幂等键和正式实绩revision。")
    fields_schema_version: Literal[2] | None = None
    trade_relation: TradeRelation | None = None
    upstream_opportunity_id: UUID | None = None
    material_lines: list[OpportunityMaterialInput] | None = Field(default=None, max_length=200)
    owner_team_id: UUID | None = Field(default=None, description="新增商机所属团队；须在创建权限的数据范围内")
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
    product_line: str | None = Field(default=None, max_length=50000, description="历史兼容字段；多产品线显示回传仅用于保持原值，新录入使用意向目录ID或物料")
    forecast_schema_version: Literal[1, 2] = 1
    quarterly_forecasts: list[QuarterForecast] | None = Field(default=None, max_length=100)
    expected_close_date: date
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    version_no: int | None = Field(default=None, ge=0)
    amount_confirmed_value: Decimal | None = Field(
        default=None, gt=0, max_digits=18, decimal_places=2,
        description="用户确认的大额商机金额，单位元；必须与最终amount相等。仅请求确认，不改变金额，不由AI自动赋值。",
    )

    @model_validator(mode="before")
    @classmethod
    def strict_new_client_fields(cls, values):
        if isinstance(values, dict) and NEW_FIELDS.intersection(values) and values.get("action") == "update" and values.get("version_no") is None:
            raise ValueError("新增商机字段编辑必须携带version_no")
        if isinstance(values, dict) and values.get("fields_schema_version") == 2:
            if "fde_member_ids" in values and values["fde_member_ids"] is None:
                raise ValueError("长期FDE名单清空请提交空数组，不能提交null")
            unknown = set(values) - cls.model_fields.keys()
            if unknown:
                raise ValueError("新版商机请求包含不支持的字段：" + ",".join(sorted(unknown)))
            if values.get("action") == "update" and values.get("version_no") is None:
                raise ValueError("新版商机编辑必须携带version_no")
        return values

    @model_validator(mode="after")
    def validate_stage(self):
        if self.recognized_actuals:
            if self.action != "update" or not self.opportunity_id or self.version_no is None:
                raise ValueError("实际确收请在已有商机修改中登记，并携带商机版本")
            periods = [(r.year, r.quarter) for r in self.recognized_actuals]
            if len(periods) != len(set(periods)):
                raise ValueError("同一季度实际确收只能填写一次")
        if self.collection_actuals:
            if self.action != "update" or not self.opportunity_id or self.version_no is None:
                raise ValueError("实际回款请在已有商机修改中登记，并携带商机版本")
            periods = [(r.year, r.quarter) for r in self.collection_actuals]
            if len(periods) != len(set(periods)):
                raise ValueError("同一季度实际回款只能填写一次")
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
    asr: dict | None = None
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
    tax_basis: Literal["unknown", "inclusive", "exclusive", "not_applicable"] = "unknown"
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
