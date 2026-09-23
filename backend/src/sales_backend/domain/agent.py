from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RoleCode(StrEnum):
    SALES = "sales"
    SUPERVISOR = "supervisor"
    MANAGER = "manager"
    FDE = "fde"
    FDE_LEAD = "fde_lead"
    OPERATIONS = "operations"
    ADMINISTRATOR = "administrator"


class DataScope(StrEnum):
    SELF = "self"
    TEAM = "team"
    WORKSPACE = "workspace"


class AgentMode(StrEnum):
    CHATBI = "chatbi"
    CUSTOMER_CHATBI = "customer_chatbi"
    TODAY_TASKS = "today_tasks"
    PERSONAL_RISKS = "personal_risks"
    OPERATING_REPORT = "operating_report"
    VISIT_ENTRY = "visit_entry"
    OPPORTUNITY_DRAFT = "opportunity_draft"
    CUSTOMER_CREATE = "customer_create"
    MANAGEMENT_TASK = "management_task"


class ActorContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    workspace_id: str
    user_id: str
    role: RoleCode
    data_scope: DataScope
    team_ids: tuple[str, ...] = ()


class AssigneeContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    workspace_id: str
    user_id: str
    role: RoleCode
    team_ids: tuple[str, ...] = ()


class AgentRequest(BaseModel):
    mode: AgentMode
    text: str = Field(min_length=1, max_length=60_000)
    conversation_id: str | None = None
    customer_id: str | None = None
    input_source: str = Field(default="text", pattern="^(text|audio_transcript)$")


class AgentPlan(BaseModel):
    mode: AgentMode
    intent_code: str
    agent_code: str
    stages: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    produces_artifact: bool
    confirmation_required: bool
    optional_tts: bool = True


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant|tool)$")
    content: str
