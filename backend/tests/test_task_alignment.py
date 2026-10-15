"""Boundary checks for additive task/KR contracts and old request fingerprints."""

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from sales_backend.api.advice import SuggestionDecision
from sales_backend.contracts.models import TaskBatchCreate, TaskCreate, TaskEventCreate
from sales_backend.contracts.task_alignment import (
    TaskActualContribution,
    TaskAlignmentInput,
    TaskAlignmentUpdate,
    TaskContribution,
    legacy_safe_task_payload,
)
from sales_backend.contracts.task_imports import TaskImportConfirmedItem
from sales_backend.domain.task_imports import confirmation_hash
from sales_backend.domain.tasks import TaskConflict, TaskForbidden, TaskNotFound
from sales_backend.services.task_alignment import report_task_contribution, save_task_alignment

TASK = dict(description="请核实客户试点并汇报", assignee_account_code="XS001",
            due_at=datetime(2030, 1, 2, tzinfo=UTC))
KR = "20000000-0000-4000-8000-000000000001"


def test_old_create_batch_advice_and_event_keep_original_payload_defaults():
    original = dict(association_kind=None, description=TASK["description"], assignee_account_code="XS001",
                    target_position=None, due_at=TASK["due_at"], priority_code="normal",
                    customer_id=None, opportunity_id=None)
    assert legacy_safe_task_payload(TaskCreate(**TASK).model_dump()) == original
    assert legacy_safe_task_payload(TaskBatchCreate(tasks=[TASK]).model_dump()) == {"tasks": [original]}
    for key, value in (("task", TASK), ("tasks", [TASK])):
        body = SuggestionDecision(decision="adopted", version_no=1, **{key: value})
        expected = {"decision": "adopted", "version_no": 1, "note": "", "task": None, "tasks": None}
        expected[key] = original if key == "task" else [original]
        assert legacy_safe_task_payload(body.model_dump()) == expected
    assert legacy_safe_task_payload(TaskEventCreate(event_type="complete", note="完成说明").model_dump()) == {
        "event_type": "complete", "note": "完成说明", "version_no": None, "assignee_account_code": None,
    }


def test_meeting_confirm_fingerprint_matches_original_bytes_without_new_fields():
    item = TaskImportConfirmedItem(**TASK, item_id=KR)
    before = item.model_dump(mode="json", exclude={"duplicate_acknowledged", "alignment"})
    expected = hashlib.sha256(json.dumps([before], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert confirmation_hash([item]) == expected
    item.alignment = TaskAlignmentInput(metric_codes=["daily_routine"])
    assert confirmation_hash([item]) != expected


def test_deduplicate_without_inventing_metric_or_financial_identity():
    assert TaskCreate(**TASK).alignment is None
    body = TaskAlignmentInput(metric_codes=["daily_routine", "daily_routine"], key_result_ids=[KR, KR])
    assert body.metric_codes == ["daily_routine"] and body.key_result_ids == [KR]
    assert body.expected_contribution is None
    assert TaskAlignmentInput(key_result_ids=[KR]).metric_codes == []  # Derived only by the database.
    with pytest.raises(ValidationError):
        TaskAlignmentInput()


@pytest.mark.parametrize("value", [1, 1.25, True, "-1", "NaN", "Infinity", "1e2", "1.001", "01", "10000000000000000"])
def test_contribution_rejects_float_nonfinite_negative_and_ambiguous_precision(value):
    with pytest.raises(ValidationError):
        TaskContribution(metric_code="visit_count", value=value)


def test_contribution_zero_and_exact_cent_survive_serialization():
    large = TaskContribution(metric_code="collection", value="9999999999999999.99")
    assert large.model_dump()["value"] == "9999999999999999.99"
    assert TaskContribution(metric_code="visit_count", value="0").value == "0"
    for values in ({"unit": "元"}, {"counts_toward_financial_actuals": True}, {"status": "confirmed"}):
        with pytest.raises(ValidationError):
            TaskContribution(metric_code="visit_count", value="1", **values)


def test_actual_claim_needs_evidence_and_explicit_task_version_only_on_completion():
    claim = {"metric_code": "visit_count", "value": "1", "note": "已完成跟进，记录可查"}
    event = TaskEventCreate(event_type="complete", note="完成", version_no=2, actual_contribution=claim)
    assert event.actual_contribution.value == "1"
    for values in ({"event_type": "complete", "note": "完成"}, {"event_type": "accept", "version_no": 2}):
        with pytest.raises(ValidationError):
            TaskEventCreate(**values, actual_contribution=claim)
    with pytest.raises(ValidationError):
        TaskActualContribution(**{**claim, "note": "  "})
    with pytest.raises(ValidationError):
        TaskAlignmentUpdate(version_no=True, alignment={"metric_codes": ["daily_routine"]})


@pytest.mark.asyncio
async def test_legacy_missing_alignment_and_claim_perform_no_new_queries():
    connection = SimpleNamespace(fetchval=AsyncMock())
    assert await save_task_alignment(connection, KR, None) is None
    assert await report_task_contribution(connection, KR, None, None) is None
    connection.fetchval.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("error,expected", [
    (asyncpg.InsufficientPrivilegeError("denied"), TaskForbidden),
    (asyncpg.NoDataFoundError("absent"), TaskNotFound),
    (asyncpg.RaiseError("stale"), TaskConflict),
    (asyncpg.InvalidParameterValueError("disabled"), TaskConflict),
])
async def test_scoped_database_failures_remain_user_visible(error, expected):
    connection = SimpleNamespace(fetchval=AsyncMock(side_effect=error))
    with pytest.raises(expected):
        await save_task_alignment(connection, KR, {"metric_codes": ["daily_routine"]}, 1)
    assert connection.fetchval.await_count == 1


@pytest.mark.asyncio
async def test_reported_claim_cannot_skip_expected_version_or_write_other_stores():
    connection = SimpleNamespace(fetchval=AsyncMock(return_value={"status": "reported"}), execute=AsyncMock())
    claim = {"metric_code": "visit_count", "value": "1", "note": "已跟进"}
    with pytest.raises(TaskConflict):
        await report_task_contribution(connection, KR, claim, None)
    connection.fetchval.assert_not_called()
    result = await report_task_contribution(connection, KR, claim, 1)
    assert result == {"status": "reported"}
    assert connection.fetchval.call_args.args[0].startswith("SELECT security.record_task_contribution")
    connection.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("access,status", [(None, 404), ({"readable": False, "editable": True}, 403),
                                          ({"readable": True, "editable": False}, 403)])
async def test_alignment_replay_reauthorizes_object_before_receipt(monkeypatch, access, status):
    from sales_backend.api import tasks
    connection = SimpleNamespace(fetchrow=AsyncMock(return_value=access))
    @asynccontextmanager
    async def transaction(actor):
        yield connection
    receipt = AsyncMock(return_value={"id": KR})
    monkeypatch.setattr(tasks, "execute_mutation", receipt)
    with pytest.raises(HTTPException) as denied:
        await tasks.update_task_alignment(KR,
            TaskAlignmentUpdate(version_no=1, alignment={"metric_codes": ["daily_routine"]}),
            SimpleNamespace(actor=SimpleNamespace(workspace_id=KR)), SimpleNamespace(transaction=transaction), KR)
    assert denied.value.status_code == status
    receipt.assert_not_called()
    assert "authorization_task('task.read'" in connection.fetchrow.call_args.args[0]
    assert "fde_can_coordinate_task" in connection.fetchrow.call_args.args[0]


@pytest.mark.asyncio
async def test_alignment_replay_does_not_recheck_old_version_or_lifecycle(monkeypatch):
    from sales_backend.api import tasks
    connection = SimpleNamespace(fetchrow=AsyncMock(return_value={"readable": True, "editable": True}))
    @asynccontextmanager
    async def transaction(actor):
        yield connection
    receipt = AsyncMock(return_value={"id": KR, "version_no": 2})
    monkeypatch.setattr(tasks, "execute_mutation", receipt)
    result = await tasks.update_task_alignment(KR,
        TaskAlignmentUpdate(version_no=1, alignment={"metric_codes": ["daily_routine"]}),
        SimpleNamespace(actor=SimpleNamespace(workspace_id=KR)), SimpleNamespace(transaction=transaction), KR)
    assert result["version_no"] == 2
    receipt.assert_awaited_once()
    query = connection.fetchrow.call_args.args[0]
    assert "version_no" not in query and "t.status" not in query
