"""Versioned, backend-owned Review facts and candidate output.

These contracts grant no access and perform no financial calculation. The
snapshot producer must query one authorized scope consistently and recheck
access before model dispatch and persistence. No client may supply these facts.
"""

import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from sales_backend.contracts.review_scope import ReviewResolvedScopeV2
from sales_backend.contracts.work_report_base import (
    ReportModel,
    ResolvedPeriod,
    ResolvedScope,
    ShortText,
)

ReviewKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,95}$")]
MetricCode = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ReviewRef = Annotated[
    str,
    StringConstraints(pattern=r"^review:[A-Za-z0-9_-]{1,100}$"),
]
DecimalText = Annotated[str, StringConstraints(strict=True, pattern=r"^-?\d{1,24}(?:\.\d{1,8})?$")]
METRIC_TOKEN = re.compile(r"\{\{metric:([a-z][a-z0-9_.-]{0,95})\}\}")


class ReviewEvidence(ReportModel):
    source_ref: ReviewRef
    kind: Literal[
        "metric_query",
        "target",
        "objective",
        "key_result",
        "task",
        "activity",
        "opportunity",
        "baseline",
    ]
    record_id: str = Field(min_length=1, max_length=128, strict=True)
    record_version: str = Field(min_length=1, max_length=128, strict=True)
    observed_at: AwareDatetime
    state_kind: Literal["occurred", "current", "planned", "declaration"]
    text: str = Field(min_length=1, max_length=16000, strict=True)
    target_status: Literal["effective", "pending"] | None = None

    @model_validator(mode="after")
    def target_state(self):
        if (self.kind == "target") != (self.target_status is not None):
            raise ValueError("目标来源必须明确生效或待审批状态")
        return self


class ReviewBaseline(ReportModel):
    """The producer attests completeness; capture time is never boundary time."""

    source_ref: ReviewRef
    boundary_at: AwareDatetime
    captured_at: AwareDatetime
    definition_version: int = Field(ge=1, strict=True)
    method: Literal["boundary_snapshot", "verified_event_reconstruction"]
    continuous_coverage: Literal[True]
    same_authorized_population: Literal[True]

    @model_validator(mode="after")
    def capture_time(self):
        if self.method == "boundary_snapshot" and self.captured_at != self.boundary_at:
            raise ValueError("延迟采集的现值不能充当历史边界快照")
        if self.method == "verified_event_reconstruction" and self.captured_at < self.boundary_at:
            raise ValueError("事件重建不能发生在目标边界之前")
        return self


class ReviewMeasure(ReportModel):
    measure_id: ReviewKey
    metric_code: MetricCode
    definition_version: int = Field(ge=1, strict=True)
    role: Literal[
        "current",
        "forecast",
        "effective_target",
        "gap",
        "rate",
        "day_change",
        "week_change",
        "kr_progress",
    ]
    value: DecimalText | None
    unit: ShortText
    currency: Literal["CNY"] | None = None
    tax_basis: Literal["tax_inclusive", "tax_exclusive", "not_applicable"]
    coverage: Literal["complete", "partial", "unavailable"]
    missing_reasons: list[ShortText] = Field(default_factory=list)
    source_refs: list[ReviewRef] = Field(min_length=1)
    comparison_boundary: AwareDatetime | None = None
    baseline: ReviewBaseline | None = None

    @model_validator(mode="after")
    def completeness(self):
        if len(set(self.source_refs)) != len(self.source_refs):
            raise ValueError("统计来源不能重复")
        if self.coverage == "complete":
            if self.value is None or self.missing_reasons:
                raise ValueError("完整统计必须有值且没有取数缺口")
        elif self.value is not None or not self.missing_reasons:
            raise ValueError("不完整统计不能把已知小计冒充全量值，必须保留缺口")
        comparison = self.role in {"day_change", "week_change"}
        if comparison != (self.comparison_boundary is not None):
            raise ValueError("变化统计必须显式冻结所需比较边界")
        if comparison and (self.value is not None) != (self.baseline is not None):
            raise ValueError("变化值必须对应可信历史基线；无基线则保持未知")
        if not comparison and self.baseline is not None:
            raise ValueError("非变化统计不能附带历史比较基线")
        if self.baseline and self.baseline.definition_version != self.definition_version:
            raise ValueError("历史比较的指标定义版本必须相同")
        if self.baseline and self.baseline.boundary_at != self.comparison_boundary:
            raise ValueError("基线必须精确对应所需比较边界，不能替换为其他日期")
        if self.metric_code == "forecast_recognized" and self.tax_basis != "tax_inclusive":
            raise ValueError("预测确收及其独立目标采用含税口径")
        if self.metric_code == "recognized" and self.tax_basis != "tax_exclusive":
            raise ValueError("实际确收保持不含税口径")
        return self


class ReviewGroup(ReportModel):
    group_id: str = Field(min_length=1, max_length=110, strict=True)
    metric_code: MetricCode
    key_result_id: UUID | None = None
    objective_id: UUID | None = None
    title: ShortText
    source_refs: list[ReviewRef]
    measure_refs: list[ReviewKey]

    @model_validator(mode="after")
    def identity(self):
        expected = f"kr:{self.key_result_id}" if self.key_result_id else f"metric:{self.metric_code}"
        if self.group_id != expected or (self.objective_id and not self.key_result_id):
            raise ValueError("分组必须对应冻结指标或KR，不允许模型新增归属")
        if len(set(self.source_refs)) != len(self.source_refs) or len(set(self.measure_refs)) != len(self.measure_refs):
            raise ValueError("分组引用不能重复")
        return self


class ReviewFacts(ReportModel):
    contract_version: Literal["work_report.review_facts.v1"] = "work_report.review_facts.v1"
    period: ResolvedPeriod
    scope: ResolvedScope
    permission_version: str = Field(min_length=1, max_length=128)
    rules_version: str = Field(min_length=1, max_length=128)
    data_as_of: AwareDatetime
    evidence: list[ReviewEvidence]
    measures: list[ReviewMeasure]
    groups: list[ReviewGroup]
    missing_data: list[ShortText] = Field(default_factory=list)

    @model_validator(mode="after")
    def frozen_references(self):
        evidence = {row.source_ref: row for row in self.evidence}
        measures = {row.measure_id: row for row in self.measures}
        if (
            len(evidence) != len(self.evidence)
            or len(measures) != len(self.measures)
            or len({row.group_id for row in self.groups}) != len(self.groups)
        ):
            raise ValueError("Review来源、统计和分组标识必须唯一")
        if len(set(self.missing_data)) != len(self.missing_data):
            raise ValueError("取数缺口不能重复")
        if any(row.observed_at > self.data_as_of for row in self.evidence):
            raise ValueError("来源取数时点不能晚于冻结快照")
        for measure in self.measures:
            if not set(measure.source_refs) <= evidence.keys():
                raise ValueError("统计引用不属于同一冻结快照")
            if not set(measure.missing_reasons) <= set(self.missing_data):
                raise ValueError("统计缺口必须保留在快照缺口中")
            sources = [evidence[ref] for ref in measure.source_refs]
            if any(row.state_kind in {"planned", "declaration"} or row.kind == "task" for row in sources):
                raise ValueError("任务贡献声明或计划不能转成正式指标进展")
            if measure.role == "effective_target" and any(
                row.kind != "target" or row.target_status != "effective" for row in sources
            ):
                raise ValueError("生效目标必须引用授权目标版本")
            if measure.comparison_boundary and measure.comparison_boundary > self.data_as_of:
                raise ValueError("比较边界不能晚于取数时点")
            if measure.role == "week_change" and measure.comparison_boundary != self.period.start:
                raise ValueError("周变化必须比较所选报告周期的起点")
            if measure.baseline:
                baseline = measure.baseline
                source = evidence.get(baseline.source_ref)
                if (
                    source is None
                    or source.kind != "baseline"
                    or baseline.source_ref not in measure.source_refs
                    or baseline.captured_at > self.data_as_of
                ):
                    raise ValueError("历史基线必须来自有效边界及同次授权来源")
        for group in self.groups:
            if not set(group.source_refs) <= evidence.keys() or not set(group.measure_refs) <= measures.keys():
                raise ValueError("分组引用不属于同一冻结快照")
            if any(measures[ref].metric_code != group.metric_code for ref in group.measure_refs):
                raise ValueError("不能把其他指标的统计放进当前分组")
        return self


class ReviewFactsV2(ReviewFacts):
    """Multi-subject Review facts; the v1 contract remains single-scope only."""

    contract_version: Literal["work_report.review_facts.v2"] = "work_report.review_facts.v2"
    scope: ReviewResolvedScopeV2


def validate_review_facts(value):
    """Parse the frozen facts by their explicit contract version."""
    raw = value.model_dump(mode="python") if hasattr(value, "model_dump") else value
    version = raw.get("contract_version") if isinstance(raw, dict) else None
    if version == "work_report.review_facts.v2":
        return ReviewFactsV2.model_validate(raw)
    return ReviewFacts.model_validate(raw)


class ReviewStatement(ReportModel):
    text: str = Field(min_length=1, max_length=2000, strict=True)
    source_refs: list[ReviewRef] = Field(default_factory=list)
    measure_refs: list[ReviewKey] = Field(default_factory=list)

    @model_validator(mode="after")
    def references(self):
        if not self.text.strip() or not (self.source_refs or self.measure_refs):
            raise ValueError("每条内容必须有正文及可回查依据")
        if len(set(self.source_refs)) != len(self.source_refs) or len(set(self.measure_refs)) != len(self.measure_refs):
            raise ValueError("引用不能重复")
        if set(METRIC_TOKEN.findall(self.text)) != set(self.measure_refs):
            raise ValueError("统计占位符必须与统计引用完全一致")
        if "{{metric:" in METRIC_TOKEN.sub("", self.text):
            raise ValueError("统计占位符无效")
        return self


class ReviewSuggestion(ReviewStatement):
    kind: Literal["suggestion"] = "suggestion"
    requires_confirmation: Literal[True] = True


class DailyReviewItem(ReportModel):
    fact: ReviewStatement
    impact_or_risk: ReviewStatement
    next_step: ReviewSuggestion


class DailyReviewContent(ReportModel):
    contract_version: Literal["work_report.review_daily.v1"] = "work_report.review_daily.v1"
    items: list[DailyReviewItem] = Field(min_length=1, max_length=3)
    missing_data: list[ShortText]


class WeeklyReviewProgress(ReviewStatement):
    kind: Literal["fact", "current", "plan", "declaration"]


class WeeklyReviewGroup(ReportModel):
    group_id: str = Field(min_length=1, max_length=110, strict=True)
    progress: list[WeeklyReviewProgress] = Field(max_length=30)
    risks: list[ReviewStatement] = Field(max_length=30)
    next_week_tasks: list[ReviewSuggestion] = Field(max_length=20)


class WeeklyReviewContent(ReportModel):
    contract_version: Literal["work_report.review_weekly.v1"] = "work_report.review_weekly.v1"
    groups: list[WeeklyReviewGroup]
    missing_data: list[ShortText]


class ReviewWireItem(ReviewStatement):
    """Keep the deployed work-report four-field item protocol."""

    kind: Literal["fact", "summary", "plan", "suggestion"]
    measure_refs: list[ReviewKey] = Field(default_factory=list, alias="metric_refs")


class ReviewWireSection(ReportModel):
    id: ReviewKey
    title: ShortText
    items: list[ReviewWireItem] = Field(max_length=30)


class ReviewWireContent(ReportModel):
    title: ShortText
    sections: list[ReviewWireSection] = Field(min_length=1)
    missing_data: list[ShortText]

    @model_validator(mode="after")
    def content_bounds(self):
        if len({row.id for row in self.sections}) != len(self.sections):
            raise ValueError("模型章节不能重复")
        if sum(len(item.text) for row in self.sections for item in row.items) > 64000:
            raise ValueError("模型正文超过上限，不能静默截断")
        return self
