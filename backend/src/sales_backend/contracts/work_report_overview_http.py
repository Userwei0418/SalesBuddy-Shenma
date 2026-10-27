"""Read-only overview wire contract, separate from the immutable model input."""
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, model_validator

from sales_backend.contracts.work_report import SnapshotSelection
from sales_backend.contracts.work_report_base import Digest, PeriodKind, ReportModel, ResolvedPeriod, ShortText
from sales_backend.contracts.work_report_overview import (
    GenerationProfile, OverviewFacts, OverviewResolvedScope, OverviewSelection,
)
from sales_backend.contracts.work_report_task_activity import TaskActivity, TaskActivityV2


class OverviewOption(ReportModel):
    id: UUID
    name: ShortText


class OverviewGenerationOptions(ReportModel):
    available_scope_kinds: list[Literal['self', 'team']]
    managed_teams: list[OverviewOption]
    profiles: list[GenerationProfile]


class OverviewOptions(ReportModel):
    contract_version: Literal['work_report.overview_options.v1'] = 'work_report.overview_options.v1'
    period_kinds: list[PeriodKind]
    available_scope_kinds: list[Literal['self', 'member', 'team']]
    members: list[OverviewOption]
    teams: list[OverviewOption]
    generation: OverviewGenerationOptions


class OverviewCapabilities(ReportModel):
    can_view: Literal[True] = True
    can_generate: bool
    generation_unavailable_reason: Literal['READ_ONLY', 'SCOPE_NOT_GENERATABLE', 'PROFILE_UNAVAILABLE',
                                            'SOURCE_NOT_GENERATABLE'] | None = None


class OverviewGenerationBinding(ReportModel):
    token: str
    expires_at: AwareDatetime
    snapshot_selection: SnapshotSelection


class OverviewResponse(ReportModel):
    contract_version: Literal['work_report.overview.v1'] = 'work_report.overview.v1'
    request_id: UUID
    selection: OverviewSelection
    selection_hash: Digest
    data_as_of: AwareDatetime
    period: ResolvedPeriod
    scope: OverviewResolvedScope
    facts: OverviewFacts
    overview_hash: Digest
    capabilities: OverviewCapabilities
    generation_binding: OverviewGenerationBinding | None = None
    task_activity: TaskActivity | None = None
    task_activity_v2: TaskActivityV2 | None = None

    @model_validator(mode='after')
    def binding_consistency(self):
        if self.task_activity_v2 is not None and (self.task_activity_v2.period != self.period
                or self.task_activity_v2.scope != self.scope or self.task_activity_v2.data_as_of != self.data_as_of):
            raise ValueError('任务活动与当前概览的周期、范围和取数时间须一致')
        if self.task_activity is not None and (self.task_activity.period != self.period
                or self.task_activity.scope != self.scope or self.task_activity.data_as_of != self.data_as_of):
            raise ValueError('任务活动与当前概览的周期、范围和取数时间须一致')
        if (self.capabilities.can_generate != (self.generation_binding is not None)
                or self.capabilities.can_generate != (self.capabilities.generation_unavailable_reason is None)):
            raise ValueError('概览生成能力与绑定不一致')
        return self
