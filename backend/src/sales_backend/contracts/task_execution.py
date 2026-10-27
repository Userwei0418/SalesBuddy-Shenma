"""Typed task execution command inputs."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from sales_backend.contracts.task_alignment import TaskActualContribution


class TaskExecutionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_no: int = Field(ge=1, strict=True)
    requirements_version: int = Field(ge=1, strict=True)
    responsibility_version: int = Field(ge=1, strict=True)
    # Mandatory for V2 in the database; omitted V1 commands keep their receipt identity.
    checklist_revision: int | None = Field(default=None, ge=1, strict=True)


class TaskItemProgress(TaskExecutionCommand):
    item_id: UUID
    definition_version: int = Field(ge=1, strict=True)
    state: Literal["not_started", "in_progress", "completed", "rework"]
    note: str = Field(min_length=1, max_length=4000, strict=True)

    @field_validator("note", mode="before")
    @classmethod
    def nonblank_note(cls, value):
        return value.strip() if isinstance(value, str) else value


class TaskFeedback(TaskExecutionCommand):
    text: str = Field(min_length=1, max_length=4000, strict=True)
    reply_to_event_id: UUID | None = None
    corrects_event_id: UUID | None = None
    feedback_kind: Literal["progress", "comment"] | None = None
    item_id: UUID | None = None
    next_step: str | None = Field(default=None, max_length=2000, strict=True)
    blocker: str | None = Field(default=None, max_length=2000, strict=True)
    evidence_urls: list[str] | None = Field(default=None, max_length=10)

    @field_validator("text", mode="before")
    @classmethod
    def nonblank_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("evidence_urls")
    @classmethod
    def safe_links(cls, value):
        from urllib.parse import urlsplit
        for link in value or []:
            parsed = urlsplit(link)
            if (len(link) > 2000 or parsed.scheme not in {"https", "http"}
                    or not parsed.netloc or parsed.username or parsed.password):
                raise ValueError("成果资料链接须为有效 HTTP/HTTPS 地址")
        return value


class TaskPause(TaskExecutionCommand):
    reason: str = Field(min_length=1, max_length=1000, strict=True)
    recheck_at: AwareDatetime

    @field_validator("reason", mode="before")
    @classmethod
    def nonblank_reason(cls, value):
        return value.strip() if isinstance(value, str) else value


class TaskResume(TaskExecutionCommand):
    pause_id: UUID
    reason: str = Field(min_length=1, max_length=1000, strict=True)

    @field_validator("reason", mode="before")
    @classmethod
    def nonblank_reason(cls, value):
        return value.strip() if isinstance(value, str) else value


class TaskHandoverResponse(TaskExecutionCommand):
    handover_id: UUID
    decision: Literal["accept", "reject"]
    reason: str = Field(default="", max_length=1000, strict=True)

    @field_validator("reason", mode="before")
    @classmethod
    def trim_reason(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def rejection_needs_reason(self):
        if self.decision == "reject" and not self.reason:
            raise ValueError("拒绝交接必须说明原因")
        return self


class TaskProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements_version: int = Field(ge=1, strict=True)
    completed: int = Field(ge=0, strict=True)
    total: int = Field(ge=0, strict=True)
    percent: int | None = Field(ge=0, le=100, strict=True)
    basis: Literal["checklist", "missing_checklist", "requires_revalidation"]

    @model_validator(mode="after")
    def coherent_progress(self):
        if self.completed > self.total:
            raise ValueError("完成项数不能大于有效项数")
        if self.basis == "missing_checklist":
            if self.total or self.completed or self.percent is not None:
                raise ValueError("无清单不能报告完成比例")
        elif self.basis == "requires_revalidation":
            if self.total == 0 or self.percent is not None:
                raise ValueError("待复核清单不能报告确定比例")
        elif self.total == 0 or self.percent != self.completed * 100 // self.total:
            raise ValueError("完成比例须由有效清单等权计算")
        return self


class TaskChecklistDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: UUID
    definition_version: int = Field(default=1, ge=1, strict=True)
    title: str = Field(min_length=1, max_length=500, strict=True)
    acceptance_criteria: str = Field(min_length=1, max_length=2000, strict=True)

    @field_validator("title", "acceptance_criteria", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class TaskOverallRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500, strict=True)
    description: str = Field(max_length=8000, strict=True)
    deliverable: str = Field(max_length=4000, strict=True)
    acceptance_criteria: str = Field(max_length=4000, strict=True)
    due_at: AwareDatetime
    priority_code: Literal["normal", "medium", "high", "urgent"]

    @field_validator("title", "description", "deliverable", "acceptance_criteria", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class TaskRequirements(TaskOverallRequirements):
    """Frozen V1 shape: the V2 command rejects this embedded checklist."""
    checklist: list[TaskChecklistDefinition] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_items(self):
        if len({x.item_id for x in self.checklist}) != len(self.checklist):
            raise ValueError("推进项标识不能重复")
        return self


class TaskHandoverInitiate(TaskExecutionCommand):
    target_user_id: UUID
    reason: str = Field(min_length=1, max_length=1000, strict=True)

    @field_validator("reason", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class TaskRequirementsPropose(TaskExecutionCommand):
    requirements: TaskRequirements | TaskOverallRequirements
    reason: str = Field(min_length=1, max_length=1000, strict=True)

    @field_validator("reason", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class TaskRequirementsRespond(TaskExecutionCommand):
    change_id: UUID
    decision: Literal["accept", "reject"]
    reason: str = Field(default="", max_length=1000, strict=True)

    @field_validator("reason", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def rejection_reason(self):
        if self.decision == "reject" and not self.reason:
            raise ValueError("拒绝变更必须说明原因")
        return self


class TaskRequirementsWithdraw(TaskExecutionCommand):
    change_id: UUID
    reason: str = Field(min_length=1, max_length=1000, strict=True)


class TaskCompletionSubmit(TaskExecutionCommand):
    actual_contribution: TaskActualContribution | None = None
    note: str = Field(min_length=1, max_length=4000, strict=True)
    missing_deliverables_resolution: str | None = Field(default=None, max_length=4000, strict=True)

    @field_validator("note", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class TaskCompletionReview(TaskExecutionCommand):
    submission_id: UUID
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=4000, strict=True)
    rework_item_ids: list[UUID] = Field(default_factory=list, max_length=100)
    missing_deliverables: str | None = Field(default=None, max_length=4000, strict=True)

    @model_validator(mode="after")
    def rejection_reason(self):
        self.note = self.note.strip()
        if self.decision == "reject" and not self.note:
            raise ValueError("驳回必须填写原因")
        if self.decision == "approve" and (self.rework_item_ids or self.missing_deliverables):
            raise ValueError("通过验收不能同时指定返工项")
        if len(set(self.rework_item_ids)) != len(self.rework_item_ids):
            raise ValueError("返工项不能重复")
        return self


class TaskCompletionWithdraw(TaskExecutionCommand):
    submission_id: UUID
    reason: str = Field(min_length=1, max_length=1000, strict=True)


class TaskPauseRecheck(TaskPause):
    pause_id: UUID


class TaskInitialResponse(TaskExecutionCommand):
    reason: str = Field(default="", max_length=1000, strict=True)


class TaskV2Command(TaskExecutionCommand):
    checklist_revision: int = Field(ge=1, strict=True)


class TaskExecutionInitialize(TaskV2Command):
    resume_status: Literal["pending_confirm", "pending_execution", "in_progress"] | None = None
    reason: str = Field(min_length=1, max_length=1000, strict=True)

    @field_validator("reason", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class TaskChecklistSave(TaskV2Command):
    reason: str = Field(min_length=1, max_length=1000, strict=True)
    checklist: list[TaskChecklistDefinition] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_items(self):
        if len({item.item_id for item in self.checklist}) != len(self.checklist):
            raise ValueError("推进项标识不能重复")
        return self


class TaskCancel(TaskV2Command):
    reason: str = Field(min_length=1, max_length=1000, strict=True)


class TaskSelfComplete(TaskV2Command):
    note: str = Field(min_length=1, max_length=4000, strict=True)
    submission_id: UUID

    @field_validator("note", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


COMMAND_MODELS = {
    "initialize_execution": TaskExecutionInitialize,
    "save_checklist": TaskChecklistSave,
    "complete_self": TaskSelfComplete,
    "cancel_task": TaskCancel,
    "set_item_progress": TaskItemProgress,
    "append_feedback": TaskFeedback,
    "pause": TaskPause,
    "recheck_pause": TaskPauseRecheck,
    "resume": TaskResume,
    "initiate_executor_handover": TaskHandoverInitiate,
    "respond_executor_handover": TaskHandoverResponse,
    "initiate_reviewer_handover": TaskHandoverInitiate,
    "respond_reviewer_handover": TaskHandoverResponse,
    "propose_requirements": TaskRequirementsPropose,
    "respond_requirements": TaskRequirementsRespond,
    "withdraw_requirements": TaskRequirementsWithdraw,
    "submit_completion": TaskCompletionSubmit,
    "review_completion": TaskCompletionReview,
    "withdraw_completion": TaskCompletionWithdraw,
    "accept_task": TaskInitialResponse,
    "decline_task": TaskInitialResponse,
}


def command_payload(command):
    """Do not add newly optional V2 keys to persisted V1 idempotency receipts."""
    body = command.model_dump(mode="json")
    for key in ("checklist_revision", "feedback_kind", "item_id", "next_step", "blocker", "evidence_urls",
                "missing_deliverables", "missing_deliverables_resolution", "resume_status"):
        if key not in command.model_fields_set:
            body.pop(key, None)
    return body
