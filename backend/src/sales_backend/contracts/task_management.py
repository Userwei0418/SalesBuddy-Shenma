"""Read-only company task management filters; dates are Beijing calendar dates."""

from datetime import date, datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from sales_backend.contracts.types import UUIDString

TaskStatus = Literal['pending_confirm', 'pending_execution', 'in_progress', 'pending_review', 'completed', 'deferred', 'cancelled']


def management_filters(
    scope: Literal['all', 'mine'] = 'all',
    status: TaskStatus | None = None,
    overdue: bool = False,
    member_id: UUIDString | None = None,
    team_id: UUIDString | None = None,
    unassigned: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    batch_id: UUIDString | None = None,
    undated: Literal['include', 'exclude', 'only'] = 'include',
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, '开始日期不能晚于结束日期')
    if date_to and date_to == date.max:
        raise HTTPException(422, '结束日期超出支持范围')
    if member_id and unassigned:
        raise HTTPException(422, '负责人和未领取筛选不能同时指定')
    return dict(scope=scope, status=status, overdue=overdue, member_id=member_id, team_id=team_id, unassigned=unassigned,
                date_from=date_from, date_to=date_to, batch_id=batch_id, undated=undated)


class TaskManagementOwner(BaseModel):
    id: UUIDString
    name: str
    account_code: str | None
    active: bool = Field(description='当前账号是否有效；历史停用负责人仍保留')
    account_status: str


class TaskManagementOwnerCount(TaskManagementOwner):
    task_count: int = Field(ge=0, description='全部筛选结果中此人为当前负责人的任务数')


class TaskManagementItem(BaseModel):
    # Existing detail/action fields are intentionally retained for shared dialogs.
    model_config = ConfigDict(extra='allow')
    id: UUIDString
    alignment: dict | None = Field(default=None, description='指标/KR关联；旧任务未设置为null，任务计数按task_id去重')
    title: str
    description: str
    status: TaskStatus
    created_at: datetime
    due_at: datetime | None
    accepted_at: datetime | None
    owner_id: UUIDString | None = Field(description='当前负责人稳定排序首项；完整责任关系以 owners 为准')
    owner_name: str | None
    owner_active: bool | None
    owner_account_status: str | None
    owners: list[TaskManagementOwner]
    owner_count: int = Field(ge=0)
    source_code: str | None
    batch_id: UUIDString | None
    batch_name: str | None
    meeting_date: date | None
    dispatched_at: datetime | None
    schedule_start_at: datetime = Field(description='正式批次确认派发时间，否则为任务创建时间；不表示实际开工')
    start_kind: Literal['created', 'dispatched']
    schedule_invalid: bool = Field(description='截止早于安排时间；展示异常，不伪造进度')
    action_permissions: dict[str, bool]
    can_cancel: bool
    can_coordinate: bool
    requires_action: bool


class TaskManagementSummary(BaseModel):
    total: int = Field(ge=0, description='完整筛选范围的任务数，历史多人任务仅计一次')
    pending_count: int = Field(ge=0, description='未完成，包含待验收；排除完成及取消')
    pending_review_count: int = Field(ge=0)
    overdue_count: int = Field(ge=0, description='截至 as_of 已过期且未完成/取消')
    completed_count: int = Field(ge=0)
    cancelled_count: int = Field(ge=0)
    undated_count: int = Field(ge=0, description='无截止日期任务；当前正式任务约束要求截止日期，正常为零')


class TaskManagementPage(BaseModel):
    items: list[TaskManagementItem]
    summary: TaskManagementSummary
    has_more: bool
    next_offset: int | None
    as_of: datetime = Field(description='本次过期判断时间；列表和统计共享只读一致性快照')


class TaskManagementOwnerPage(BaseModel):
    items: list[TaskManagementOwnerCount]
    unassigned_count: int = Field(ge=0, description='完整筛选范围中当前没有负责人任务数；指定人待接受不计入')
    has_more: bool
    next_offset: int | None
    as_of: datetime
