"""Additive task alignment. Contributions are declarations, never booked performance."""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sales_backend.contracts.types import UUIDString

MetricCode = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class TaskContribution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_code: MetricCode
    value: str = Field(strict=True, pattern=r"^(0|[1-9][0-9]{0,15})(?:\.[0-9]{1,2})?$")
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("note")
    @classmethod
    def strip_note(cls, value):
        return value.strip() if value is not None else None


class TaskActualContribution(TaskContribution):
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("note")
    @classmethod
    def evidence_required(cls, value):
        if not value.strip():
            raise ValueError("实际贡献声明须填写说明；不会自动计入业绩")
        return value.strip()


class TaskAlignmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_codes: list[MetricCode] = Field(default_factory=list, max_length=20)
    key_result_ids: list[UUIDString] = Field(default_factory=list, max_length=20)
    expected_contribution: TaskContribution | None = None

    @model_validator(mode="after")
    def bounded_distinct_links(self):
        self.metric_codes = sorted(set(self.metric_codes))
        self.key_result_ids = sorted(set(self.key_result_ids))
        if not self.metric_codes and not self.key_result_ids:
            raise ValueError("请选择至少一个指标或类别；可选择日常事务")
        return self


# ECMAScript String.trim whitespace, shared explicitly with the V244 SQL boundary.
ECMASCRIPT_WHITESPACE = (
    "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680"
    + "".join(chr(code) for code in range(0x2000, 0x200B))
    + "\u2028\u2029\u202f\u205f\u3000\ufeff"
)


class TaskAlignmentV2Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_ids: list[UUIDString] = Field(default_factory=list, max_length=20)
    key_result_ids: list[UUIDString] = Field(default_factory=list, max_length=20)
    other_reason: str | None = None
    expected_contribution: TaskContribution | None = None

    @field_validator("other_reason", mode="before")
    @classmethod
    def normalize_reason(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("其他必要性原因须为文字")
        return value.strip(ECMASCRIPT_WHITESPACE) or None

    @model_validator(mode="after")
    def explicit_object_or_reason(self):
        self.target_ids = sorted(set(self.target_ids))
        self.key_result_ids = sorted(set(self.key_result_ids))
        objects = bool(self.target_ids or self.key_result_ids)
        if objects == bool(self.other_reason):
            raise ValueError("请选择具体目标或KR，或选择其他并填写必要性原因，不能同时选择")
        if self.other_reason is not None and len(self.other_reason) > 2000:
            raise ValueError("其他必要性原因不能超过2000字")
        if not objects and self.expected_contribution is not None:
            raise ValueError("其他任务不能填写指标贡献声明")
        return self


def alignment_request(values):
    """Select one protocol before union parsing; never infer V2 from extra fields."""
    if not isinstance(values, dict):
        return values
    values = dict(values)
    version = values.get("alignment_contract_version")
    if version is not None and (type(version) is not int or version != 2):
        raise ValueError("不支持的任务关联契约版本")
    alignment = values.get("alignment")
    if version == 2 and alignment is None:
        raise ValueError("V2任务须明确关联目标/KR或填写其他必要性原因")
    if alignment is not None:
        if isinstance(alignment, BaseModel):
            alignment = alignment.model_dump()
        model = TaskAlignmentV2Input if version == 2 else TaskAlignmentInput
        values["alignment"] = model.model_validate(alignment)
    return values


class TaskAlignmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_no: int = Field(strict=True, ge=1, le=2147483647)
    alignment_contract_version: int | None = Field(default=None, strict=True, ge=2, le=2)
    alignment: TaskAlignmentInput | TaskAlignmentV2Input

    @model_validator(mode="before")
    @classmethod
    def protocol(cls, values):
        return alignment_request(values)


def legacy_safe_task_payload(value: Any) -> Any:
    """Preserve pre-V242 idempotency bytes for requests without the new fields."""
    if isinstance(value, dict):
        return {
            key: legacy_safe_task_payload(item)
            for key, item in value.items()
            if not (key in {"alignment", "actual_contribution", "alignment_contract_version"} and item is None)
        }
    if isinstance(value, list):
        return [legacy_safe_task_payload(item) for item in value]
    return value
