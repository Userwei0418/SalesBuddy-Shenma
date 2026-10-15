"""Read-only task activity alongside overview; never part of frozen generation facts."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from sales_backend.contracts.task_management import TaskStatus
from sales_backend.contracts.work_report_base import ReportModel, ResolvedPeriod, ShortText
from sales_backend.contracts.work_report_meeting import MeetingCount, MeetingSource
from sales_backend.contracts.work_report_overview import OverviewResolvedScope


class TaskActivityCounts(ReportModel):
    new_tasks: MeetingCount
    completed_tasks: MeetingCount


class TaskActivityOwner(ReportModel):
    member_id: UUID
    name: ShortText | None


class TaskActivityItem(ReportModel):
    id: UUID
    task_id: UUID
    name: ShortText
    owners: list[TaskActivityOwner] = Field(min_length=1)
    member_ids: list[UUID] = Field(min_length=1)
    customer_id: UUID | None = None
    customer_name: ShortText | None = None
    opportunity_id: UUID | None = None
    opportunity_name: ShortText | None = None
    created_at: AwareDatetime
    completed_at: AwareDatetime | None
    status: TaskStatus
    status_label: ShortText
    source_refs: list[ShortText] = Field(min_length=1)

    @model_validator(mode="after")
    def exact_owner_identity(self):
        if self.id != self.task_id or len(self.member_ids) != len(set(self.member_ids)):
            raise ValueError("任务活动使用唯一任务ID和去重负责人")
        if len(self.owners) != len(self.member_ids) or {o.member_id for o in self.owners} != set(self.member_ids):
            raise ValueError("负责人姓名与成员引用须一致")
        return self


class TaskActivityMember(ReportModel):
    member_id: UUID
    counts: TaskActivityCounts
    new_task_ids: list[UUID]
    completed_task_ids: list[UUID]


class TaskActivity(ReportModel):
    contract_version: Literal["work_report.task_activity.v1"] = "work_report.task_activity.v1"
    attribution_basis: Literal["current_task_owner"] = "current_task_owner"
    period: ResolvedPeriod
    scope: OverviewResolvedScope
    data_as_of: AwareDatetime
    counts: TaskActivityCounts
    new_tasks: list[TaskActivityItem]
    completed_tasks: list[TaskActivityItem]
    members: list[TaskActivityMember]
    sources: list[MeetingSource]
    missing_reasons: list[ShortText] = Field(default_factory=list)

    @model_validator(mode="after")
    def exact_detail_references(self):
        source_ids = {s.source_id for s in self.sources}
        if len(source_ids) != len(self.sources) or len({m.member_id for m in self.members}) != len(self.members):
            raise ValueError("来源和成员必须唯一")
        for key, member_key in [("new_tasks", "new_task_ids"), ("completed_tasks", "completed_task_ids")]:
            items = getattr(self, key)
            ids = {item.task_id for item in items}
            if len(ids) != len(items) or getattr(self.counts, key).known_count != len(items):
                raise ValueError("任务计数须与去重明细一致")
            for item in items:
                if not set(item.source_refs) <= source_ids:
                    raise ValueError("任务活动含未知来源")
            for member in self.members:
                expected = {item.task_id for item in items if member.member_id in item.member_ids}
                actual = getattr(member, member_key)
                if len(actual) != len(set(actual)) or set(actual) != expected:
                    raise ValueError("成员引用须精确对应其负责的任务")
                if getattr(member.counts, key).known_count != len(actual):
                    raise ValueError("成员计数与明细不一致")
        return self
