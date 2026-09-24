from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from sales_backend.api.models import TaskCreate, TaskEventCreate
from sales_backend.main import app
from sales_backend.services.tasks import TaskService


def test_task_routes_are_registered() -> None:
    paths = app.openapi()["paths"]
    assert "get" in paths["/api/v1/tasks"]
    assert "post" in paths["/api/v1/tasks"]
    assert "get" in paths["/api/v1/tasks/{task_id}"]
    assert "post" in paths["/api/v1/tasks/{task_id}/events"]


def test_task_create_contract() -> None:
    body = TaskCreate(
        description="提交客户重启方案与下一步时间表",
        assignee_account_code="XS001",
        due_at=datetime.now(UTC) + timedelta(days=1),
        priority_code="high",
        customer_id=" 2277ce92-87c8-4b04-9751-453f534b0297 ",
    )
    assert body.assignee_account_code == "XS001"
    assert body.priority_code == "high"
    assert body.customer_id == "2277ce92-87c8-4b04-9751-453f534b0297"
    assert (
        TaskCreate(
            description="提交客户重启方案与下一步时间表",
            assignee_account_code="XS001",
            due_at=datetime.now(UTC) + timedelta(days=1),
            customer_id="  ",
        ).customer_id
        is None
    )


def test_completion_requires_explanation() -> None:
    assert TaskEventCreate(event_type="complete", note="交付说明").event_type == "complete"
    with pytest.raises(ValidationError):
        TaskEventCreate(event_type="cancel")


def test_task_create_enforces_assignee_scope() -> None:
    import inspect

    from sales_backend.repositories.task_targets import TaskTargetRepository

    source = inspect.getsource(TaskTargetRepository.resolve)
    assert "self.recipients" in source
    assert "ASSIGNEE_OUT_OF_SCOPE" in source


def test_cross_user_task_notifications_use_scoped_database_function() -> None:
    import inspect

    from sales_backend.repositories.task_mutations import TaskMutationRepository
    source = inspect.getsource(TaskMutationRepository)
    assert "workflow.enqueue_task_notification" in source
    assert "INSERT INTO workflow.notification" not in source


def test_position_and_person_are_exclusive_required_targets():
    common = dict(description="请协助核对客户资料", due_at=datetime.now(UTC) + timedelta(days=1))
    assert TaskCreate(**common, target_position="operations").assignee_account_code is None
    for invalid in [{}, {"target_position": "unknown"}, {"target_position": "self", "assignee_account_code": "XS001"}]:
        with pytest.raises(ValidationError):
            TaskCreate(**common, **invalid)


@pytest.mark.parametrize('accounts', [[], ['XS001', ' xs001 '], ['XS001'] * 101])
def test_task_batch_rejects_empty_duplicate_or_excess_recipients(accounts):
    from sales_backend.contracts.models import TaskBatchCreate
    due = datetime.now(UTC) + timedelta(days=1)
    with pytest.raises(ValidationError):
        TaskBatchCreate(tasks=[dict(description='请协助核对客户资料', assignee_account_code=a, due_at=due) for a in accounts])


def test_task_batch_keeps_one_assignment_and_explicit_recipients():
    from sales_backend.api.advice import SuggestionDecision
    from sales_backend.contracts.models import TaskBatchCreate
    common = dict(description='请协助核对客户资料', due_at=datetime.now(UTC) + timedelta(days=1))
    tasks = [dict(**common, assignee_account_code=a) for a in ['XS001', 'XS002']]
    assert len(TaskBatchCreate(tasks=tasks).tasks) == 2
    for other in [dict(**common, target_position='self'), {**tasks[1], 'description': '另一种任务内容'}]:
        with pytest.raises(ValidationError):
            TaskBatchCreate(tasks=[tasks[0], other])
    assert len(SuggestionDecision(decision='adopted', version_no=1, tasks=tasks).tasks) == 2
    for extra in [dict(task=tasks[0]), dict(decision='no_task')]:
        with pytest.raises(ValidationError):
            SuggestionDecision(**({'decision':'adopted', 'version_no':1, 'tasks':tasks} | extra))
