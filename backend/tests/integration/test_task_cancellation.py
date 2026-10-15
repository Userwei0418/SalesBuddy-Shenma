"""Cancellation visibility and retained history under disposable, non-bypass PostgreSQL RLS."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.domain.tasks import TaskConflict, TaskForbidden, TaskNotFound
from sales_backend.repositories.task_browse import TaskBrowseRepository
from sales_backend.repositories.tasks import TaskRepository
from sales_backend.services.tasks import TaskService
from tests.integration.feishu_fixtures import assert_restricted, fixture_owner, seed_execute
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


async def make_task(connection):
    creator = await actor(connection, "XS001")
    task = await TaskService().create(
        connection, actor=creator, description="隔离取消验收：录入错误但保留处理记录",
        assignee_account_code="XS002", priority_code="normal", due_at=datetime.now(UTC) + timedelta(hours=1),
    )
    return creator, task


async def assert_projections(connection, task_id, expected):
    await assert_restricted(connection)
    detail = await TaskRepository().detail(connection, task_id=task_id)
    legacy = await TaskRepository().list(connection, status=None, customer_id=None, limit=100)
    page = await TaskBrowseRepository().page(connection, tab="all", limit=100)
    assert detail["can_cancel"] is expected
    assert next(t for t in legacy if t["id"] == task_id)["can_cancel"] is expected
    assert next(t for t in page["items"] if t["id"] == task_id)["can_cancel"] is expected
    return detail


async def test_creator_can_cancel_but_ordinary_recipient_cannot(connection):
    _, task = await make_task(connection)
    await assert_projections(connection, task["id"], True)
    recipient = await actor(connection, "XS002")
    await assert_projections(connection, task["id"], False)
    with pytest.raises(TaskForbidden):
        await TaskService().apply_event(connection, actor=recipient, task_id=task["id"],
            event_type="cancel", note="接收人不能取消他人任务", expected_version=task["version_no"])


@pytest.mark.parametrize("code", ["ZJ001", "ZJL001", "OPS001", "ADMIN001"])
async def test_management_authority_can_cancel_within_scope(connection, code):
    _, task = await make_task(connection)
    coordinator = await actor(connection, code)
    await assert_projections(connection, task["id"], True)
    result = await TaskService().apply_event(connection, actor=coordinator, task_id=task["id"],
        event_type="cancel", note="确认录入错误", expected_version=task["version_no"])
    assert result["status"] == "cancelled" and result["can_cancel"] is False


async def test_cancel_flag_rechecks_personal_deny_and_independent_coordination(connection):
    creator, task = await make_task(connection)
    manager = await actor(connection, "ZJL001")
    admin = await actor(connection, "ADMIN001")
    await grant(connection, admin, creator.user_id, [dict(permission="task.cancel", effect="deny")])
    await set_request_context(connection, creator)
    await assert_projections(connection, task["id"], False)
    with pytest.raises(TaskForbidden):
        await TaskService().apply_event(connection, actor=creator, task_id=task["id"],
            event_type="cancel", note="个人禁用应拒绝", expected_version=task["version_no"])
    await grant(connection, admin, manager.user_id, [dict(permission="task.coordinate", effect="deny")])
    await set_request_context(connection, manager)
    detail = await assert_projections(connection, task["id"], False)
    assert detail["action_permissions"]["task.cancel"] is True
    # UPDATE RLS hides the row before the coordination check can return its error.
    with pytest.raises(TaskNotFound):
        await TaskService().apply_event(connection, actor=manager, task_id=task["id"],
            event_type="cancel", note="不能仅凭取消功能越过协调资格", expected_version=task["version_no"])


@pytest.mark.parametrize("task_status,expected", [
    ("pending_confirm", True), ("pending_execution", True), ("in_progress", True), ("deferred", True),
    ("pending_review", False), ("completed", False), ("cancelled", False),
])
async def test_cancel_flag_matches_event_state_guard(connection, task_status, expected):
    creator, task = await make_task(connection)
    # Historical states are synthetic only; all projections and actions use the restricted role.
    await seed_execute(connection, "UPDATE workflow.task SET status=$2 WHERE id=$1::uuid", task["id"], task_status)
    detail = await assert_projections(connection, task["id"], expected)
    call = dict(actor=creator, task_id=task["id"], event_type="cancel", note="更正错误任务",
                expected_version=detail["version_no"])
    if expected:
        result = await TaskService().apply_event(connection, **call)
        assert result["status"] == "cancelled" and result["can_cancel"] is False
    else:
        with pytest.raises(TaskConflict):
            await TaskService().apply_event(connection, **call)


async def test_cancel_http_preserves_history_reason_and_counters_and_rejects_stale_version(connection):
    creator, task = await make_task(connection)
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        for params in ({}, {"tab": "all"}):
            response = await client.get("/api/v1/tasks", params=params)
            assert response.status_code == 200, response.text
            assert next(t for t in response.json()["items"] if t["id"] == task["id"])["can_cancel"] is True
        detail = await client.get(f"/api/v1/tasks/{task['id']}")
        assert detail.status_code == 200 and detail.json()["can_cancel"] is True
        await business_login(client, "XS002")
        accepted = await client.post(f"/api/v1/tasks/{task['id']}/events", json={"event_type": "accept"})
        assert accepted.status_code == 200, accepted.text
        await business_login(client, "XS001")
        stale = await client.post(f"/api/v1/tasks/{task['id']}/events", json={
            "event_type": "cancel", "note": "错误重复任务", "version_no": task["version_no"],
        })
        assert stale.status_code == 409, stale.text
        prior = (await client.get(f"/api/v1/tasks/{task['id']}")).json()
        assert prior["status"] == "pending_execution"
        before = (await client.get("/api/v1/tasks", params={"tab": "all"})).json()["summary"]
        overview_before = (await client.get("/api/v1/tasks/overview")).json()["metrics"]
        key = str(uuid4())
        body = {"event_type": "cancel", "note": "错误重复任务", "version_no": prior["version_no"]}
        cancelled = await client.post(f"/api/v1/tasks/{task['id']}/events", json=body,
                                      headers={"Idempotency-Key": key})
        assert cancelled.status_code == 200, cancelled.text
        result = cancelled.json()
        assert result["status"] == "cancelled" and result["can_cancel"] is False
        assert result["rejection_comment"] == "错误重复任务" and result["completed_at"] is None
        assert result["assignees"] == prior["assignees"]
        assert result["events"][:-1] == prior["events"]
        event = result["events"][-1]
        assert event["event_type"] == "cancel" and event["note"] == "错误重复任务"
        assert event["from_status"] == "pending_execution" and event["to_status"] == "cancelled"
        replay = await client.post(f"/api/v1/tasks/{task['id']}/events", json=body,
                                   headers={"Idempotency-Key": key})
        assert replay.status_code == 200 and replay.json() == result
        after = (await client.get("/api/v1/tasks", params={"tab": "all"})).json()["summary"]
        assert after["total"] == before["total"]
        assert after["pending_count"] == before["pending_count"] - 1
        assert after["completed_count"] == before["completed_count"]
        assert after["rejected_count"] == before["rejected_count"] + 1
        overview_after = (await client.get("/api/v1/tasks/overview")).json()["metrics"]
        assert overview_after["all_pending"] == overview_before["all_pending"] - 1
        assert overview_after["today_completed"] == overview_before["today_completed"]
        for tab in ("pending", "completed"):
            items = (await client.get("/api/v1/tasks", params={"tab": tab})).json()["items"]
            assert task["id"] not in {t["id"] for t in items}
        ended = (await client.get("/api/v1/tasks", params={"tab": "rejected"})).json()["items"]
        assert next(t for t in ended if t["id"] == task["id"])["can_cancel"] is False
    await set_request_context(connection, creator)
    assert await connection.fetchval("SELECT deleted_at IS NULL FROM workflow.task WHERE id=$1::uuid", task["id"])
    await actor(connection, "XS002")
    assert await connection.fetchval(
        "SELECT count(*) FROM workflow.notification WHERE object_id=$1::uuid AND template_code='task_cancelled'",
        task["id"],
    ) == 1


async def test_foreign_tenant_cannot_discover_or_cancel_task(connection):
    _, task = await make_task(connection)
    admin = await actor(connection, "ADMIN001")
    workspace, user = uuid4(), uuid4()
    async with fixture_owner(connection):
        await connection.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'隔离其他公司')",
                                 workspace, str(workspace))
        await connection.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", workspace)
        await connection.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) "
                                 "VALUES($1,$2,$3,'隔离管理员')", user, workspace, str(user))
        await connection.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) "
                                 "VALUES($1,$2,'administrator','workspace')", workspace, user)
    foreign = admin.model_copy(update={"workspace_id": str(workspace), "user_id": str(user)})
    await set_request_context(connection, foreign)
    await assert_restricted(connection)
    assert await TaskRepository().detail(connection, task_id=task["id"]) is None
    assert (await TaskBrowseRepository().page(connection, tab="all"))["items"] == []
    assert await TaskRepository().list(connection, status=None, customer_id=None, limit=100) == []
    with pytest.raises(TaskNotFound):
        await TaskService().apply_event(connection, actor=foreign, task_id=task["id"], event_type="cancel",
            note="跨公司拒绝", expected_version=task["version_no"])
