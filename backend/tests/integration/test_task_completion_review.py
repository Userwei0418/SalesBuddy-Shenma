from datetime import UTC, datetime, timedelta

import pytest

from sales_backend.domain.concurrency import VersionConflict
from sales_backend.domain.tasks import TaskConflict, TaskForbidden
from sales_backend.repositories.tasks import TaskRepository
from sales_backend.services.tasks import TaskService
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def start(connection, self_task=False):
    creator = await actor(connection, "XS001" if self_task else "ZJ001")
    service = TaskService()
    task = await service.create(connection, actor=creator, description="验收任务交付",
        assignee_account_code="XS001", priority_code="normal", due_at=datetime.now(UTC)+timedelta(days=1))
    owner = await actor(connection, "XS001")
    accepted = await service.apply_event(connection, actor=owner, task_id=task["id"], event_type="accept", note=None)
    return service, creator, owner, accepted


async def test_review_reject_resubmit_approve_retains_history_and_receipts(connection):
    service, creator, owner, task = await start(connection)
    with pytest.raises(TaskConflict, match="完成说明"):
        await service.complete(connection, actor=owner, task_id=task["id"], note=" ")
    submitted = await service.complete(connection, actor=owner, task_id=task["id"], note="第一轮交付")
    assert submitted["status"] == "pending_review" and submitted["completed_at"] is None
    assert submitted["completion_submitted_at"]
    assert not submitted["requires_action"]
    with pytest.raises(TaskForbidden):
        await service.apply_event(connection, actor=owner, task_id=task["id"], event_type="approve_completion",
            note=None, expected_version=submitted["version_no"])
    creator = await actor(connection, "ZJ001")
    detail = await TaskRepository().detail(connection, task_id=task["id"])
    assert detail["requires_action"]
    inbox = await TaskRepository().list(connection, status=None, customer_id=None, limit=50, inbox=True)
    assert task["id"] in {t["id"] for t in inbox}
    with pytest.raises(TaskConflict, match="驳回原因"):
        await service.apply_event(connection, actor=creator, task_id=task["id"], event_type="reject_completion",
            note=" ", expected_version=submitted["version_no"])
    rejected = await service.apply_event(connection, actor=creator, task_id=task["id"], event_type="reject_completion",
        note="请补充验证结果", expected_version=submitted["version_no"])
    assert rejected["status"] == "in_progress" and rejected["completed_at"] is None
    assert rejected["completion_note"] == "第一轮交付"
    owner = await actor(connection, "XS001")
    second = await service.complete(connection, actor=owner, task_id=task["id"], note="第二轮已补齐",
        expected_version=rejected["version_no"])
    creator = await actor(connection, "ZJ001")
    with pytest.raises(VersionConflict):
        await service.apply_event(connection, actor=creator, task_id=task["id"], event_type="approve_completion",
            note=None, expected_version=submitted["version_no"])
    approved = await service.apply_event(connection, actor=creator, task_id=task["id"], event_type="approve_completion",
        note=None, expected_version=second["version_no"])
    assert approved["status"] == "completed" and approved["completed_at"]
    assert [e['event_type'] for e in approved['events']][-4:] == [
        'submit_completion','reject_completion','submit_completion','approve_completion']
    notices = await connection.fetch(
        "SELECT template_code FROM workflow.notification WHERE object_id=$1::uuid", task["id"])
    assert sum(n['template_code']=='task_completion_submitted' for n in notices)==2
    assert sum(n['template_code']=='task_completed' for n in notices)==1
    with pytest.raises((VersionConflict, TaskConflict)):
        await service.apply_event(connection, actor=creator, task_id=task["id"], event_type="reject_completion",
            note="过期驳回", expected_version=second["version_no"])


async def test_self_assigned_completes_directly_but_note_required(connection):
    service, creator, owner, task = await start(connection, True)
    done = await service.complete(connection, actor=owner, task_id=task['id'], note='自建任务已交付')
    assert done['status']=='completed' and done['completed_at']
    assert done['events'][-1]['event_type']=='complete'


@pytest.mark.parametrize("creator_code", ["OPS001", "ADMIN001"])
async def test_operations_creator_can_review_without_owning_task(connection, creator_code):
    creator = await actor(connection, creator_code)
    service = TaskService()
    task = await service.create(connection, actor=creator, description="运营发起验收任务",
        assignee_account_code="XS001", priority_code="normal", due_at=datetime.now(UTC)+timedelta(days=1))
    owner = await actor(connection, "XS001")
    await service.apply_event(connection, actor=owner, task_id=task['id'], event_type='accept', note=None)
    first = await service.complete(connection, actor=owner, task_id=task['id'], note='第一版')
    creator = await actor(connection, creator_code)
    rejected = await service.apply_event(connection, actor=creator, task_id=task['id'],
        event_type='reject_completion', note='请补充', expected_version=first['version_no'])
    assert rejected['status']=='in_progress'
    owner = await actor(connection, "XS001")
    second = await service.complete(connection, actor=owner, task_id=task['id'], note='第二版')
    creator = await actor(connection, creator_code)
    approved = await service.apply_event(connection, actor=creator, task_id=task['id'],
        event_type='approve_completion', note=None, expected_version=second['version_no'])
    assert approved['status']=='completed'
