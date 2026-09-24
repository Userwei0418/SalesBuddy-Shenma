from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sales_backend.repositories.task_browse import TaskBrowseRepository, selection
from sales_backend.repositories.tasks import TASK_HANDOVER_REQUIRED_SQL, TaskRepository


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
    assert "t.status<>'completed' OR" in where and chosen == "t.status='completed'"


@pytest.mark.parametrize("overview,column", [("today_pending", "due_at"), ("today_completed", "completed_at")])
def test_today_filters_have_beijing_midnight_half_open_range(overview, column):
    _, _, chosen = selection(tab="all", overview=overview)
    assert f"t.{column}>=" in chosen and f"t.{column}<" in chosen
    assert "Asia/Shanghai" in chosen and "interval '1 day'" in chosen


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
    result = await TaskBrowseRepository().page(c, limit=20, offset=20, tab="pending", fde_view="self")
    assert len(result["items"]) == 20 and result["summary"]["filtered_total"] == 700
    assert result["has_more"] and result["next_offset"] == 40
    sql, args = c.calls[-1]
    assert args[-2:] == (21, 20) and "WITH page AS MATERIALIZED" in sql
    assert "left(t.description,600)" in sql and "t.source_follow_up_record" not in sql and "t.import_meta" not in sql
    assert TASK_HANDOVER_REQUIRED_SQL in sql and TASK_HANDOVER_REQUIRED_SQL in TaskRepository._SELECT


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
