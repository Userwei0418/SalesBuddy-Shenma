from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sales_backend.repositories.task_browse import TaskBrowseRepository, selection
from sales_backend.repositories.tasks import TASK_CAN_CANCEL_SQL, TASK_HANDOVER_REQUIRED_SQL, TaskRepository


def test_filters_keep_fde_scope_and_use_bound_owner_period_values():
    uid = str(uuid4())
    where, args, chosen = selection(
        fde_view="team",
        tab="completed",
        member_id=uid,
        completed_year=2026,
        completed_quarters=[1, 3],
        member="销售甲",
        team="华东",
    )
    assert "security.authorization_task('task.read',t.id)" in where
    assert uid not in where and "销售甲" not in where and "华东" not in where
    assert uid in args and "销售甲" in args and "华东" in args
    assert "a.responsibility='owner'" in where
    dates = [v for v in args if hasattr(v, "month")]
    assert [v.month for v in dates] == [1, 4, 7, 10]
    assert all(v.utcoffset().total_seconds() == 28800 for v in dates)
    assert "t.status<>'completed' OR" in where and "submit_completion" in chosen and "review_completion" in chosen


@pytest.mark.parametrize("overview,column", [("today_pending", "due_at"), ("today_completed", "completed_at")])
def test_today_filters_have_beijing_midnight_half_open_range(overview, column):
    _, _, chosen = selection(tab="all", overview=overview)
    assert f"t.{column}>=" in chosen and f"t.{column}<" in chosen
    assert "Asia/Shanghai" in chosen and "interval '1 day'" in chosen


@pytest.mark.parametrize("tab,overview,task_status", [
    ("pending", None, "in_progress"),
    ("completed", None, "completed"),
    ("all", "today_pending", "deferred"),
    ("pending", "today_completed", "completed"),
])
def test_status_only_narrows_chosen_list_and_preserves_all_base_filters(tab, overview, task_status):
    options = dict(tab=tab, overview=overview, fde_view="team", inbox=True,
                   customer_id=str(uuid4()), opportunity_id=str(uuid4()),
                   member_id=str(uuid4()), team_id=str(uuid4()), team="华东", member="销售甲",
                   opportunity_only=True, completed_year=2026, completed_quarters=[1, 3])
    base_where, base_args, base_chosen = selection(**options)
    where, args, chosen = selection(status=task_status, **options)
    assert where == base_where and args[:-1] == base_args
    assert args[-1] == task_status
    assert chosen == f"({base_chosen}) AND t.status=${len(args)}::text"
    assert f"${len(args)}::text" not in where
    assert "security.authorization_task('task.read',t.id)" in where
    assert "t.opportunity_id IS NOT NULL" in where
    assert "a.responsibility='owner'" in where
    assert "t.status<>'completed' OR" in where


@pytest.mark.asyncio
async def test_page_builds_summary_before_bounded_light_cards():
    class Connection:
        calls = []

        async def fetchrow(self, sql, *args):
            self.calls.append((sql, args))
            return {
                "total": 900,
                "pending_count": 700,
                "completed_count": 200,
                "rejected_count": 0,
                "filtered_total": 700,
            }

        async def fetch(self, sql, *args):
            self.calls.append((sql, args))
            return [{"id": str(i)} for i in range(21)]

    c = Connection()
    result = await TaskBrowseRepository().page(
        c, limit=20, offset=20, tab="pending", fde_view="self", status="in_progress")
    assert len(result["items"]) == 20 and result["summary"]["filtered_total"] == 700
    assert result["has_more"] and result["next_offset"] == 40
    sql, args = c.calls[-1]
    summary_sql, summary_args = c.calls[0]
    where, _, chosen = selection(tab="pending", fde_view="self", status="in_progress")
    assert summary_sql.endswith(f"FROM workflow.task t WHERE {where}")
    assert f"FILTER(WHERE {chosen})::int AS filtered_total" in summary_sql
    assert "t.status=$1::text" not in summary_sql.split("FROM workflow.task t WHERE ", 1)[1]
    assert f"WHERE {where} AND ({chosen})" in sql
    assert summary_args == ("in_progress",) and args == (*summary_args, 21, 20)
    assert args[-2:] == (21, 20) and "WITH page AS MATERIALIZED" in sql
    assert "left(t.description,600)" in sql and "t.source_follow_up_record" not in sql and "t.import_meta" not in sql
    assert TASK_HANDOVER_REQUIRED_SQL in sql and TASK_HANDOVER_REQUIRED_SQL in TaskRepository._SELECT
    assert TASK_CAN_CANCEL_SQL in sql and TASK_CAN_CANCEL_SQL in TaskRepository._SELECT


@pytest.mark.asyncio
async def test_existing_legacy_task_endpoint_uses_original_repository(monkeypatch):
    from sales_backend.api import tasks

    calls = []

    class Repo:
        async def list(self, *args, **kwargs):
            calls.append(kwargs)
            return []

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield object()

    monkeypatch.setattr(tasks, "TaskRepository", Repo)
    result = await tasks.list_tasks(
        task_status=None,
        page_size=20,
        offset=0,
        inbox=False,
        customer_id=None,
        opportunity_id=None,
        view="self",
        tab=None,
        completed_quarters=[],
        completed_year=None,
        identity=SimpleNamespace(actor=SimpleNamespace(role=SimpleNamespace(value="sales"))),
        database=SimpleNamespace(transaction=transaction),
    )
    assert result == {"items": [], "has_more": False, "next_offset": None} and len(calls) == 1
