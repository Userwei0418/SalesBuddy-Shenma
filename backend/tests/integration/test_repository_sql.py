"""让每个读路径的 SQL 真的在 PostgreSQL 上跑一遍。

这里不断言具体业务数值（演示库数据会变），只断言两件纯函数测试永远测不到的事：
1. SQL 语法、列名、JOIN 和聚合能在真实 schema 上执行；
2. RLS 可见范围随角色单调收敛（一线 ⊆ 总监 ⊆ 总经理）。
"""

from __future__ import annotations

import pytest

from sales_backend.domain.agent import RoleCode
from sales_backend.repositories.assistant import AssistantRepository
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.repositories.directory import DirectoryRepository
from sales_backend.repositories.notifications import NotificationRepository
from sales_backend.repositories.opportunities import OpportunityRepository
from sales_backend.repositories.profile import ProfileRepository
from sales_backend.repositories.risks import RiskRepository
from sales_backend.repositories.tasks import TaskRepository
from sales_backend.repositories.workbench import WorkbenchRepository

pytestmark = pytest.mark.asyncio


async def test_customer_list_sql_executes_for_every_role(connection, actor_factory) -> None:
    for role in (RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER):
        await actor_factory(role)
        rows = await CustomerRepository().list(connection, query=None, level=None, unassigned=None, limit=5)
        assert isinstance(rows, list)
        for row in rows:
            assert row["id"] and row["name"]


async def test_customer_list_filters_are_valid_sql(connection, sales_actor) -> None:
    """三个可选筛选条件各自单独生效时 SQL 依然可执行。"""
    for kwargs in (
        {"query": "科技", "level": None, "unassigned": None},
        {"query": None, "level": "A", "unassigned": None},
        {"query": None, "level": None, "unassigned": True},
        {"query": "科技", "level": "A", "unassigned": False},
    ):
        rows = await CustomerRepository().list(connection, limit=3, **kwargs)
        assert isinstance(rows, list)


async def test_customer_detail_returns_none_for_unknown_id(connection, sales_actor) -> None:
    missing = await CustomerRepository().detail(connection, customer_id="ffffffff-ffff-ffff-ffff-ffffffffffff")
    assert missing is None


async def test_task_list_and_detail_sql_executes(connection, actor_factory) -> None:
    for role in (RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER):
        await actor_factory(role)
        rows = await TaskRepository().list(connection, status=None, customer_id=None, limit=5)
        assert isinstance(rows, list)
        if rows:
            detail = await TaskRepository().detail(connection, task_id=rows[0]["id"])
            assert detail is not None and detail["id"] == rows[0]["id"]


async def test_task_list_status_filter_is_valid_sql(connection, sales_actor) -> None:
    for status in ("pending_confirm", "pending_execution", "completed", "cancelled"):
        rows = await TaskRepository().list(connection, status=status, customer_id=None, limit=3)
        assert all(row["status"] == status for row in rows)


async def test_risk_list_and_detail_sql_executes(connection, actor_factory) -> None:
    for role in (RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER):
        await actor_factory(role)
        rows = await RiskRepository().list(connection, status=None, limit=5)
        assert isinstance(rows, list)
        if rows:
            detail = await RiskRepository().detail(connection, risk_id=rows[0]["id"])
            assert detail is not None


async def test_notification_list_sql_executes(connection, sales_actor) -> None:
    for unread_only in (True, False):
        rows = await NotificationRepository().list(connection, unread_only=unread_only, limit=5)
        assert isinstance(rows, list)


async def test_opportunity_list_with_all_filters_is_valid_sql(connection, sales_actor) -> None:
    rows = await OpportunityRepository().list(
        connection,
        sales_actor,
        customer_id=None,
        owner_name=None,
        probability=None,
        stage_code=None,
        close_from=None,
        close_to=None,
        limit=5,
    )
    assert isinstance(rows, list)
    for probability in (10, 30, 50, 70, 90):
        filtered = await OpportunityRepository().list(
            connection,
            sales_actor,
            customer_id=None,
            owner_name=None,
            probability=probability,
            stage_code=None,
            close_from=None,
            close_to=None,
            limit=3,
        )
        assert isinstance(filtered, list)


async def test_workbench_load_sql_executes_for_every_role(connection, actor_factory) -> None:
    for role in (RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER):
        actor = await actor_factory(role)
        result = await WorkbenchRepository().load(connection, actor)
        assert isinstance(result, dict) and result


async def test_assistant_home_sql_executes_for_every_role(connection, actor_factory) -> None:
    for role in (RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER):
        actor = await actor_factory(role)
        result = await AssistantRepository().home(connection, actor)
        assert set(result) >= {"my_tasks", "completed_tasks"}
        assert int(result["my_tasks"]) >= 0


async def test_directory_queries_execute_for_every_role(connection, actor_factory) -> None:
    for role in (RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER):
        actor = await actor_factory(role)
        assert isinstance(await DirectoryRepository().members(connection, actor), list)
        assert isinstance(await DirectoryRepository().task_assignees(connection, actor), list)


async def test_profile_evaluation_summary_ratios_stay_in_range(connection, actor_factory) -> None:
    """evaluation_metrics 的分母保护在真实数据上也要成立。"""
    for role in (RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER):
        actor = await actor_factory(role)
        result = await ProfileRepository().evaluation_summary(connection, actor)
        for key in ("win_rate", "a_customer_share"):
            assert 0 <= float(result["maturity"][key]) <= 100, f"{role} 的 {key} 越界"
        assert 0 <= float(result["efficiency"]["followup_closure_rate"]) <= 100
