"""Meeting facts v1 contract. Not yet wired to an endpoint or old weekly paths.

Backend owns metrics/scope/history; clients only format immutable facts. These
models do not grant permission, reconstruct history or authorize publication.
"""
from decimal import Decimal, localcontext
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from sales_backend.contracts.work_report_base import (
    Digest, PeriodKind, PeriodSelection, ReportError, ReportModel, ResolvedPeriod, ShortText,
)

MEETING_FACTS_CONTRACT = 'work_report.meeting_facts.v1'
DecimalText = Annotated[str, StringConstraints(pattern=r'^-?\d{1,24}(?:\.\d{1,8})?$')]
Coverage = Literal['complete', 'partial', 'unavailable']
Metric = Literal['acv', 'quarter_revenue', 'quarter_collection']


class MeetingScopeSelection(ReportModel):
    kind: Literal['self', 'managed_team'] = 'self'
    team_id: UUID | None = None

    @model_validator(mode='after')
    def team_selector(self):
        if (self.kind == 'managed_team') != (self.team_id is not None):
            raise ValueError('所管理团队须选择团队，本人范围不能附带团队')
        return self


class MeetingResolvedScope(MeetingScopeSelection):
    label: ShortText
    member_ids: list[UUID]

    @model_validator(mode='after')
    def members(self):
        if len(self.member_ids) != len(set(self.member_ids)) or (self.kind == 'self' and len(self.member_ids) != 1):
            raise ValueError('范围成员须由后端去重解析，本人范围恰好一人')
        return self


class MeetingSelection(ReportModel):
    facts_contract: Literal['work_report.meeting_facts.v1'] = MEETING_FACTS_CONTRACT
    period: PeriodSelection = Field(default_factory=lambda: PeriodSelection(kind='day'))
    scope: MeetingScopeSelection = Field(default_factory=MeetingScopeSelection)


class MeetingQuarter(ReportModel):
    year: int = Field(ge=1900, le=9999)
    quarter: int = Field(ge=1, le=4)


class MoneyPoints(ReportModel):
    opening: DecimalText | None
    change: DecimalText | None
    closing: DecimalText | None


class MoneyCoverage(ReportModel):
    opening: Coverage
    change: Coverage
    closing: Coverage


class MeetingMoneyChange(MoneyPoints):
    currency: Literal['CNY'] = 'CNY'
    unit: Literal['元'] = '元'
    known_subtotals: MoneyPoints
    coverage: MoneyCoverage
    missing_reasons: list[ShortText] = Field(default_factory=list)

    @model_validator(mode='after')
    def honest_amounts(self):
        partial = False
        for name in ('opening', 'change', 'closing'):
            value, status = getattr(self, name), getattr(self.coverage, name)
            subtotal = getattr(self.known_subtotals, name)
            if (value is not None) != (status == 'complete'):
                raise ValueError('金额未知须为null；仅完整覆盖可报告总额')
            if value is not None and (subtotal is None or Decimal(subtotal) != Decimal(value)):
                raise ValueError('完整总额与已知小计须一致')
            if status == 'unavailable' and subtotal is not None:
                raise ValueError('全部未知不能伪造已知小计')
            partial |= status != 'complete'
        if partial and not self.missing_reasons:
            raise ValueError('缺失历史或金额须解释覆盖缺口')
        if self.opening is not None and self.closing is not None:
            with localcontext() as ctx:
                ctx.prec = 80
                if self.change is None or Decimal(self.change) != Decimal(self.closing)-Decimal(self.opening):
                    raise ValueError('可知期初期末时净变化必须精确等于期末减期初')
        elif self.change is not None:
            raise ValueError('未知端点不能推导完整净变化')
        return self


class MeetingAmounts(ReportModel):
    acv: MeetingMoneyChange
    quarter_revenue: MeetingMoneyChange
    quarter_collection: MeetingMoneyChange


class MeetingCount(ReportModel):
    value: int | None = Field(ge=0, strict=True)
    known_count: int = Field(ge=0, strict=True)
    coverage: Coverage
    missing_reasons: list[ShortText] = Field(default_factory=list)

    @model_validator(mode='after')
    def honest_count(self):
        if (self.value is not None) != (self.coverage == 'complete'):
            raise ValueError('未知计数须为null')
        if self.value is not None and self.value != self.known_count:
            raise ValueError('完整计数与已知计数须一致')
        if self.coverage != 'complete' and not self.missing_reasons:
            raise ValueError('不完整计数须说明原因')
        if self.coverage == 'unavailable' and self.known_count:
            raise ValueError('全部未知不能有已知计数')
        return self


class MeetingCounts(ReportModel):
    new_claimed_customers: MeetingCount
    formal_visits: MeetingCount
    new_opportunities: MeetingCount
    lost_opportunities: MeetingCount


class MeetingSource(ReportModel):
    source_id: ShortText
    kind: Literal['customer_claim', 'opportunity', 'opportunity_history', 'business_change', 'forecast_history', 'visit', 'task']
    record_id: UUID
    version: ShortText | None
    observed_at: AwareDatetime
    business_at: AwareDatetime | None
    source_hash: Digest


class MeetingItem(ReportModel):
    id: UUID
    name: ShortText
    customer_id: UUID | None = None
    customer_name: ShortText | None = None
    opportunity_id: UUID | None = None
    opportunity_name: ShortText | None = None
    occurred_at: AwareDatetime | None = None
    member_ids: list[UUID] = Field(default_factory=list)
    summary: str = Field(default='', max_length=16000)
    source_refs: list[ShortText] = Field(min_length=1)


class MeetingNextStep(ReportModel):
    id: ShortText
    opportunity_id: UUID | None = None
    member_ids: list[UUID] = Field(default_factory=list)
    text: str = Field(min_length=1, max_length=16000)
    due_at: AwareDatetime | None = None
    original_date_text: str | None = Field(default=None, max_length=240)
    source_refs: list[ShortText] = Field(min_length=1)


class MeetingChangeReason(ReportModel):
    code: Literal['new_opportunity', 'amount_change', 'forecast_change', 'lost', 'reopened',
                  'scope_transfer_in', 'scope_transfer_out', 'ownership_change', 'history_gap', 'other_recorded']
    text: ShortText
    source_refs: list[ShortText] = Field(default_factory=list)


class MeetingOpportunityChange(ReportModel):
    opportunity_id: UUID
    name: ShortText
    customer_id: UUID
    customer_name: ShortText | None = None
    amounts: MeetingAmounts
    opening_member_ids: list[UUID]
    closing_member_ids: list[UUID]
    reasons: list[MeetingChangeReason]
    follow_up_ids: list[UUID]
    next_step_ids: list[ShortText]
    source_refs: list[ShortText] = Field(min_length=1)


class MeetingMemberFacts(ReportModel):
    member_id: UUID
    name: ShortText | None
    membership_state: Literal['current', 'period_only']
    missing_reasons: list[ShortText] = Field(default_factory=list)
    counts: MeetingCounts
    amounts: MeetingAmounts
    opportunity_ids: list[UUID]
    opportunity_changes: list[MeetingOpportunityChange]
    new_claimed_customer_ids: list[UUID]
    formal_visit_ids: list[UUID]
    new_opportunity_ids: list[UUID]
    lost_opportunity_ids: list[UUID]
    next_step_ids: list[ShortText]
    deduplicated_team_total: Literal[False] = False

    @model_validator(mode='after')
    def honest_member_name(self):
        if self.name is None and not self.missing_reasons:
            raise ValueError('历史成员姓名不可得时须说明，不由前端猜测')
        return self


class MeetingFacts(ReportModel):
    contract_version: Literal['work_report.meeting_facts.v1'] = MEETING_FACTS_CONTRACT
    period: ResolvedPeriod
    data_as_of: AwareDatetime
    quarter: MeetingQuarter
    scope: MeetingResolvedScope
    counts: MeetingCounts
    amounts: MeetingAmounts
    opportunity_changes: list[MeetingOpportunityChange]
    new_claimed_customers: list[MeetingItem]
    formal_visits: list[MeetingItem]
    new_opportunities: list[MeetingItem]
    lost_opportunities: list[MeetingItem]
    next_steps: list[MeetingNextStep]
    members: list[MeetingMemberFacts]
    sources: list[MeetingSource]
    missing_reasons: list[ShortText] = Field(default_factory=list)

    @model_validator(mode='after')
    def member_item_references(self):
        pairs = (('new_claimed_customers', 'new_claimed_customer_ids'), ('formal_visits', 'formal_visit_ids'),
                 ('new_opportunities', 'new_opportunity_ids'), ('lost_opportunities', 'lost_opportunity_ids'),
                 ('next_steps', 'next_step_ids'))
        for collection, member_key in pairs:
            rows = getattr(self, collection)
            ids = {row.id for row in rows}
            if len(ids) != len(rows):
                raise ValueError('冻结明细行ID不能重复')
            for member in self.members:
                refs = getattr(member, member_key)
                if len(refs) != len(set(refs)) or not set(refs) <= ids:
                    raise ValueError('成员明细引用必须指向顶层冻结行ID，不能用业务ID或事件ID替代')
        return self


class MeetingScheme(ReportModel):
    builtin_id: ShortText
    version: Literal[1] = 1
    period_kind: PeriodKind
    name: ShortText
    section_ids: list[ShortText]
    preserves_existing_weekly_generation: Literal[True] = True


class MeetingSaveDraft(ReportModel):
    snapshot_id: UUID
    snapshot_hash: Digest
    title: ShortText
    builtin_scheme_id: ShortText


class MeetingTeamOption(ReportModel):
    team_id: UUID
    name: ShortText
    member_ids: list[UUID]


class MeetingOptions(ReportModel):
    contract_version: Literal['work_report.meeting_facts.v1'] = MEETING_FACTS_CONTRACT
    available_scope_kinds: list[Literal['self', 'managed_team']]
    teams: list[MeetingTeamOption]
    default_schemes: list[MeetingScheme]
    default_period: PeriodKind = 'day'
    can_prepare: bool
    can_save_draft: bool
    can_share: bool


class MeetingSnapshotView(ReportModel):
    id: UUID
    status: Literal['preparing', 'ready', 'failed']
    selection: MeetingSelection
    period: ResolvedPeriod
    scope: MeetingResolvedScope
    created_at: AwareDatetime
    data_as_of: AwareDatetime | None = None
    snapshot_hash: Digest | None = None
    meeting_facts: MeetingFacts | None = None
    error: ReportError | None = None

    @model_validator(mode='after')
    def consistent_envelope(self):
        if (self.selection.scope.kind != self.scope.kind or self.selection.scope.team_id != self.scope.team_id
                or self.selection.period.kind != self.period.kind):
            raise ValueError('会议请求与已解析范围或周期类型不一致')
        if (self.status == 'failed') != (self.error is not None):
            raise ValueError('失败状态必须且只能有错误信息')
        if self.status == 'ready':
            if self.snapshot_hash is None or self.meeting_facts is None or self.data_as_of is None:
                raise ValueError('就绪会议快照须有完整冻结事实和摘要')
            if (self.period != self.meeting_facts.period or self.scope != self.meeting_facts.scope
                    or self.data_as_of != self.meeting_facts.data_as_of):
                raise ValueError('会议事实与快照周期范围和采集时点不一致')
        elif self.meeting_facts is not None:
            raise ValueError('未就绪快照不能暴露部分冻结事实')
        return self
