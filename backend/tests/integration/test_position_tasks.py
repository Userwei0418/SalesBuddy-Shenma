from datetime import UTC, datetime, timedelta

import pytest

from sales_backend.domain.tasks import TaskConflict, TaskForbidden
from sales_backend.repositories.assistant import AssistantRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.repositories.task_targets import TaskTargetRepository
from sales_backend.repositories.tasks import TaskRepository
from sales_backend.services.tasks import TaskService
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


async def two_operations(connection):
    admin = await actor(connection, "ADMIN001")
    await OperationsAccountRepository().create(
        connection,
        admin,
        {"account_code": "OPS002", "display_name": "第二位运营", "roles": ["operations"], "team_id": admin.team_ids[0]},
    )


async def create(connection, position="operations"):
    sales = await actor(connection, "XS001")
    return await TaskService().create(
        connection,
        actor=sales,
        description="请协助核对客户认领材料",
        due_at=datetime.now(UTC) + timedelta(days=1),
        priority_code="high",
        target_position=position,
    )


async def test_one_position_member_claims_others_observe_and_only_owner_completes(connection):
    await two_operations(connection)
    task = await create(connection)
    assert len(task["candidates"]) == 2 and task["assignees"] == [] and task["target_position"] == "operations"
    service = TaskService()
    first = await actor(connection, "OPS001")
    detail = await TaskRepository().detail(connection, task_id=task["id"])
    assert detail["requires_action"] is True
    home = await AssistantRepository().home(connection, first)
    assert any(t["source_id"] == task["id"] for t in home["task_items"])
    accepted = await service.apply_event(connection, actor=first, task_id=task["id"], event_type="accept", note=None)
    assert accepted["status"] == "pending_execution" and accepted["owner_name"] == "OPS001"
    other = await actor(connection, "OPS002")
    detail = await TaskRepository().detail(connection, task_id=task["id"])
    assert detail["owner_name"] == "OPS001" and not detail["requires_action"]
    with pytest.raises(TaskConflict):
        await service.apply_event(connection, actor=other, task_id=task["id"], event_type="accept", note=None)
    with pytest.raises(TaskForbidden):
        await service.complete(connection, actor=other, task_id=task["id"], note="越权完成")
    first = await actor(connection, "OPS001")
    completed = await service.complete(connection, actor=first, task_id=task["id"], note="材料已核对")
    assert completed["status"] == "pending_review"
    with pytest.raises(TaskConflict):
        await service.complete(connection, actor=first, task_id=task["id"], note="再次完成")
    creator = await actor(connection, "XS001")
    approved = await service.apply_event(connection, actor=creator, task_id=task["id"],
        event_type="approve_completion", note=None, expected_version=completed["version_no"])
    assert approved["status"] == "completed"
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM workflow.task_candidate WHERE task_id=$1::uuid AND decision='claimed'", task["id"]
        )
        == 1
    )
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM workflow.task_event WHERE task_id=$1::uuid AND event_type='approve_completion'", task["id"]
        )
        == 1
    )
    other = await actor(connection, "OPS002")
    notices = await connection.fetch(
        "SELECT template_code FROM workflow.notification WHERE object_id=$1::uuid", task["id"]
    )
    assert {n["template_code"] for n in notices} == {"task_assigned", "task_claimed", "task_completed"}


async def test_position_reject_does_not_cancel_other_candidates_and_all_reject_cancels(connection):
    await two_operations(connection)
    task = await create(connection)
    service = TaskService()
    first = await actor(connection, "OPS001")
    with pytest.raises(TaskConflict, match="REJECTION_COMMENT_REQUIRED"):
        await service.apply_event(connection, actor=first, task_id=task["id"], event_type="reject", note="")
    result = await service.apply_event(
        connection, actor=first, task_id=task["id"], event_type="reject", note="本周出差"
    )
    assert result["status"] == "pending_confirm" and not result["requires_action"]
    other = await actor(connection, "OPS002")
    result = await service.apply_event(
        connection, actor=other, task_id=task["id"], event_type="reject", note="需要资料补齐"
    )
    assert result["status"] == "cancelled" and result["assignees"] == []
    await actor(connection, "XS001")
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM workflow.notification WHERE object_id=$1::uuid "
            "AND template_code='task_candidate_declined'",
            task["id"],
        )
        == 2
    )


async def test_position_endpoint_real_targets_idempotent_creation_and_role_revocation(connection):
    await two_operations(connection)
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        positions = await client.get("/api/v1/directory/task-positions")
        assert positions.status_code == 200
        assert next(p for p in positions.json()["items"] if p["code"] == "operations")["candidate_count"] == 2
        body = {
            "description": "请协助核对正式客户资料",
            "target_position": "operations",
            "due_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        }
        from uuid import uuid4

        headers = {"Idempotency-Key": str(uuid4())}
        first = await client.post("/api/v1/tasks", json=body, headers=headers)
        assert first.status_code == 201, first.text
        again = await client.post("/api/v1/tasks", json=body, headers=headers)
        assert again.json()["id"] == first.json()["id"]
        task_id = first.json()["id"]
        await sign_in(client)
        inbox = await client.get("/api/v1/tasks", params={"inbox": True, "page_size": 1})
        assert inbox.status_code == 200 and inbox.json()["items"][0]["id"] == task_id
        accepted = await client.post(f"/api/v1/tasks/{task_id}/events", json={"event_type": "accept"})
        assert accepted.status_code == 200, accepted.text
    task = await create(connection)
    person = await actor(connection, "OPS001")
    await actor(connection, "ADMIN001")
    await connection.execute(
        "UPDATE platform.role_binding SET valid_to=clock_timestamp() WHERE user_ref_id=$1::uuid "
        "AND role_code='operations'",
        person.user_id,
    )
    from sales_backend.db import set_request_context

    await set_request_context(connection, person)
    assert await TaskTargetRepository().current_candidate(connection, person, task["id"]) is None
    with pytest.raises(TaskForbidden):
        await TaskService().apply_event(connection, actor=person, task_id=task["id"], event_type="accept", note=None)
