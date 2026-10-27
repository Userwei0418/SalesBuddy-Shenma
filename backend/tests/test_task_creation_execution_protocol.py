"""Human confirmation and legacy entrypoint fences, independent of V257 SQL tests."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sales_backend.contracts.models import TaskBatchCreate, TaskCreate
from sales_backend.domain.tasks import TaskConflict
from sales_backend.services.agent_run.persist import AgentRunStore
from sales_backend.services.tasks import TaskService


def creation(**changes):
    return {
        "description": "核实试点验收标准并反馈",
        "assignee_account_code": "SALES001",
        "due_at": datetime.now(UTC) + timedelta(days=1),
        "execution_protocol_version": 1,
        "checklist": [
            {
                "item_id": str(uuid4()),
                "definition_version": 1,
                "title": "核实验收标准",
                "acceptance_criteria": "记录业务方确认的验收标准",
            }
        ],
    } | changes


@pytest.mark.parametrize(
    "change",
    [
        {"execution_protocol_version": None},
        {"execution_protocol_version": True},
        {"execution_protocol_version": "1"},
        {"checklist": []},
        {"checklist": None},
    ],
)
def test_creation_requires_explicit_protocol_and_nonempty_human_checklist(change):
    with pytest.raises(ValidationError):
        TaskCreate(**creation(**change))


def test_creation_rejects_duplicate_or_noninitial_definitions():
    data = creation()
    with pytest.raises(ValidationError):
        TaskCreate(**(data | {"checklist": data["checklist"] * 2}))
    data["checklist"][0]["definition_version"] = 2
    with pytest.raises(ValidationError):
        TaskCreate(**data)


def test_batch_compares_definitions_but_allows_independent_item_ids():
    first = creation()
    second = first | {
        "assignee_account_code": "SALES002",
        "checklist": [first["checklist"][0] | {"item_id": str(uuid4())}],
    }
    batch = TaskBatchCreate(tasks=[first, second])
    assert len(batch.tasks) == 2
    second["checklist"][0]["acceptance_criteria"] = "不同的要求"
    with pytest.raises(ValidationError, match="必须一致"):
        TaskBatchCreate(tasks=[first, second])


@pytest.mark.asyncio
async def test_unconfirmed_internal_create_fails_before_database_writes():
    db = AsyncMock()
    with pytest.raises(TaskConflict, match="TASK_EXECUTION_CONFIRMATION_REQUIRED"):
        await TaskService().create(
            db, actor=None, description="不完整新协议", due_at=datetime.now(UTC), priority_code="normal", execution_protocol_version=1
        )
    db.fetchval.assert_not_awaited()
    db.execute.assert_not_awaited()


def test_old_create_batch_and_meeting_confirm_remain_valid_without_new_fields():
    from sales_backend.contracts.task_alignment import legacy_safe_task_payload
    from sales_backend.contracts.task_imports import TaskImportConfirmedItem
    payload = creation()
    payload.pop("execution_protocol_version")
    payload.pop("checklist")
    item = TaskCreate(**payload)
    assert item.execution_protocol_version is None and item.checklist == []
    assert "execution_protocol_version" not in legacy_safe_task_payload(item.model_dump())
    assert "checklist" not in legacy_safe_task_payload(item.model_dump())
    assert len(TaskBatchCreate(tasks=[payload, payload | {"assignee_account_code": "SALES002"}]).tasks) == 2
    assert TaskImportConfirmedItem(**payload, item_id=uuid4()).execution_protocol_version is None


@pytest.mark.asyncio
async def test_agent_fully_linked_suggestion_still_requires_human_confirmation(monkeypatch):
    source, customer, opportunity = (str(uuid4()) for _ in range(3))
    candidate = {"source_id": source, "customer_id": customer, "opportunity_id": opportunity}
    draft = SimpleNamespace(
        candidate=candidate,
        source_visit_id=source,
        title="确认方案",
        description="确认交付方案",
        due_at=datetime.now(UTC) + timedelta(days=1),
        priority="normal",
    )
    monkeypatch.setattr(
        "sales_backend.services.agent_run.persist.follow_up_task_drafts", lambda *args, **kwargs: [draft]
    )
    db = SimpleNamespace(fetch=AsyncMock(return_value=[]), execute=AsyncMock(), fetchval=AsyncMock())
    result = await AgentRunStore(None, None)._materialize_today_tasks(
        db, SimpleNamespace(actor=SimpleNamespace(user_id=str(uuid4()))), {}, {"follow_up_candidates": [candidate]}
    )
    assert result["pending_candidates"][0]["requires_human_confirmation"] is True
    assert result["pending_candidates"][0]["opportunity_id"] == opportunity
    assert result["rows"] == []
    db.execute.assert_not_awaited()
    db.fetchval.assert_not_awaited()
