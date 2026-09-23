"""RLS 可见范围与任务状态机的真实数据库行为。

写路径全部跑在会回滚的事务里，真实库不留痕迹。
这些是纯函数测试完全覆盖不到的部分：RLS 策略、状态机的库内约束、以及
先前只能靠手工线上探测确认的拒绝路径。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sales_backend.domain.agent import RoleCode
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.domain.tasks import (
    TaskConflict,
    TaskForbidden,
    TaskNotFound,
)
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.repositories.tasks import TaskRepository
from sales_backend.services.tasks import TaskService

pytestmark = pytest.mark.asyncio


async def _visible_customer_ids(connection, actor_factory, role: RoleCode) -> set[str]:
    await actor_factory(role)
    rows = await CustomerRepository().list(
        connection, query=None, level=None, unassigned=None, limit=500
    )
    return {row["id"] for row in rows}


async def test_customer_visibility_is_monotonic_across_roles(connection, actor_factory) -> None:
    """一线可见 ⊆ 总监可见 ⊆ 总经理可见。RLS 策略回归的第一道防线。"""
    sales = await _visible_customer_ids(connection, actor_factory, RoleCode.SALES)
    supervisor = await _visible_customer_ids(connection, actor_factory, RoleCode.SUPERVISOR)
    manager = await _visible_customer_ids(connection, actor_factory, RoleCode.MANAGER)

    assert sales <= supervisor, f"一线能看到总监看不到的客户: {sorted(sales - supervisor)}"
    assert supervisor <= manager, f"总监能看到总经理看不到的客户: {sorted(supervisor - manager)}"


async def test_customer_linked_task_visibility_is_monotonic(connection, actor_factory) -> None:
    """挂了客户的任务随客户可见范围收敛；无客户的个人任务只对创建人/受托人可见，
    不要求一线 ⊆ 总经理（workflow.task 的 RLS 就是这样写的）。"""
    scopes: dict[RoleCode, set[str]] = {}
    for role in (RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER):
        await actor_factory(role)
        rows = await TaskRepository().list(connection, status=None, customer_id=None, limit=500)
        scopes[role] = {row["id"] for row in rows if row.get("customer_id")}

    assert scopes[RoleCode.SALES] <= scopes[RoleCode.MANAGER]
    assert scopes[RoleCode.SUPERVISOR] <= scopes[RoleCode.MANAGER]


async def test_task_rejects_past_due_date(connection, supervisor_actor) -> None:
    with pytest.raises(TaskConflict, match="TASK_DUE_AT_MUST_BE_FUTURE"):
        await TaskService().create(
            connection,
            actor=supervisor_actor,
            description="集成测试：过期到期时间",
            assignee_account_code="XS001",
            due_at=datetime.now(UTC) - timedelta(days=1),
            priority_code="medium",
        )


async def test_task_lifecycle_accept_then_complete(connection, supervisor_actor, actor_factory) -> None:
    """派单 → 接受 → 完成，全程在回滚事务内验证状态流转。"""
    created = await TaskService().create(
        connection,
        actor=supervisor_actor,
        description="集成测试：正常生命周期",
        assignee_account_code="XS001",
        due_at=datetime.now(UTC) + timedelta(days=3),
        priority_code="medium",
    )
    task_id = created["id"]
    assert created["status"] == "pending_confirm"

    sales = await actor_factory(RoleCode.SALES)
    accepted = await TaskService().apply_event(
        connection, actor=sales, task_id=task_id, event_type="accept", note=None
    )
    assert accepted["status"] == "pending_execution"

    completed = await TaskService().complete(
        connection, actor=sales, task_id=task_id, note="集成测试完成说明"
    )
    assert completed["status"] == "pending_review"
    supervisor = await actor_factory(RoleCode.SUPERVISOR)
    approved = await TaskService().apply_event(connection, actor=supervisor, task_id=task_id,
        event_type="approve_completion", note=None, expected_version=completed["version_no"])
    assert approved["status"] == "completed"


async def test_task_reject_requires_comment(connection, supervisor_actor, actor_factory) -> None:
    created = await TaskService().create(
        connection,
        actor=supervisor_actor,
        description="集成测试：拒绝需填理由",
        assignee_account_code="XS001",
        due_at=datetime.now(UTC) + timedelta(days=3),
        priority_code="medium",
    )
    sales = await actor_factory(RoleCode.SALES)

    with pytest.raises(TaskConflict):
        await TaskService().apply_event(
            connection, actor=sales, task_id=created["id"], event_type="reject", note="   "
        )

    rejected = await TaskService().apply_event(
        connection, actor=sales, task_id=created["id"], event_type="reject", note="当期资源不足"
    )
    assert rejected["status"] == "cancelled"


async def test_task_cannot_be_accepted_twice(connection, supervisor_actor, actor_factory) -> None:
    created = await TaskService().create(
        connection,
        actor=supervisor_actor,
        description="集成测试：重复接受",
        assignee_account_code="XS001",
        due_at=datetime.now(UTC) + timedelta(days=3),
        priority_code="medium",
    )
    sales = await actor_factory(RoleCode.SALES)
    await TaskService().apply_event(
        connection, actor=sales, task_id=created["id"], event_type="accept", note=None
    )

    with pytest.raises(TaskConflict):
        await TaskService().apply_event(
            connection, actor=sales, task_id=created["id"], event_type="accept", note=None
        )


async def test_non_assignee_cannot_respond_to_task(connection, supervisor_actor, actor_factory) -> None:
    created = await TaskService().create(
        connection,
        actor=supervisor_actor,
        description="集成测试：非受托人回应",
        assignee_account_code="XS001",
        due_at=datetime.now(UTC) + timedelta(days=3),
        priority_code="medium",
    )
    manager = await actor_factory(RoleCode.MANAGER)

    # 无客户的任务对非受托人不可见，RLS 让 SELECT … FOR UPDATE 直接空手，
    # 仓储因此抛 TaskNotFound；有可见权但身份不对才是 TaskForbidden。
    with pytest.raises((TaskForbidden, TaskConflict, TaskNotFound)):
        await TaskService().apply_event(
            connection, actor=manager, task_id=created["id"], event_type="accept", note=None
        )


async def test_unknown_task_raises_not_found(connection, sales_actor) -> None:
    with pytest.raises(TaskNotFound):
        await TaskService().apply_event(
            connection,
            actor=sales_actor,
            task_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
            event_type="accept",
            note=None,
        )


async def test_customer_update_rejects_stale_version(connection, sales_actor) -> None:
    rows = await CustomerRepository().list(
        connection, query=None, level=None, unassigned=None, limit=5
    )
    if not rows:
        pytest.skip("演示库没有一线可见客户")
    detail = await CustomerRepository().detail(connection, customer_id=rows[0]["id"])
    assert detail is not None
    with pytest.raises(VersionConflict):
        await CustomerMutationRepository().update(
            connection,
            sales_actor,
            customer_id=detail["id"],
            data={"demand_summary": "集成测试：过期版本不应写入"},
            expected_version=int(detail["version_no"]) + 1,
        )
    # 当前版本应能更新（事务结束会回滚）
    updated = await CustomerMutationRepository().update(
        connection,
        sales_actor,
        customer_id=detail["id"],
        data={"demand_summary": detail.get("demand_summary") or "集成测试保留原摘要"},
        expected_version=detail["version_no"],
    )
    assert updated["id"] == detail["id"]
    refreshed = await CustomerRepository().detail(connection, customer_id=detail["id"])
    assert refreshed is not None
    assert int(refreshed["version_no"]) == int(detail["version_no"]) + 1
    assert "attributes" in refreshed
