from __future__ import annotations

import pytest

from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.repositories.assistant import AssistantRepository


class _AssistantConnection:
    def __init__(self) -> None:
        self.metric_sql = ""
        self.metric_args = ()

    async def fetchrow(self, statement: str, *args):
        self.metric_sql = statement
        self.metric_args = args
        return {
            "today_visits": 0,
            "open_opportunities": 0,
            "open_risks": 0,
            "my_tasks": 27,
            "completed_tasks": 1,
        }

    async def fetch(self, *_args):
        return []


@pytest.mark.asyncio
async def test_management_task_board_counts_the_same_visible_scope_as_task_center() -> None:
    connection = _AssistantConnection()
    actor = ActorContext(
        workspace_id="00000000-0000-0000-0000-000000000001",
        user_id="02000000-0000-0000-0000-000000000001",
        role=RoleCode.MANAGER,
        data_scope=DataScope.WORKSPACE,
        team_ids=(),
    )

    result = await AssistantRepository().home(connection, actor)

    assert result["my_tasks"] == 27
    assert result["completed_tasks"] == 1
    assert "FROM workflow.task t" in connection.metric_sql
    assert "ta.assignee_user_ref_id = $1::uuid" not in connection.metric_sql
    assert connection.metric_args == ()
