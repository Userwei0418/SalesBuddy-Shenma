"""Work report v1: separate trusted facts, writing configuration and draft output.

These are data contracts, not permission grants. Backend prepares snapshots and
rechecks source access on every read/write/export; the generator only proposes
content. No model endpoint, credential, SQL or business action enters this API.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, field_validator, model_validator

from sales_backend.contracts.review_scope import ReviewResolvedScopeV2, WorkReportScopeSelectionV2
from sales_backend.contracts.work_report_base import (
    Digest,
    PeriodKind,
    PeriodSelection,
    ReportError,
    ReportModel,
    ResolvedPeriod,
    ResolvedScope,
    ScopeSelection,
    ShortText,
)
from sales_backend.contracts.work_report_meeting import MeetingFacts
from sales_backend.contracts.work_report_overview import OverviewFacts, OverviewGenerationFacts
from sales_backend.contracts.work_report_review import (
    DailyReviewContent,
    ReviewFacts,
    ReviewFactsV2,
    WeeklyReviewContent,
)

CONTRACT_VERSION = 'work_report.v1'
METRIC_TOKEN = re.compile(r'\{\{metric:([a-z][a-z0-9_.-]{0,95})\}\}')
Key = Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9_.-]{0,95}$')]
SourceId = Annotated[str, StringConstraints(pattern=r'^(visit|opportunity|task|risk|actual|user|overview):[A-Za-z0-9_-]{1,100}$')]
SourceType = Literal['visit', 'opportunity', 'task', 'risk', 'actual']
SOURCE_TYPES = frozenset(('visit', 'opportunity', 'task', 'risk'))
WEEK_FACTS_CONTRACT = 'work_report.sales_week_facts.v1'
WeekContract = Literal['work_report.sales_week_facts.v1']
WeekSectionId = Literal['summary', 'actual_collection', 'next_week_plan', 'next_week_collection', 'ppl_first_30', 'ppl_open_50']
WEEK_SECTIONS = ('summary', 'actual_collection', 'next_week_plan', 'next_week_collection', 'ppl_first_30', 'ppl_open_50')






class SupplementInput(ReportModel):
    id: UUID
    text: str = Field(min_length=1, max_length=16000, strict=True)
    declared_kind: Literal['completed', 'context', 'plan'] = 'context'
    section_id: WeekSectionId | None = None


class SnapshotSelection(ReportModel):
    overview_binding: str | None = Field(default=None, min_length=1, max_length=8192)
    review_mode: Literal['daily', 'weekly'] | None = None
    facts_contract: WeekContract | None = None
    period: PeriodSelection = Field(default_factory=PeriodSelection)
    scope: ScopeSelection | WorkReportScopeSelectionV2 = Field(default_factory=ScopeSelection)
    sources: list[SourceType] = Field(default_factory=lambda: ['visit', 'opportunity'], max_length=5)
    supplements: list[SupplementInput] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def unique_materials(self):
        if self.review_mode:
            if self.overview_binding:
                raise ValueError('Review模式不能复用旧概览绑定')
            expected_period = 'day' if self.review_mode == 'daily' else 'week'
            if self.period.kind != expected_period:
                raise ValueError('Review模式与所选报告周期不一致')
        if self.facts_contract is not None and self.period.kind != 'week':
            raise ValueError('销售六节事实只适用于周报')
        if self.facts_contract is None and ('actual' in self.sources or any(s.section_id for s in self.supplements)):
            raise ValueError('财务来源和六节补充须显式选择销售周报事实版本')
        if len(self.sources) != len(set(self.sources)) or len({s.id for s in self.supplements}) != len(self.supplements):
            raise ValueError('来源或补充材料标识不能重复')
        if not self.sources and not self.supplements and self.review_mode is None:
            raise ValueError('至少选择一种来源或填写补充材料')
        return self


class SourceCoverage(ReportModel):
    source_type: SourceType
    selected: bool = Field(strict=True)
    status: Literal['complete', 'partial', 'failed', 'not_selected']
    total: int | None = Field(default=None, ge=0, strict=True)
    included: int = Field(ge=0, strict=True)
    truncated: bool = Field(default=False, strict=True)
    statistics_complete: bool = Field(default=False, strict=True)
    missing_reasons: list[ShortText] = Field(default_factory=list)

    @model_validator(mode='after')
    def honest_coverage(self):
        if self.selected == (self.status == 'not_selected'):
            raise ValueError('来源选择与覆盖状态不一致')
        if self.total is not None and self.included > self.total:
            raise ValueError('已纳入数量不能超过已知总量')
        if self.status == 'complete' and (self.total is None or self.included != self.total or self.truncated
                                         or not self.statistics_complete or self.missing_reasons):
            raise ValueError('完整覆盖必须有精确总量、完整材料和统计')
        if self.status in ('partial', 'failed') and not self.missing_reasons:
            raise ValueError('部分覆盖或读取失败必须说明缺口')
        if self.status == 'not_selected' and (self.included or self.truncated or self.total is not None
                                              or self.statistics_complete or self.missing_reasons):
            raise ValueError('未选来源不能包含材料')
        return self


class PersonReference(ReportModel):
    user_id: UUID
    name: ShortText


class ActualPaymentLineage(ReportModel):
    source_ref: SourceId
    record_id: UUID
    effective_fact_id: str = Field(min_length=1, max_length=240)
    customer_id: UUID
    opportunity_id: UUID | None
    year: int = Field(ge=1900, le=9999)
    quarter: int = Field(ge=1, le=4)
    kind: Literal['collection'] = 'collection'
    source: Literal['operations', 'entries', 'historical']
    status: str = Field(min_length=1, max_length=60)
    amount: str | None
    currency: Literal['CNY'] = 'CNY'
    occurred_on: date | None
    period_precision: Literal['day', 'quarter']
    period_start: date
    period_end: date
    complete: bool
    reference_changed: bool
    source_fingerprint: str | None
    resolution_version: int = Field(ge=0)
    projection_fingerprint: Digest
    included_in_week: bool

    @model_validator(mode='after')
    def exact_precision(self):
        if not self.source_ref.startswith('actual:') or str(self.record_id) != self.effective_fact_id:
            raise ValueError('回款来源必须对应真实有效叶事实')
        if (self.period_precision == 'day') != (self.occurred_on is not None):
            raise ValueError('回款日期精度不一致')
        if self.amount is not None and (not re.fullmatch(r'-?\d{1,24}(?:\.\d{1,8})?', self.amount)):
            raise ValueError('回款金额必须为精确十进制文本')
        return self


class FactSource(ReportModel):
    source_id: SourceId
    source_type: SourceType
    record_id: UUID
    record_version: str | None = Field(default=None, max_length=128)
    business_at: AwareDatetime | None
    observed_at: AwareDatetime
    time_basis: Literal['interaction_at', 'business_created_on', 'completed_at', 'opened_at', 'resolved_at', 'event_at', 'observed_at', 'occurred_on', 'due_at']
    state_kind: Literal['occurred', 'current', 'planned']
    title: ShortText
    text: str = Field(max_length=16000, strict=True)
    contributors: list[PersonReference] = Field(default_factory=list)
    truncated: bool = Field(default=False, strict=True)
    actual_lineage: ActualPaymentLineage | None = None

    @model_validator(mode='after')
    def source_prefix(self):
        if not self.source_id.startswith(self.source_type + ':'):
            raise ValueError('事实来源类型与引用标识不一致')
        if (self.source_type == 'actual') != (self.actual_lineage is not None):
            raise ValueError('财务来源必须保留采用链')
        if self.actual_lineage and (self.source_id != self.actual_lineage.source_ref or self.record_id != self.actual_lineage.record_id):
            raise ValueError('财务来源引用与采用链不一致')
        if self.actual_lineage and self.record_version != self.actual_lineage.projection_fingerprint:
            raise ValueError('财务来源版本与冻结指纹不一致')
        return self


class TrustedMetric(ReportModel):
    metric_id: Key
    label: ShortText
    kind: Literal['count', 'amount', 'ratio']
    value: str | None  # exact decimal text, never binary float
    unit: ShortText
    currency: str | None = Field(default=None, pattern=r'^[A-Z]{3}$')
    display_value: ShortText
    definition: str = Field(min_length=1, max_length=2000)
    period_basis: Literal['occurred_in_period', 'current_at_cutoff', 'current_at_snapshot', 'planned_in_period']
    complete: bool = Field(strict=True)

    @model_validator(mode='after')
    def decimal_value(self):
        if self.value is not None:
            if not re.fullmatch(r'-?\d{1,24}(?:\.\d{1,8})?', self.value) or not Decimal(self.value).is_finite():
                raise ValueError('统计值必须为有限精确十进制字符串或null')
            if self.kind == 'count' and ('.' in self.value or Decimal(self.value) < 0):
                raise ValueError('计数必须是非负整数')
        if self.kind == 'amount' and self.currency is None:
            raise ValueError('金额统计必须明确币种')
        return self


class UserSupplement(ReportModel):
    source_id: SourceId
    author_id: UUID
    text: str = Field(min_length=1, max_length=16000, strict=True)
    declared_kind: Literal['completed', 'context', 'plan']
    section_id: WeekSectionId | None = None

    @model_validator(mode='after')
    def user_prefix(self):
        if not self.source_id.startswith('user:'):
            raise ValueError('用户补充不能冒充系统事实')
        return self


class WeekBounds(ReportModel):
    start: AwareDatetime
    end_exclusive: AwareDatetime
    timezone: Literal['Asia/Shanghai'] = 'Asia/Shanghai'


class SectionFacts(ReportModel):
    section_id: WeekSectionId
    availability: Literal['available', 'partial', 'unknown', 'not_selected']
    source_refs: list[SourceId] = Field(default_factory=list)
    metric_refs: list[Key] = Field(default_factory=list)
    missing_codes: list[Key] = Field(default_factory=list)
    missing_reasons: list[ShortText] = Field(default_factory=list)
    scope_basis: Literal['current_authorized_scope'] = 'current_authorized_scope'
    population_total: int | None = Field(default=None, ge=0)
    included: int = Field(default=0, ge=0)
    population_complete: bool = False
    content_complete: bool = False
    observed_at: AwareDatetime
    period_basis: Literal['occurred_in_period', 'current_at_snapshot', 'planned_in_period']


class FirstThresholdFact(ReportModel):
    threshold: Literal[30] = 30
    status: Literal['proven', 'unknown'] = 'unknown'
    event_id: UUID | None = None
    occurred_at: AwareDatetime | None = None
    entry_kind: Literal['created_at_or_above', 'crossed_from_below'] | None = None
    before_probability: int | None = None
    after_probability: int | None = None
    history_origin: Literal['native_creation', 'verified_external_history', 'unknown'] = 'unknown'
    trustworthy_from: AwareDatetime | None = None
    complete_from_origin: bool = False
    reason_codes: list[Key] = Field(default_factory=lambda: ['threshold_history_incomplete'])

    @model_validator(mode='after')
    def proof_required(self):
        if self.status == 'proven' and (not self.complete_from_origin or self.history_origin == 'unknown'
                or self.event_id is None or self.occurred_at is None or self.trustworthy_from is None
                or self.entry_kind is None or self.after_probability is None or self.after_probability < 30):
            raise ValueError('首次达到必须有完整历史证明')
        return self


class OpportunityHistoryGuard(ReportModel):
    """Private complete-chain dependency, including unknown and never-reached rows."""
    opportunity_id: UUID
    current_version: int = Field(ge=1)
    event_count: int = Field(ge=0)
    head_event_id: UUID | None
    history_sha256: Digest
    first_event_id: UUID | None
    first_event_version: int | None = Field(ge=1)

    @model_validator(mode='after')
    def chain_shape(self):
        if (self.event_count == 0) != (self.head_event_id is None):
            raise ValueError('商机历史头与事件数量不一致')
        if (self.first_event_id is None) != (self.first_event_version is None):
            raise ValueError('商机首次达到历史引用不完整')
        return self


class OpportunityFact(ReportModel):
    source_ref: SourceId
    opportunity_id: UUID
    opportunity_version: int = Field(ge=1)
    probability: int | None
    stage_code: str
    status: str
    qualified_as: list[Literal['first_ge30_in_week', 'current_open_ge50']]
    progress_refs: list[SourceId] = Field(default_factory=list)
    next_action_refs: list[SourceId] = Field(default_factory=list)
    progress_state: Literal['recorded_in_week', 'no_recorded_progress_in_week', 'unavailable']
    next_action_state: Literal['recorded', 'not_recorded', 'unavailable']
    first_ge30: FirstThresholdFact | None = None


class TransitionCoverage(ReportModel):
    candidate_count: int = Field(ge=0)
    history_complete_count: int = Field(ge=0)
    history_unknown_count: int = Field(ge=0)
    proven_first_count: int = Field(ge=0)
    cohort_complete: bool
    trustworthy_from: AwareDatetime | None = None
    missing_codes: list[Key] = Field(default_factory=list)


class ActualPeriodDependency(ReportModel):
    """Private access dependency for a null period, never a fabricated fact ID."""
    effective_fact_id: str = Field(pattern=r'^period:', max_length=240)
    customer_id: UUID
    opportunity_id: UUID | None
    year: int = Field(ge=1900, le=9999)
    quarter: int = Field(ge=1, le=4)
    source_fingerprint: str | None
    resolution_version: int = Field(ge=0)
    status: str = Field(min_length=1, max_length=60)
    projection_fingerprint: Digest


class CollectionCoverage(ReportModel):
    lines: list[ActualPaymentLineage]
    dependencies: list[ActualPeriodDependency] = Field(default_factory=list)
    day_precision_confirmed_subtotal: str | None
    weekly_total: str | None
    weekly_total_complete: bool
    unallocated_period_count: int = Field(ge=0)
    period_only_collection_amount: str | None
    incomplete_period_count: int = Field(ge=0)
    conflict_period_count: int = Field(ge=0)
    precision_warning: Literal['not_allocated_to_days'] | None

    @model_validator(mode='after')
    def complete_amount(self):
        for value in (self.day_precision_confirmed_subtotal, self.weekly_total, self.period_only_collection_amount):
            if value is not None and not re.fullmatch(r'-?\d{1,24}(?:\.\d{1,8})?', value):
                raise ValueError('回款统计必须是精确十进制文本')
        if self.weekly_total_complete != (self.weekly_total is not None):
            raise ValueError('未知回款总额不能转成零')
        if self.weekly_total_complete and (self.unallocated_period_count or self.incomplete_period_count or self.conflict_period_count):
            raise ValueError('有未分配或不完整来源不能核定周总额')
        return self


class SalesWeekFacts(ReportModel):
    facts_contract: WeekContract = WEEK_FACTS_CONTRACT
    data_as_of: AwareDatetime
    next_period: WeekBounds
    sections: list[SectionFacts] = Field(min_length=6, max_length=6)
    finance: CollectionCoverage | None = None
    opportunity_facts: list[OpportunityFact] = Field(default_factory=list)
    transition_coverage: TransitionCoverage
    history_guards: list[OpportunityHistoryGuard] = Field(default_factory=list)

    @model_validator(mode='after')
    def section_identity(self):
        if tuple(s.section_id for s in self.sections) != WEEK_SECTIONS:
            raise ValueError('销售周报事实章节必须完整且顺序固定')
        if len({g.opportunity_id for g in self.history_guards}) != len(self.history_guards):
            raise ValueError('商机历史依赖不能重复')
        return self


class SchemeSection(ReportModel):
    id: Key
    title: ShortText
    enabled: bool = Field(default=True, strict=True)
    instructions: str = Field(default='', max_length=2000, strict=True)


class ReportStyle(ReportModel):
    audience: str = Field(default='直属负责人', min_length=1, max_length=120)
    tone: Literal['formal', 'concise', 'narrative'] = 'concise'
    length: Literal['short', 'medium', 'long'] = 'medium'
    voice: Literal['first_person', 'team'] = 'first_person'
    language: Literal['zh-CN'] = 'zh-CN'
    focus: list[ShortText] = Field(default_factory=list, max_length=20)


class ReferenceText(ReportModel):
    id: UUID
    version_no: int = Field(ge=1, strict=True)
    text: str = Field(min_length=1, max_length=20000, strict=True)


class SchemeConfig(ReportModel):
    facts_contract: WeekContract | None = None
    sections: list[SchemeSection] = Field(min_length=1, max_length=20)
    group_by: Literal['customer', 'opportunity', 'member', 'topic'] = 'topic'
    style: ReportStyle = Field(default_factory=ReportStyle)
    reference_texts: list[ReferenceText] = Field(default_factory=list, max_length=5)
    default_period: PeriodKind = 'week'
    default_sources: list[SourceType] = Field(default_factory=lambda: ['visit', 'opportunity'], max_length=5)

    @model_validator(mode='after')
    def configuration(self):
        if self.facts_contract and self.default_period != 'week':
            raise ValueError('销售六节默认周期必须是周')
        if self.facts_contract is None and 'actual' in self.default_sources:
            raise ValueError('财务来源须选择销售周报事实版本')
        if not any(s.enabled for s in self.sections) or len({s.id for s in self.sections}) != len(self.sections):
            raise ValueError('至少启用一个章节，章节标识不能重复')
        if len(set(self.default_sources)) != len(self.default_sources):
            raise ValueError('默认来源不能重复')
        if len({r.id for r in self.reference_texts}) != len(self.reference_texts):
            raise ValueError('范文标识不能重复')
        if sum(len(r.text) for r in self.reference_texts) > 60000:
            raise ValueError('文本范文总计最多60000字符')
        return self


class SchemeReference(ReportModel):
    scheme_id: UUID
    version_no: int = Field(ge=1, strict=True)


class SchemeSnapshot(SchemeReference):
    name: ShortText
    config: SchemeConfig
    config_hash: Digest


class ReportSourceNote(ReportModel):
    """Backend-retained evidence, separate from editable narrative prose."""
    title: ShortText
    notice_text: str = Field(min_length=1, max_length=12000, strict=True)
    text: str = Field(min_length=1, max_length=12000, strict=True)
    source_refs: list[SourceId] = Field(min_length=1, max_length=200)

    @model_validator(mode='after')
    def literal_unique_sources(self):
        if len(set(self.source_refs)) != len(self.source_refs):
            raise ValueError('来源说明引用不能重复')
        if any('{{metric:' in value for value in (self.title, self.notice_text, self.text)):
            raise ValueError('来源说明只能包含原文，不能包含统计占位符')
        return self

    def text_size(self):
        return len(self.title) + len(self.notice_text) + len(self.text)


class ReportItem(ReportModel):
    kind: Literal['fact', 'summary', 'plan', 'suggestion', 'user_supplement', 'manual']
    text: str = Field(min_length=1, max_length=12000, strict=True)
    source_refs: list[SourceId] = Field(default_factory=list)
    metric_refs: list[Key] = Field(default_factory=list)
    source_notes: list[ReportSourceNote] | None = Field(default=None, max_length=200)

    @field_validator('source_notes')
    @classmethod
    def omit_empty_notes(cls, value):
        return value or None

    def text_size(self):
        return len(self.text) + sum(note.text_size() for note in self.source_notes or [])

    @model_validator(mode='after')
    def token_references(self):
        if self.text_size() > 12000:
            raise ValueError('段落及来源说明最多12000字符，不能静默截断')
        if len(set(self.source_refs)) != len(self.source_refs) or len(set(self.metric_refs)) != len(self.metric_refs):
            raise ValueError('引用不能重复')
        if set(METRIC_TOKEN.findall(self.text)) != set(self.metric_refs):
            raise ValueError('统计占位符必须与metric_refs完全对应')
        if '{{metric:' in METRIC_TOKEN.sub('', self.text):
            raise ValueError('统计占位符格式无效')
        if self.kind == 'manual' and (self.source_refs or self.metric_refs):
            raise ValueError('人工正文不能冒充自动核定的来源或统计')
        return self


class ReportSection(ReportModel):
    id: Key
    title: ShortText
    items: list[ReportItem] = Field(default_factory=list, max_length=200)


class ReportContent(ReportModel):
    title: ShortText
    sections: list[ReportSection] = Field(min_length=1, max_length=20)
    missing_data: list[ShortText] = Field(default_factory=list)
    review_content: DailyReviewContent | WeeklyReviewContent | None = None

    @model_validator(mode='after')
    def content_bounds(self):
        if len({s.id for s in self.sections}) != len(self.sections):
            raise ValueError('报告章节不能重复')
        review_size = 0
        if isinstance(self.review_content, DailyReviewContent):
            review_size = sum(len(row.text) for item in self.review_content.items
                              for row in (item.fact, item.impact_or_risk, item.next_step))
        elif isinstance(self.review_content, WeeklyReviewContent):
            review_size = sum(len(row.text) for group in self.review_content.groups
                              for row in [*group.progress, *group.risks, *group.next_week_tasks])
        if review_size + sum(item.text_size() for s in self.sections for item in s.items) > 64000:
            raise ValueError('报告正文最多64000字符，不能静默截断')
        return self


class RewriteContext(ReportModel):
    document_id: UUID
    base_revision_id: UUID
    base_document_version: int = Field(ge=1, strict=True)
    section_ids: list[Key] = Field(min_length=1, max_length=20)
    previous_content: ReportContent

    @model_validator(mode='after')
    def selected_sections(self):
        if len(set(self.section_ids)) != len(self.section_ids) or not set(self.section_ids) <= {s.id for s in self.previous_content.sections}:
            raise ValueError('重写章节必须唯一且存在于原修订')
        return self


class GenerationInput(ReportModel):
    contract_version: Literal['work_report.v1'] = CONTRACT_VERSION
    snapshot_id: UUID
    snapshot_hash: Digest
    period: ResolvedPeriod
    scope: ResolvedScope | ReviewResolvedScopeV2
    permission_version: str = Field(min_length=1, max_length=128)
    data_as_of: AwareDatetime
    coverage: list[SourceCoverage]
    sources: list[FactSource]  # no 300/500-row semantic cap; generation must batch
    metrics: list[TrustedMetric]
    supplements: list[UserSupplement]
    scheme: SchemeSnapshot
    business_rules: list[ShortText] = Field(default_factory=list)
    rules_version: str = Field(min_length=1, max_length=128)
    rewrite: RewriteContext | None = None
    sales_week_facts: SalesWeekFacts | None = None
    overview_facts: OverviewGenerationFacts | None = None
    review_facts: ReviewFacts | ReviewFactsV2 | None = None
    review_mode: Literal['daily', 'weekly'] | None = None

    @model_validator(mode='after')
    def trusted_material_contract(self):
        if (self.review_mode is not None) != (self.review_facts is not None):
            raise ValueError('Review生成模式和授权事实必须同时存在')
        if self.review_mode and self.period.kind != ('day' if self.review_mode == 'daily' else 'week'):
            raise ValueError('Review生成模式与冻结周期不一致')
        if self.review_mode and self.rewrite:
            raise ValueError('Review扩展请完整重新生成，暂不支持局部重写')
        coverage = {c.source_type: c for c in self.coverage}
        expected = SOURCE_TYPES | {'actual'} if self.sales_week_facts else SOURCE_TYPES
        if len(coverage) != len(self.coverage) or set(coverage) != expected:
            raise ValueError('所选事实版本的全部来源覆盖必须明确且唯一')
        if self.scheme.config.facts_contract and self.sales_week_facts is None:
            raise ValueError('销售六节方案须使用销售周报事实快照')
        if any(c.status == 'failed' for c in self.coverage):
            raise ValueError('来源读取失败不能转成模型调用或基础汇总')
        source_ids = [s.source_id for s in [*self.sources, *self.supplements]]
        if len(source_ids) != len(set(source_ids)) or len({m.metric_id for m in self.metrics}) != len(self.metrics):
            raise ValueError('来源和统计引用必须唯一')
        for kind, cov in coverage.items():
            rows = [s for s in self.sources if s.source_type == kind]
            if len({s.record_id for s in rows}) != cov.included or (rows and not cov.selected):
                raise ValueError('来源正文与覆盖数量不一致')
            if any(s.truncated for s in rows) and not cov.truncated:
                raise ValueError('原文裁剪不能隐藏在完整覆盖中')
        if self.sales_week_facts:
            from datetime import timedelta
            weekly = self.sales_week_facts
            if (self.period.kind != 'week' or weekly.data_as_of != self.data_as_of
                    or weekly.next_period.start != self.period.end_exclusive
                    or weekly.next_period.end_exclusive != self.period.end_exclusive + timedelta(days=7)):
                raise ValueError('销售周报时点或下周范围不一致')
            for section in weekly.sections:
                if (not set(section.source_refs) <= set(source_ids)
                        or not set(section.metric_refs) <= {m.metric_id for m in self.metrics}):
                    raise ValueError('销售周报章节引用不属于冻结快照')
            sources = {s.source_id: s for s in self.sources}
            for opportunity in weekly.opportunity_facts:
                source = sources.get(opportunity.source_ref)
                if (source is None or source.source_type != 'opportunity' or source.record_id != opportunity.opportunity_id
                        or source.record_version != str(opportunity.opportunity_version)
                        or not set(opportunity.progress_refs + opportunity.next_action_refs) <= sources.keys()):
                    raise ValueError('商机周报元信息与原始材料不一致')
            if weekly.history_guards:
                current = {s.record_id: s.record_version for s in self.sources
                           if s.source_type == 'opportunity' and s.state_kind == 'current'}
                guards = {g.opportunity_id: str(g.current_version) for g in weekly.history_guards}
                if current != guards or len(guards) != weekly.transition_coverage.candidate_count:
                    raise ValueError('完整商机集合与历史依赖不一致')
            actual_lines = [s.actual_lineage for s in self.sources if s.source_type == 'actual']
            if actual_lines != (weekly.finance.lines if weekly.finance else []):
                raise ValueError('回款覆盖与原始采用链不一致')
        if self.overview_facts:
            overview = self.overview_facts.facts
            if isinstance(self.scope, ReviewResolvedScopeV2):
                raise ValueError('旧概览事实不能与Review多范围快照混用')
            if (overview.data_as_of != self.data_as_of or overview.period != self.period
                    or overview.scope.member_ids != self.scope.member_ids
                    or overview.scope.team_id != self.scope.team_id):
                raise ValueError('概览事实与冻结范围或时点不一致')
        if self.review_facts:
            review = self.review_facts
            if (review.period != self.period or review.scope != self.scope
                    or review.data_as_of != self.data_as_of
                    or review.permission_version != self.permission_version
                    or review.rules_version != self.rules_version):
                raise ValueError('Review事实必须与同次授权快照的周期、范围、时点及规则一致')
            if isinstance(self.scope, ReviewResolvedScopeV2) != isinstance(review, ReviewFactsV2):
                raise ValueError('Review事实版本必须与冻结范围版本一致')
        return self


class GenerationTrace(ReportModel):
    prompt_version: Key
    fallback_reason: Key | None = None
    elapsed_ms: int = Field(ge=0, strict=True)
    input_tokens: int | None = Field(default=None, ge=0, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, strict=True)
    chunks_total: int = Field(ge=0, strict=True)
    chunks_completed: int = Field(ge=0, strict=True)


class GenerationResult(ReportModel):
    contract_version: Literal['work_report.v1'] = CONTRACT_VERSION
    status: Literal['generated', 'basic_summary', 'insufficient_data']
    provider: Literal['agent_platform', 'direct', 'none']
    snapshot_id: UUID
    snapshot_hash: Digest
    scheme_id: UUID
    scheme_version: int = Field(ge=1, strict=True)
    coverage_status: Literal['complete', 'partial']
    content: ReportContent | None
    trace: GenerationTrace

    @model_validator(mode='after')
    def honest_result(self):
        if self.trace.chunks_completed > self.trace.chunks_total:
            raise ValueError('完成批次数不能超过总批次')
        if self.status == 'generated' and (self.provider == 'none' or self.content is None
                                         or self.trace.chunks_total == 0
                                         or self.trace.chunks_completed != self.trace.chunks_total):
            raise ValueError('生成成功必须有完整批次结果与实际路线')
        if self.status == 'basic_summary' and (self.provider != 'none' or self.content is None):
            raise ValueError('基础汇总不能冒充模型生成')
        if self.status == 'insufficient_data' and (self.provider != 'none' or self.content is not None):
            raise ValueError('缺少材料不能生成正文')
        return self


class RunCreate(ReportModel):
    snapshot_id: UUID
    snapshot_hash: Digest
    scheme: SchemeReference
    scheme_override: SchemeConfig | None = None
    document_id: UUID | None = None
    version_no: int | None = Field(default=None, ge=1, strict=True)
    base_revision_id: UUID | None = None
    section_ids: list[Key] | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode='after')
    def edit_target(self):
        fields = (self.document_id, self.version_no, self.base_revision_id, self.section_ids)
        if any(v is not None for v in fields) and not all(v is not None for v in fields):
            raise ValueError('局部重写必须同时指定文档、版本、原修订和章节')
        if self.section_ids and len(set(self.section_ids)) != len(self.section_ids):
            raise ValueError('重写章节不能重复')
        return self


class VersionRequest(ReportModel):
    version_no: int = Field(ge=1, strict=True)


class RevisionCreate(VersionRequest):
    content: ReportContent


class ShareAudience(ReportModel):
    kind: Literal['members', 'team', 'company']
    member_ids: list[UUID] = Field(default_factory=list, max_length=100)
    team_id: UUID | None = None

    @model_validator(mode='after')
    def target(self):
        if ((self.kind == 'members' and (not self.member_ids or self.team_id))
                or (self.kind == 'team' and (not self.team_id or self.member_ids))
                or (self.kind == 'company' and (self.member_ids or self.team_id))
                or len(set(self.member_ids)) != len(self.member_ids)):
            raise ValueError('共享对象与范围类型不一致')
        return self


class ShareRequest(VersionRequest):
    confirm: Literal[True]
    audience: ShareAudience

    @field_validator('confirm', mode='before')
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError('共享必须明确确认confirm=true')
        return value


class SchemeCreate(ReportModel):
    name: ShortText
    config: SchemeConfig
    visibility: Literal['private', 'team', 'company'] = 'private'
    team_id: UUID | None = None

    @model_validator(mode='after')
    def sharing_scope(self):
        if (self.visibility == 'team') != (self.team_id is not None):
            raise ValueError('团队方案必须且只能指定一个团队')
        return self


class SchemeUpdate(SchemeCreate, VersionRequest):
    pass




class SchemeView(SchemeCreate, VersionRequest):
    id: UUID
    owner_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    can_edit: bool


class SnapshotView(ReportModel):
    id: UUID
    status: Literal['preparing', 'ready', 'failed']
    selection: SnapshotSelection
    period: ResolvedPeriod
    scope: ResolvedScope | ReviewResolvedScopeV2
    created_at: AwareDatetime
    data_as_of: AwareDatetime | None = None
    snapshot_hash: Digest | None = None
    coverage: list[SourceCoverage] = Field(default_factory=list)
    metrics: list[TrustedMetric] = Field(default_factory=list)
    error: ReportError | None = None
    sales_week_facts: SalesWeekFacts | None = None
    review_facts: ReviewFacts | ReviewFactsV2 | None = None

    @model_validator(mode='after')
    def ready_material(self):
        if self.status == 'ready' and self.selection.review_mode and self.review_facts is None:
            raise ValueError('Review就绪快照必须保留授权事实')
        if self.review_facts and (self.selection.review_mode is None
                or self.review_facts.period != self.period or self.review_facts.scope != self.scope
                or self.review_facts.data_as_of != self.data_as_of):
            raise ValueError('Review快照展示必须与冻结事实同范围同周期同时点')
        if self.review_facts and (isinstance(self.scope, ReviewResolvedScopeV2) != isinstance(self.review_facts, ReviewFactsV2)):
            raise ValueError('Review快照事实版本必须与冻结范围版本一致')
        expected = SOURCE_TYPES | {'actual'} if self.sales_week_facts else SOURCE_TYPES
        if self.status == 'ready' and (self.snapshot_hash is None or self.data_as_of is None
                                       or len(self.coverage) != len(expected)
                                       or {c.source_type for c in self.coverage} != expected
                                       or any(c.status == 'failed' for c in self.coverage)):
            raise ValueError('就绪快照必须有摘要、时点和全部来源覆盖结果')
        if (self.status == 'failed') != (self.error is not None):
            raise ValueError('失败快照必须有且只能在失败时有错误')
        return self


class RunView(ReportModel):
    id: UUID
    snapshot_id: UUID
    status: Literal['queued', 'running', 'succeeded', 'failed', 'cancelled']
    version_no: int = Field(ge=1, strict=True)
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    result_status: Literal['generated', 'basic_summary', 'insufficient_data'] | None = None
    provider: Literal['agent_platform', 'direct', 'none'] | None = None
    coverage_status: Literal['complete', 'partial'] | None = None
    document_id: UUID | None = None
    revision_id: UUID | None = None
    error: ReportError | None = None
    trace: GenerationTrace | None = None


class RevisionView(ReportModel):
    id: UUID
    document_id: UUID
    revision_no: int = Field(ge=1, strict=True)
    origin: Literal['generated', 'basic_summary', 'manual', 'rewrite']
    created_at: AwareDatetime
    created_by: UUID
    run_id: UUID | None = None
    content: ReportContent


class DocumentSchemeReference(SchemeReference):
    """Safe document projection; a share never publishes private writing examples."""
    name: ShortText
    config_hash: Digest


class DocumentQuery(ReportModel):
    q: str | None = Field(default=None, max_length=120)
    status: Literal['private', 'shared'] | None = None
    limit: int = Field(default=40, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class DocumentSummary(VersionRequest):
    id: UUID
    owner_id: UUID
    title: ShortText
    period: ResolvedPeriod
    scope: ResolvedScope | ReviewResolvedScopeV2
    snapshot_id: UUID
    snapshot_hash: Digest
    scheme: DocumentSchemeReference
    status: Literal['private', 'shared']
    latest_revision_id: UUID
    shared_revision_id: UUID | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    can_edit: bool
    can_share: bool
    can_export: bool


class DocumentView(DocumentSummary):
    overview_facts: OverviewFacts | None = None
    review_facts: ReviewFacts | ReviewFactsV2 | None = None
    meeting_facts: MeetingFacts | None = None
    revision: RevisionView  # owner sees latest; reader sees explicitly shared revision
    coverage: list[SourceCoverage]
    metrics: list[TrustedMetric]
    audience: ShareAudience | None = None


ItemT = TypeVar('ItemT')


class Page(ReportModel, Generic[ItemT]):
    items: list[ItemT]
    total: int = Field(ge=0, strict=True)
    limit: int = Field(ge=1, le=100, strict=True)
    offset: int = Field(ge=0, strict=True)
    has_more: bool
    next_offset: int | None = Field(default=None, ge=0, strict=True)


class ScopeOption(ReportModel):
    id: UUID
    name: ShortText


class SourceOption(ReportModel):
    id: SourceType
    label: ShortText
    available: bool
    unavailable_reason: ShortText | None = None


class ShareTargets(ReportModel):
    allowed_kinds: list[Literal['members', 'team', 'company']]
    members: list[ScopeOption]
    teams: list[ScopeOption]


class ExportView(ReportModel):
    document_id: UUID
    revision_id: UUID
    format: Literal['markdown', 'text']
    text: str
    filename: ShortText
    mime_type: Literal['text/markdown; charset=utf-8', 'text/plain; charset=utf-8']


class ReportOptions(ReportModel):
    contract_version: Literal['work_report.v1'] = CONTRACT_VERSION
    timezone: Literal['Asia/Shanghai'] = 'Asia/Shanghai'
    periods: list[PeriodKind] = Field(default_factory=lambda: ['day', 'week', 'two_weeks', 'month', 'quarter'])
    available_scope_kinds: list[Literal['self', 'member', 'team', 'workspace']]
    members: list[ScopeOption]
    teams: list[ScopeOption]
    share_targets: ShareTargets
    sources: list[SourceOption]
    default_scheme: SchemeConfig
    general_scheme: SchemeConfig | None = None
    builtin_schemes: list[SchemeSnapshot] | None = None
    can_create: bool
    can_share: bool
    can_export: bool
    generation_available: bool
