"""Lightweight commands; legacy checklist contracts and receipts stay unchanged."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class TaskV3Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_no: int = Field(ge=1, strict=True)
    requirements_version: int = Field(ge=1, strict=True)
    responsibility_version: int = Field(ge=1, strict=True)

    @field_validator("*", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class TaskV3Feedback(TaskV3Command):
    text: str = Field(min_length=1, max_length=4000, strict=True)
    next_step: str | None = Field(default=None, max_length=2000, strict=True)


class TaskV3Reason(TaskV3Command):
    reason: str = Field(min_length=1, max_length=1000, strict=True)


class TaskV3Defer(TaskV3Reason):
    due_at: AwareDatetime


class TaskV3Submit(TaskV3Command):
    note: str = Field(min_length=1, max_length=4000, strict=True)


class TaskV3Review(TaskV3Command):
    submission_id: UUID
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=4000, strict=True)

    @model_validator(mode="after")
    def require_rejection_reason(self):
        if self.decision == "reject" and not self.note:
            raise ValueError("退回继续须填写原因")
        return self


class TaskV3Revise(TaskV3Command):
    description: str | None = Field(default=None, min_length=5, max_length=500, strict=True)
    due_at: AwareDatetime | None = None
    executor_user_id: UUID | None = None
    reason: str | None = Field(default=None, max_length=1000, strict=True)

    @model_validator(mode="after")
    def actual_fields(self):
        if not any(getattr(self, field) is not None for field in ("description", "due_at", "executor_user_id")):
            raise ValueError("请选择需要修改的任务内容、截止日期或执行人")
        return self


class TaskV3Continue(TaskV3Command):
    reason: str = Field(default="", max_length=1000, strict=True)


class TaskV3Adopt(TaskV3Command):
    note: str = Field(default="", max_length=1000, strict=True)


COMMAND_MODELS_V3 = {
    "append_feedback": TaskV3Feedback,
    "defer": TaskV3Defer,
    "request_adjustment": TaskV3Reason,
    "pause": TaskV3Reason,
    "resume": TaskV3Reason,
    "submit_completion": TaskV3Submit,
    "review_completion": TaskV3Review,
    "revise": TaskV3Revise,
    "continue_original": TaskV3Continue,
    "close": TaskV3Reason,
    "adopt": TaskV3Adopt,
}


def command_payload_v3(command):
    return command.model_dump(mode="json", exclude_none=True)
