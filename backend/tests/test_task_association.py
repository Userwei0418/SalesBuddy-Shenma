from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from sales_backend.contracts.models import TaskCreate
from sales_backend.domain.advice import AdviceError
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.tasks import TaskConflict, TaskForbidden, task_association_kind
from sales_backend.services.advice import AdviceService
from sales_backend.services.agent_run.persist import AgentRunStore
from sales_backend.services.tasks import TaskService, validate_task_links


def actor(role="sales"):
    return ActorContext(workspace_id=str(uuid4()), user_id=str(uuid4()), role=role,
                        data_scope="self", team_ids=(str(uuid4()),))


def test_new_tasks_require_both_customer_links_and_daily_has_neither():
    assert task_association_kind() == "daily"
    assert task_association_kind("c", "o") == "customer"
    for values in [("c", None, None), (None, "o", None), ("c", "o", "daily"), (None, None, "customer"),
                   ("c", None, "legacy_customer")]:
        with pytest.raises(TaskConflict):
            task_association_kind(*values)


@pytest.mark.asyncio
async def test_link_validation_rejects_wrong_customer_and_inaccessible_opportunity():
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=True),
                                 fetchrow=AsyncMock(return_value={"id": "o", "customer_id": "other"}))
    with pytest.raises(TaskConflict):
        await validate_task_links(connection, actor(), "c", "o")
    connection.fetchrow.return_value = {"id": "o", "customer_id": "c"}
    connection.fetchval.side_effect = [True, False]
    with pytest.raises(TaskForbidden):
        await validate_task_links(connection, actor(), "c", "o")


@pytest.mark.asyncio
async def test_agent_incomplete_source_remains_candidate_without_writes():
    connection = SimpleNamespace(fetch=AsyncMock(return_value=[]), execute=AsyncMock(), fetchval=AsyncMock())
    source, customer = str(uuid4()), str(uuid4())
    result = await AgentRunStore(None, None)._materialize_today_tasks(
        connection, SimpleNamespace(actor=actor()), {},
        {"follow_up_candidates": [{"source_id": source, "customer_id": customer,
                                   "opportunity_id": None, "next_action": "联系客户确认验收标准"}]})
    assert result["rows"] == []
    assert result["pending_candidates"][0]["source_visit_id"] == source
    connection.fetchval.assert_not_called()
    connection.execute.assert_not_called()


def advice_context(source_opportunity=None, kind="visit"):
    service = AdviceService.__new__(AdviceService)
    customer, suggestion = str(uuid4()), str(uuid4())
    row = {"customer_id": customer, "opportunity_id": source_opportunity, "subject_kind": kind,
           "status": "succeeded", "cache_key": "same"}
    service.repo = SimpleNamespace(get=AsyncMock(return_value=row))
    service.assert_actor = AsyncMock()
    service.key_for = AsyncMock(return_value="same")
    connection = SimpleNamespace(fetchrow=AsyncMock(return_value={"version_no": 1, "decision": "pending", "advice_id": str(uuid4())}),
                                 execute=AsyncMock())
    return service, connection, customer, suggestion


@pytest.mark.asyncio
async def test_advice_without_opportunity_accepts_human_selection_and_keeps_source(monkeypatch):
    service, connection, customer, suggestion = advice_context()
    selected = str(uuid4())
    task = TaskCreate(description="联系客户确认验收标准", assignee_account_code="OTHER_TEAM",
                      due_at=datetime.now(UTC)+timedelta(days=1), customer_id=customer, opportunity_id=selected,
                      association_kind="customer")
    create = AsyncMock(return_value={"id": str(uuid4())})
    monkeypatch.setattr(TaskService, "create", create)
    await service.decide(connection, actor("fde"), suggestion, "adopted", "", 1, task, None)
    assert create.call_args.kwargs["opportunity_id"] == selected
    assert create.call_args.kwargs["customer_id"] == customer
    assert create.call_args.kwargs["source_suggestion_id"] == suggestion
    assert create.call_args.kwargs["association_kind"] == "customer"


@pytest.mark.asyncio
async def test_fixed_source_and_missing_selection_cannot_be_reclassified_as_daily():
    for fixed, selected, association in [(str(uuid4()), str(uuid4()), "customer"),
                                         (None, None, None), (None, None, "daily")]:
        service, connection, customer, suggestion = advice_context(fixed)
        task = TaskCreate(description="联系客户确认验收标准", assignee_account_code="OTHER_TEAM",
                          due_at=datetime.now(UTC)+timedelta(days=1), customer_id=customer,
                          opportunity_id=selected, association_kind=association)
        with pytest.raises(AdviceError):
            await service.decide(connection, actor(), suggestion, "adopted", "", 1, task, None)
        connection.execute.assert_not_called()


@pytest.mark.asyncio
async def test_fde_cannot_decide_customer_level_advice():
    service, connection, _, suggestion = advice_context(kind="customer")
    with pytest.raises(AdviceError):
        await service.decide(connection, actor("fde"), suggestion, "no_task", "", 1, None, None)
    connection.execute.assert_not_called()
