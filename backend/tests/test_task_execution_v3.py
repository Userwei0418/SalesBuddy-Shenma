"""Compact HTTP, compatibility and report contract checks for the lightweight mode."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sales_backend.contracts.models import TaskCreate
from sales_backend.contracts.work_report_base import ResolvedPeriod
from sales_backend.contracts.work_report_overview import OverviewResolvedScope
from sales_backend.domain.tasks import TaskConflict
from sales_backend.domain.work_report_task_activity import assemble_task_activity, assemble_task_activity_v2
from sales_backend.services.task_compatibility import require_execution_creation_enabled, require_legacy_mutation
from tests.test_task_execution_api import BASE, client  # noqa: F401


@pytest.mark.parametrize("action,body", [
    ("review_completion", {"decision": "approve", "note": "好"}),
    ("review_completion", {"decision": "reject", "submission_id": str(uuid4())}),
    ("pause", {"reason": " "}),
    ("defer", {"reason": "等反馈", "due_at": "2027-01-01T12:00:00"}),
    ("append_feedback", {"text": "处理了", "percent": 100}),
    ("revise", {"reason": "仅备注不修改要求"}),
])
def test_invalid_v3_write_cannot_reach_database(client, action, body):  # noqa: F811
    response = client.post(f"/api/v1/tasks/{uuid4()}/execution-v3/{action}",
        json=BASE | body, headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "TASK_EXECUTION_INVALID_COMMAND"


def test_lightweight_creation_is_single_person_and_has_no_checklist_requirements():
    body = dict(description="确认本次客户回访的下一步计划", target_position="self",
        due_at=datetime.now(UTC) + timedelta(days=1), execution_protocol_version=3)
    assert TaskCreate(**body).execution_protocol_version == 3
    for changed in ({"target_position": "manager"}, {"deliverable": "额外协议"}):
        with pytest.raises(ValidationError):
            TaskCreate(**(body | changed))


@pytest.mark.asyncio
async def test_v3_blocks_old_creation_and_mutation_without_removing_legacy_tasks():
    conn = SimpleNamespace(fetchval=AsyncMock(return_value=True))
    for protocol in (None, 1, 2):
        with pytest.raises(TaskConflict, match="TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED"):
            await require_execution_creation_enabled(conn, protocol_version=protocol)
    await require_execution_creation_enabled(conn, protocol_version=3)
    conn.fetchval.return_value = "execution_v3"
    for action in ("accept", "complete", "cancel"):
        with pytest.raises(TaskConflict, match="TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED"):
            await require_legacy_mutation(conn, str(uuid4()), action)
    conn.fetchval.return_value = "legacy"
    await require_legacy_mutation(conn, str(uuid4()), "complete")


def test_report_v2_counts_open_inventory_and_preserves_close_result_without_fake_progress():
    now = datetime(2026, 10, 23, 8, tzinfo=UTC)
    owner, tid = uuid4(), uuid4()
    period = ResolvedPeriod(kind="day", start=now - timedelta(hours=8),
        end_exclusive=now + timedelta(hours=16), cutoff=now, label="今日")
    scope = OverviewResolvedScope(kind="self", label="本人", member_ids=[owner])
    base = dict(id=str(tid), version_no=4, title="历史任务", status="needs_adjustment",
        created_at=now - timedelta(days=5), updated_at=now, completed_at=None,
        owners=[dict(member_id=str(owner), name="合成执行人")], completion_events=[],
        execution_mode="execution_v3", execution=dict(processing_reason="awaiting_review",
            processing_detail={"reason": "提交完成待确认"}, latest_feedback={"text": "已发送方案"}, next_step="等待客户确认"))
    args = dict(period=period, scope=scope, data_as_of=now)
    assert assemble_task_activity([base], **args).counts.new_tasks.value == 0
    overview = assemble_task_activity_v2([base], **args)
    assert overview.counts.progressing_tasks.value == 1
    assert overview.counts.completed_tasks.value == 0
    assert overview.progressing_tasks[0].processing_detail == "提交完成待确认"
    assert overview.progressing_tasks[0].latest_feedback == "已发送方案"
    closed = base | dict(status="completed", completed_at=now - timedelta(minutes=1),
        execution={"completion_result": "客户暂缓，本次不再继续", "completion_action": "close"})
    overview = assemble_task_activity_v2([closed], **args)
    assert overview.counts.completed_tasks.value == 0 and overview.counts.progressing_tasks.value == 0
    assert overview.counts.completed_tasks.coverage == "complete"
    assert not overview.completed_tasks
    assert closed["execution"]["completion_result"] == "客户暂缓，本次不再继续"


def test_management_pending_group_uses_current_inventory_and_keeps_status_filter():
    from sales_backend.contracts.task_management import management_filters
    from sales_backend.repositories.task_management import management_selection
    filters = management_filters(tab="pending", status="needs_adjustment")
    sql, args = management_selection(as_of=datetime.now(UTC), **filters)
    assert "t.status NOT IN ('completed','cancelled')" in sql
    assert "needs_adjustment" in args
