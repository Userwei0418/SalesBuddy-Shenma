"""Human transfers preserve named evidence without changing recipients or workflow."""

from datetime import UTC, datetime, timedelta

import pytest

from sales_backend.db import set_request_context
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.domain.tasks import TaskConflict
from sales_backend.repositories.tasks import TaskRepository
from sales_backend.services.tasks import TaskService
from tests.integration.feishu_fixtures import assert_restricted, fixture_owner, seed_execute
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def make_task(connection, *, creator_code="XS001", owner_code="XS002"):
    creator = await actor(connection, creator_code)
    task = await TaskService().create(
        connection, actor=creator, description="隔离转交验收：保留人工指定负责人和操作证据",
        assignee_account_code=owner_code, priority_code="normal",
        due_at=datetime.now(UTC) + timedelta(hours=1),
    )
    return creator, task


async def transfer(connection, coordinator, task, account, *, version=None):
    await set_request_context(connection, coordinator)
    await assert_restricted(connection)
    return await TaskService().apply_event(
        connection, actor=coordinator, task_id=task["id"], event_type="reassign",
        note="人工确认转交给新负责人", expected_version=task["version_no"] if version is None else version,
        assignee_account_code=account,
    )


async def transfer_notifications(connection, task_id):
    # Inspect only this synthetic task in the runner-created disposable database;
    # all business operations above execute as the restricted runtime role.
    async with fixture_owner(connection):
        rows = await connection.fetch(
            "SELECT recipient_user_ref_id::text AS recipient,title,body,payload "
            "FROM workflow.notification WHERE object_id=$1::uuid AND template_code='task_reassigned' "
            "ORDER BY recipient_user_ref_id", task_id,
        )
    await assert_restricted(connection)
    return [dict(row) for row in rows]


async def test_transfer_event_audit_and_notifications_keep_named_decision_evidence(connection):
    _, task = await make_task(connection)
    previous = next(person for person in task["assignees"] if person["responsibility"] == "owner")
    coordinator = await actor(connection, "ZJL001")
    actor_name = await connection.fetchval("SELECT display_name FROM platform.user_ref WHERE id=$1::uuid",
                                           coordinator.user_id)
    result = await transfer(connection, coordinator, task, "ZJ001")
    owner, = [person for person in result["assignees"] if person["responsibility"] == "owner"]
    event = result["events"][-1]
    assert result["status"] == "pending_confirm" and result["version_no"] == task["version_no"] + 1
    assert result["creator_user_ref_id"] == task["creator_user_ref_id"]
    assert result["events"][:-1] == task["events"]
    assert event["event_type"] == "reassign" and event["note"] == "人工确认转交给新负责人"
    evidence = event["payload"]
    assert evidence["actor_name"] == actor_name
    assert evidence["previous_owners"] == [{
        "user_id": previous["user_id"], "role": previous["role"],
        "team_id": previous["team_id"], "name": previous["name"],
    }]
    assert evidence["new_owner"]["user_id"] == owner["user_id"]
    assert evidence["new_owner"]["display_name"] == owner["name"]
    assert evidence["new_owner"]["role_code"] == owner["role"]
    assert evidence["new_owner"]["team_id"] == owner["team_id"]
    notifications = await transfer_notifications(connection, task["id"])
    assert notifications
    for notification in notifications:
        assert notification["title"] == f"{actor_name}将任务转交给{owner['name']}"
        assert notification["body"] == task["title"]
        assert notification["payload"] == {
            "task_id": task["id"], "status": "pending_confirm", "reason": event["note"],
            "actor_user_ref_id": coordinator.user_id, "event_version": result["version_no"], **evidence,
        }
    await actor(connection, "ADMIN001")
    audit = await connection.fetchrow(
        "SELECT actor_user_ref_id::text,after_snapshot FROM ops.audit_log "
        "WHERE object_type='task_event' AND object_id=$1::uuid "
        "AND action_code='workflow.task_event.insert'", event["id"],
    )
    assert audit and audit["actor_user_ref_id"] == coordinator.user_id
    assert audit["after_snapshot"]["payload"] == evidence
    # A subsequent account rename must not rewrite who made this historical decision.
    await seed_execute(connection, "UPDATE platform.user_ref SET display_name='隔离验收后改名' "
                       "WHERE id=ANY($1::uuid[])", [coordinator.user_id, previous["user_id"], owner["user_id"]])
    reread = await TaskRepository().detail(connection, task_id=task["id"])
    assert reread["events"][-1]["payload"] == evidence
    assert await transfer_notifications(connection, task["id"]) == notifications


@pytest.mark.parametrize("creator_code,owner_code,coordinator_code,new_code", [
    ("XS001", "XS002", "ZJL001", "ZJ001"),
    ("XS001", "XS001", "XS001", "XS002"),
    ("XS001", "ZJL001", "ZJL001", "XS002"),
])
async def test_transfer_notifies_unique_old_new_creator_and_excludes_operator(
    connection, creator_code, owner_code, coordinator_code, new_code,
):
    creator, task = await make_task(connection, creator_code=creator_code, owner_code=owner_code)
    old_ids = {person["user_id"] for person in task["assignees"] if person["responsibility"] == "owner"}
    coordinator = await actor(connection, coordinator_code)
    result = await transfer(connection, coordinator, task, new_code)
    new_ids = {person["user_id"] for person in result["assignees"] if person["responsibility"] == "owner"}
    expected = (old_ids | new_ids | {creator.user_id}) - {coordinator.user_id}
    notifications = await transfer_notifications(connection, task["id"])
    assert len(notifications) == len(expected)
    assert {item["recipient"] for item in notifications} == expected


async def test_stale_transfer_does_not_reassign_or_duplicate_evidence_and_notifications(connection):
    _, task = await make_task(connection)
    coordinator = await actor(connection, "ZJL001")
    result = await transfer(connection, coordinator, task, "ZJ001")
    notifications = await transfer_notifications(connection, task["id"])
    with pytest.raises(VersionConflict):
        await transfer(connection, coordinator, task, "XS002")
    reread = await TaskRepository().detail(connection, task_id=task["id"])
    assert reread == result
    assert await transfer_notifications(connection, task["id"]) == notifications


async def test_same_owner_transfer_is_rejected_without_changing_task_or_sending_notices(connection):
    creator, task = await make_task(connection)
    with pytest.raises(TaskConflict, match="当前负责人就是此人"):
        await transfer(connection, creator, task, "XS002")
    assert await TaskRepository().detail(connection, task_id=task["id"]) == task
    assert await transfer_notifications(connection, task["id"]) == []
