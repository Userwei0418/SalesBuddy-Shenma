"""Per-task mode fences and old request identity; SQL proof lives in the private harness."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.domain.tasks import TaskConflict, TaskNotFound
from sales_backend.repositories.task_execution_projection import normalize_execution
from sales_backend.services.task_compatibility import require_execution_creation_enabled, require_legacy_mutation


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["accept", "reject", "complete", "approve_completion", "reject_completion", "reassign"])
async def test_old_actions_depend_on_persistent_mode(action):
    db = SimpleNamespace(fetchval=AsyncMock(return_value="legacy"))
    await require_legacy_mutation(db, "task", action)
    db.fetchval.return_value = "execution_v1"
    with pytest.raises(TaskConflict, match="CLIENT_UPGRADE_REQUIRED"):
        await require_legacy_mutation(db, "task", action)
    await require_legacy_mutation(db, "task", "cancel")
    db.fetchval.return_value = None
    with pytest.raises(TaskNotFound):
        await require_legacy_mutation(db, "task", action)


@pytest.mark.asyncio
async def test_creation_rollout_is_explicit_fail_closed():
    for enabled in (None, False):
        with pytest.raises(TaskConflict, match="ROLLOUT_DISABLED"):
            await require_execution_creation_enabled(SimpleNamespace(fetchval=AsyncMock(return_value=enabled)))
    await require_execution_creation_enabled(SimpleNamespace(fetchval=AsyncMock(return_value=True)))


def test_legacy_projection_does_not_invent_checklist_or_disable_old_actions():
    permissions = {"task.accept": True, "task.complete": True, "task.review": True}
    item = normalize_execution({"execution_mode": "legacy", "creator_user_ref_id": "creator",
        "action_permissions": dict(permissions), "execution": None})
    assert item["execution"] is None and item["execution_protocol_version"] is None
    assert item["action_permissions"] == permissions and item["current_reviewer"] == "creator"
    assert item["execution_compatibility"]["code"] is None and "checklist" not in item


def test_v1_projection_retains_protocol_and_disables_old_actions():
    item = normalize_execution({"execution_mode": "execution_v1", "execution": {"protocol_version": 1,
        "capabilities": {"accept_task": True}}, "action_permissions": {"task.accept": True}})
    assert item["execution_protocol_version"] == 1
    assert item["execution_capabilities"]["accept_task"] is True
    assert item["action_permissions"]["task.accept"] is False


@pytest.mark.asyncio
async def test_today_agent_card_keeps_saved_mode_without_creating_tasks():
    from datetime import UTC, datetime

    from sales_backend.services.agent_run.persist import AgentRunStore

    rows = [dict(task_id='legacy-task', execution_mode='legacy', title='原任务',
                 due_at=datetime.now(UTC), task_type='management', attributes=None, import_meta=None)]
    db = SimpleNamespace(fetch=AsyncMock(return_value=rows), execute=AsyncMock())
    result = await AgentRunStore(None, None)._materialize_today_tasks(
        db, SimpleNamespace(actor=SimpleNamespace(user_id='user')), {}, {'follow_up_candidates': []}
    )
    assert result['rows'][0]['execution_mode'] == 'legacy'
    db.execute.assert_not_awaited()
