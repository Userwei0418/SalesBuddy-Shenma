"""Small contract/safety checks; state transitions are exercised by V285's isolated SQL probe."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sales_backend.contracts.models import TaskCreate
from sales_backend.contracts.task_execution import (
    TaskChecklistSave,
    TaskFeedback,
    command_payload,
)
from sales_backend.domain.tasks import TaskConflict
from sales_backend.repositories.task_execution_projection import normalize_execution
from sales_backend.services.task_compatibility import require_legacy_mutation
from sales_backend.services.task_execution import TaskExecutionError, _normalize_event, read_history
from sales_backend.services.tasks import TaskService


def test_v2_can_be_dispatched_without_checklist_but_requires_overall_agreement():
    value = dict(
        description="确认客户试点计划",
        target_position="self",
        due_at=datetime.now(UTC) + timedelta(days=1),
        execution_protocol_version=2,
        deliverable="一份客户确认的试点计划",
        acceptance_criteria="写明日期、负责人及验收指标",
    )
    assert TaskCreate(**value).checklist == []
    with pytest.raises(ValidationError):
        TaskCreate(**(value | {"deliverable": "  "}))
    with pytest.raises(ValidationError):
        TaskChecklistSave(
            version_no=1, requirements_version=1, responsibility_version=1, checklist=[], reason="建立执行清单"
        )


def test_legacy_feedback_receipt_does_not_gain_optional_v2_fields():
    body = dict(version_no=1, requirements_version=1, responsibility_version=1, text="确认需求")
    assert command_payload(TaskFeedback(**body)) == body | {"reply_to_event_id": None, "corrects_event_id": None}
    assert (
        _normalize_event({"actor_user_id": "known-actor", "kind": "append_feedback"})["actor_user_id"] == "known-actor"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,position,same_owner,accepted",
    [
        ("mini_program", None, True, True),
        ("mini_program", "self", True, True),
        ("business_advice", None, True, True),
        ("meeting_import", None, True, False),
        ("mini_program", None, False, False),
        ("mini_program", "supervisor", True, False),
    ],
)
async def test_creation_passes_only_explicit_personal_confirmation_to_database(
    monkeypatch, source, position, same_owner, accepted
):
    actor = SimpleNamespace(workspace_id=str(uuid4()), user_id=str(uuid4()), team_ids=())
    target = dict(
        user_id=actor.user_id if same_owner else str(uuid4()), team_id=None, role_code="sales", display_name="执行人"
    )
    monkeypatch.setattr("sales_backend.services.tasks.require_execution_creation_enabled", AsyncMock())
    monkeypatch.setattr("sales_backend.services.tasks.validate_task_links", AsyncMock(return_value="daily"))
    monkeypatch.setattr(
        "sales_backend.services.tasks.AuthorizationRepository.effective",
        AsyncMock(return_value=SimpleNamespace(require=Mock())),
    )
    monkeypatch.setattr("sales_backend.services.tasks.TaskTargetRepository.resolve", AsyncMock(return_value=[target]))
    monkeypatch.setattr("sales_backend.services.tasks.TaskMutationRepository.assign_owner", AsyncMock())
    notify = AsyncMock()
    monkeypatch.setattr("sales_backend.services.tasks.TaskMutationRepository.notify", notify)
    monkeypatch.setattr("sales_backend.services.tasks.TaskMutationRepository.event", AsyncMock())
    monkeypatch.setattr("sales_backend.services.tasks.TaskRepository.detail", AsyncMock(return_value={"saved": True}))
    initialize = AsyncMock()
    monkeypatch.setattr("sales_backend.services.task_execution.initialize_execution", initialize)
    db = SimpleNamespace(fetchval=AsyncMock(side_effect=["发起人", str(uuid4())]), execute=AsyncMock())
    await TaskService().create(
        db,
        actor=actor,
        description="确认客户试点计划",
        due_at=datetime.now(UTC) + timedelta(days=1),
        priority_code="normal",
        target_position=position,
        assignee_account_code=None if position else "SALES001",
        source_code=source,
        execution_protocol_version=2,
        deliverable="客户确认计划",
        acceptance_criteria="明确时间与目标",
        checklist=[],
    )
    assert initialize.await_args.kwargs == {"protocol_version": 2, "confirmed_self": accepted}
    assert initialize.await_args.args[2]["checklist"] == []
    assert notify.await_count == (0 if accepted else 1)
    if position == "self":
        assert db.fetchval.await_args.args[10] is None


@pytest.mark.asyncio
async def test_history_cursor_paginates_uuid_rows_and_cannot_cross_task_or_kind():
    task = str(uuid4())
    rows = [
        dict(
            id=uuid4(),
            actor_user_ref_id=uuid4(),
            kind="append_feedback",
            body={"command": {"text": str(index)}},
            occurred_at=datetime.now(UTC),
        )
        for index in range(3)
    ]
    db = SimpleNamespace(fetchval=AsyncMock(return_value=True), fetch=AsyncMock(return_value=rows))
    page = await read_history(db, task, "events", None, 2)
    assert len(page["items"]) == 2 and page["next_cursor"]
    assert page["items"][0]["actor_user_id"] == rows[0]["actor_user_ref_id"]
    await read_history(db, task, "events", page["next_cursor"], 2)
    assert db.fetch.await_args.args[3] == str(rows[1]["id"])
    calls = db.fetchval.await_count
    for changed_task, kind in [(str(uuid4()), "events"), (task, "submissions")]:
        with pytest.raises(TaskExecutionError, match="TASK_EXECUTION_CURSOR_INVALID"):
            await read_history(db, changed_task, kind, page["next_cursor"], 2)
    assert db.fetchval.await_count == calls


@pytest.mark.asyncio
async def test_v2_legacy_cancel_cannot_bypass_versions_and_projection_retains_facts():
    db = SimpleNamespace(fetchval=AsyncMock(return_value="execution_v2"))
    with pytest.raises(TaskConflict, match="TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED"):
        await require_legacy_mutation(db, str(uuid4()), "cancel")
    item = normalize_execution(
        {
            "execution_mode": "execution_v2",
            "execution": {
                "protocol_version": 2,
                "checklist_revision": 3,
                "self_managed": True,
                "review_required": False,
                "requirements_incomplete": True,
                "missing_deliverables": "还缺客户反馈",
                "capabilities": {"save_checklist": True},
            },
            "action_permissions": {"task.cancel": True},
        }
    )
    assert item["self_managed"] and item["checklist_revision"] == 3
    assert item["missing_deliverables"] == "还缺客户反馈"
    assert item["execution_compatibility"]["legacy_mutations_supported"] == []
    assert item["action_permissions"]["task.cancel"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_count", [1, 2])
async def test_legacy_multi_owner_can_choose_existing_owner_without_duplicate_assignment(monkeypatch, owner_count):
    from sales_backend.services.task_coordination import coordinate_task

    actor = SimpleNamespace(workspace_id=str(uuid4()), user_id=str(uuid4()))
    owner = str(uuid4())
    prior = [dict(user_id=owner, role="sales", team_id=None, name="执行人")]
    if owner_count == 2:
        prior.append(dict(user_id=str(uuid4()), role="sales", team_id=None, name="另一执行人"))
    task = dict(
        id=str(uuid4()),
        title="原任务",
        status="in_progress",
        version_no=3,
        creator_user_ref_id=actor.user_id,
        opportunity_id=None,
        customer_id=None,
    )
    monkeypatch.setattr("sales_backend.services.task_coordination.require_permission", AsyncMock())
    monkeypatch.setattr("sales_backend.services.task_coordination.require_legacy_mutation", AsyncMock())
    monkeypatch.setattr("sales_backend.services.task_coordination._coordinatable_task", AsyncMock(return_value=task))
    monkeypatch.setattr(
        "sales_backend.services.task_coordination.TaskTargetRepository.resolve",
        AsyncMock(return_value=[dict(user_id=owner, team_id=None, role_code="sales", display_name="执行人")]),
    )
    assign, event = AsyncMock(), AsyncMock()
    monkeypatch.setattr("sales_backend.services.task_coordination.TaskMutationRepository.assign_owner", assign)
    monkeypatch.setattr("sales_backend.services.task_coordination.TaskMutationRepository.event", event)
    monkeypatch.setattr("sales_backend.services.task_coordination.TaskMutationRepository.notify", AsyncMock())
    monkeypatch.setattr("sales_backend.services.task_coordination.TaskRepository.detail", AsyncMock(return_value=task))
    db = SimpleNamespace(
        fetch=AsyncMock(return_value=prior), fetchval=AsyncMock(return_value="协调人"), execute=AsyncMock()
    )
    if owner_count == 1:
        with pytest.raises(TaskConflict, match="当前负责人就是此人"):
            await coordinate_task(
                db,
                actor=actor,
                task_id=task["id"],
                event_type="reassign",
                note="明确唯一主责",
                expected_version=3,
                account="SALES001",
            )
        db.execute.assert_not_awaited()
    else:
        await coordinate_task(
            db,
            actor=actor,
            task_id=task["id"],
            event_type="reassign",
            note="明确唯一主责",
            expected_version=3,
            account="SALES001",
        )
        assign.assert_not_awaited()
        assert "accepted_at=NULL" in db.execute.await_args_list[0].args[0]
        assert event.await_args.args[7]["previous_owners"] == prior
        assert event.await_args.args[5] == "pending_confirm"


def test_login_capability_model_preserves_v2_creation_instead_of_stripping_it():
    from sales_backend.auth.models import TaskExecutionAvailability
    from sales_backend.contracts.task_execution import TaskExecutionInitialize

    value = TaskExecutionAvailability.model_validate(
        {
            "protocol_version": 1,
            "available": True,
            "creation_enabled": False,
            "v2_creation_enabled": True,
            "default_creation_mode": "execution_v2",
            "supported_versions": [1, 2],
            "checklist_owner_editable": True,
        }
    ).model_dump()
    assert value["creation_enabled"] is False and value["v2_creation_enabled"] and value["checklist_owner_editable"]
    assert value["protocol_version"] == 1 and value["supported_versions"] == [1, 2]
    assert value["default_creation_mode"] == "execution_v2"
    command = TaskExecutionInitialize(
        version_no=1, requirements_version=1, responsibility_version=1, checklist_revision=1, reason="接入原任务"
    )
    assert "resume_status" not in command_payload(command)
    resumed = TaskExecutionInitialize(**(command_payload(command) | {"resume_status": "pending_confirm"}))
    assert command_payload(resumed)["resume_status"] == "pending_confirm"


def test_overview_http_response_keeps_current_execution_facts(monkeypatch):
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sales_backend.api.dependencies import get_database, get_identity
    from sales_backend.api.tasks import router

    class ReadOnlyDatabase:
        @asynccontextmanager
        async def transaction(self, actor, readonly=False):
            assert readonly
            yield self

    task_id, actor_id = str(uuid4()), str(uuid4())
    item = normalize_execution({
        "id": task_id, "status": "in_progress", "attributes": {}, "handover_required": False,
        "execution_mode": "execution_v2", "execution": {
            "protocol_version": 2, "self_managed": True, "review_required": False,
            "progress": {"percent": 50, "done": 1, "total": 2},
            "executor_user_id": actor_id, "executor": {"user_id": actor_id, "name": "本人"},
            "current_reviewer": actor_id, "checklist_revision": 3,
            "requirements_revalidation": True, "requirements_incomplete": False,
            "coordination_required": {"reviewer_unavailable": False},
            "capabilities": {"set_item_progress": True},
        },
    })
    overview = AsyncMock(return_value={
        "metrics": {"today_completed": 0, "today_pending": 1, "all_pending": 1}, "items": [item],
    })
    monkeypatch.setattr("sales_backend.api.tasks.TaskRepository.overview", overview)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=object())
    app.dependency_overrides[get_database] = ReadOnlyDatabase
    with TestClient(app) as client:
        response = client.get("/api/v1/tasks/overview", params={"task_ids": task_id})
    assert response.status_code == 200
    card = response.json()["items"][0]
    assert card["execution_mode"] == "execution_v2" and card["execution_protocol_version"] == 2
    assert card["self_managed"] and card["review_required"] is False
    assert card["progress"]["percent"] == 50 and card["checklist_revision"] == 3
    assert card["requirements_revalidation"] and card["executor_user_id"] == actor_id
    assert card["current_reviewer"] == actor_id and card["executor"]["name"] == "本人"
    assert card["execution_capabilities"]["set_item_progress"]
    assert card["execution_flags"]["paused"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "execution_v1"])
async def test_adoption_versions_use_database_baseline_without_changing_v1_commands(monkeypatch, mode):
    from sales_backend.services.task_execution import read_execution

    database_value = {
        "execution_mode": mode, "version_no": 9, "requirements_version": 3,
        "responsibility_version": 4, "checklist_revision": 2,
        "capabilities": {"initialize_execution": False},
    }
    monkeypatch.setattr(
        "sales_backend.services.task_execution.TaskExecutionRepository.read",
        AsyncMock(return_value=database_value),
    )
    monkeypatch.setattr(
        "sales_backend.services.task_execution.TaskExecutionRepository.history", AsyncMock(return_value=[]),
    )
    result = await read_execution(object(), str(uuid4()))
    assert result["initialize_versions"] == {
        "version_no": 9, "requirements_version": 3, "responsibility_version": 4, "checklist_revision": 2,
    }
    assert result["capabilities"]["initialize_execution"] is False
    assert "initialize_versions" not in database_value
    v1 = command_payload(TaskFeedback(version_no=9, requirements_version=3, responsibility_version=4, text="继续"))
    assert "checklist_revision" not in v1
