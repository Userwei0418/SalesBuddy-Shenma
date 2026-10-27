"""The paged task list must expose the same execution projection as detail."""

from datetime import UTC, datetime

import pytest

from sales_backend.repositories.task_browse import TaskBrowseRepository


class FakeConnection:
    def __init__(self):
        self.queries = []

    async def fetchrow(self, query, *args):
        self.queries.append(query)
        return {
            "total": 1,
            "pending_count": 1,
            "overdue_count": 0,
            "today_pending_count": 0,
            "today_completed_count": 0,
            "completed_count": 0,
            "rejected_count": 0,
            "filtered_total": 1,
        }

    async def fetch(self, query, *args):
        self.queries.append(query)
        return [{
            "id": "task-1",
            "execution_mode": "execution_v1",
            "task_type": "management",
            "title": "新版任务",
            "description": "清单任务",
            "execution": {
                "status": "in_progress",
                "protocol_version": 1,
                "progress": {"basis": "checklist", "total": 2, "completed": 1, "percent": 50},
                "capabilities": {"set_item_progress": True},
            },
            "alignment": None,
            "customer_id": None,
            "customer_name": None,
            "opportunity_id": None,
            "opportunity_name": None,
            "target_position": None,
            "status": "in_progress",
            "priority_code": "normal",
            "due_at": datetime(2026, 10, 20, tzinfo=UTC),
            "completed_at": None,
            "created_at": datetime(2026, 10, 17, tzinfo=UTC),
            "updated_at": datetime(2026, 10, 23, 10, 15, tzinfo=UTC),
            "version_no": 1,
            "creator_user_ref_id": "user-1",
            "creator_name": "执行人",
            "owner_name": "执行人",
            "team_name": "测试团队",
            "can_cancel": True,
            "handover_required": False,
            "last_event_type": "start",
        }]


@pytest.mark.asyncio
async def test_paged_task_list_carries_execution_v1_summary():
    connection = FakeConnection()

    result = await TaskBrowseRepository().page(connection, tab="all", limit=20)

    item = result["items"][0]
    assert item["execution_mode"] == "execution_v1"
    assert item["execution_protocol_version"] == 1
    assert item["updated_at"] == datetime(2026, 10, 23, 10, 15, tzinfo=UTC)
    assert item["updated_at"] != item["created_at"]
    assert item["progress"] == {"basis": "checklist", "total": 2, "completed": 1, "percent": 50}
    assert item["execution_flags"] == {
        "paused": False,
        "execution_handover_pending": False,
        "reviewer_handover_pending": False,
        "requirements_pending": False,
        "awaiting_review": False,
        "progress_basis_missing": False,
    }
    assert "t.execution_mode" in connection.queries[1]
    assert "workflow.task_execution_summary(t.id)" in connection.queries[1]
    # The materialized page and its outer projection must both retain the source timestamp.
    assert connection.queries[1].count("t.updated_at") == 2
