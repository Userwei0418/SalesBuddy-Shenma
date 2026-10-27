"""Human-reviewed meeting drafts; these contracts do not grant task authority."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sales_backend.contracts.models import TaskCreate
from sales_backend.contracts.task_execution import TaskChecklistDefinition


class TaskImportPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_text: str = Field(min_length=5, max_length=30000)
    meeting_date: date
    batch_name: str = Field(min_length=1, max_length=120)

    @field_validator("batch_name", mode="before")
    @classmethod
    def trim_batch_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class TaskImportDraftItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: UUID
    selected: bool = True
    description: str = Field(max_length=30000)
    assignee_account_code: str | None = Field(default=None, max_length=64)
    due_at: datetime | None = None
    priority_code: Literal["normal", "medium", "high", "urgent"] = "normal"
    association_kind: Literal["daily", "customer"] = "daily"
    customer_id: UUID | None = None
    opportunity_id: UUID | None = None
    duplicate_acknowledged: bool = False
    execution_protocol_version: int | None = Field(default=None, strict=True, ge=1, le=3)
    checklist: list[TaskChecklistDefinition] = Field(default_factory=list, max_length=100)
    deliverable: str | None = Field(default=None, max_length=4000)
    acceptance_criteria: str | None = Field(default=None, max_length=4000)


class TaskImportDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_no: int = Field(ge=1)
    items: list[TaskImportDraftItem] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_items(self):
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("同一原文条目不能重复出现")
        return self


class TaskImportReparse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_no: int = Field(ge=1)


class TaskImportConfirmedItem(TaskCreate):
    model_config = ConfigDict(extra="forbid")
    item_id: UUID
    duplicate_acknowledged: bool = False

    @model_validator(mode="after")
    def explicit_person(self):
        if not self.assignee_account_code or self.target_position:
            raise ValueError("周会任务须明确选择一位负责人，不使用岗位领取")
        if self.due_at.tzinfo is None:
            raise ValueError("截止时间须包含时区")
        return self


class TaskImportConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_no: int = Field(ge=1)
    items: list[TaskImportConfirmedItem] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_items(self):
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("同一原文条目不能重复派发")
        return self
