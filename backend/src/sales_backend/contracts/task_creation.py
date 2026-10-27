"""Old clients create legacy tasks; explicit V1 requests require human definitions."""

from pydantic import BaseModel, Field, field_validator, model_validator

from sales_backend.contracts.task_execution import TaskChecklistDefinition


class TaskCreationConfirmation(BaseModel):
    execution_protocol_version: int | None = Field(default=None, ge=1, le=3, strict=True)
    checklist: list[TaskChecklistDefinition] = Field(default_factory=list, max_length=100)
    deliverable: str | None = Field(default=None, max_length=4000, strict=True)
    acceptance_criteria: str | None = Field(default=None, max_length=4000, strict=True)

    @field_validator("deliverable", "acceptance_criteria", mode="before")
    @classmethod
    def trim_requirements(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def initial_definitions(self):
        if self.execution_protocol_version == 3:
            if self.checklist or self.deliverable is not None or self.acceptance_criteria is not None:
                raise ValueError("轻量任务不需要推进清单、交付要求或验收标准")
            return self
        if self.execution_protocol_version != 2 and (
            self.deliverable is not None or self.acceptance_criteria is not None
        ):
            raise ValueError("整体交付要求需要任务执行协议版本2")
        if self.execution_protocol_version is None:
            if self.checklist:
                raise ValueError("推进清单须显式指定执行协议版本1")
            return self
        if self.execution_protocol_version == 2 and (not self.deliverable or not self.acceptance_criteria):
            raise ValueError("请明确任务整体交付要求和验收标准")
        if self.execution_protocol_version == 1 and not self.checklist:
            raise ValueError("新协议任务须确认至少一项推进清单")
        if any(item.definition_version != 1 for item in self.checklist):
            raise ValueError("新任务推进项定义版本必须为1")
        if len({item.item_id for item in self.checklist}) != len(self.checklist):
            raise ValueError("推进项标识不能重复")
        return self
