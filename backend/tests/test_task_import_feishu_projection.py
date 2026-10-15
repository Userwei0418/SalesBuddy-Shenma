"""Batch reporting follows the task lifecycle; it cannot infer a completed task."""
from uuid import UUID

import pytest
from pydantic import ValidationError

from sales_backend.domain.feishu_sync.config import COMMON_FIELDS, SOURCE_FIELDS, SyncConfig
from sales_backend.domain.feishu_sync.projection import RELATIONS, project

FIELDS = SOURCE_FIELDS["task"] | COMMON_FIELDS


@pytest.mark.parametrize("status,active,completed,pending", [
    ("pending_acceptance", 1, 0, 0), ("in_progress", 1, 0, 0),
    ("pending_review", 1, 0, 1), ("completed", 1, 1, 0), ("cancelled", 0, 0, 0),
])
def test_reporting_counts_preserve_acceptance_and_cancel_semantics(status, active, completed, pending):
    raw = {"id": "fixture", "status": status,
           "due_at": "2026-09-28T17:00:00+08:00", "completed_at": "2026-09-28T09:00:00Z"}
    values = project("task", raw, FIELDS)
    assert (values["active_count"], values["completed_count"], values["pending_review_count"]) == (
        active, completed, pending)
    assert values["ontime_completed_count"] == completed


@pytest.mark.parametrize("completed_at,due_at,expected", [
    (None, "2026-09-28T17:00:00+08:00", 0),
    ("2026-09-28T09:00:01Z", "2026-09-28T17:00:00+08:00", 0),
    ("2026-09-28T08:59:59Z", "2026-09-28T17:00:00+08:00", 1),
    ("2026-09-28T17:00:00", "2026-09-28T09:00:00Z", 1),
    ("2026-09-28T08:00:00Z", None, 0),
])
def test_on_time_uses_final_completion_not_submission(completed_at, due_at, expected):
    values = project("task", {"id": "fixture", "status": "completed", "completed_at": completed_at,
                              "due_at": due_at, "completion_submitted_at": "2026-09-27T01:00:00Z"}, FIELDS)
    assert values["ontime_completed_count"] == expected


def test_transfer_exports_current_owner_and_separate_original_snapshot():
    raw = {"id": "fixture", "status": "in_progress", "owner_id": "new", "owner_name": "接手人",
           "owner_account": "NEW", "original_owner_id": "old", "original_owner_name": "原负责人",
           "creator_user_ref_id": "creator", "batch_id": "batch", "batch_name": "周会",
           "source_text": "整段周会含其他人的任务", "items": [{"private": True}]}
    values = project("task", raw, FIELDS)
    assert values["owner_id"] == "new" and values["original_owner_id"] == "old"
    assert values["owner_name"] == "接手人" and values["creator_id"] == "creator"
    assert values["batch_name"] == "周会"
    assert "source_text" not in values and "items" not in values
    assert RELATIONS["original_owner_id"] == RELATIONS["owner_id"] == "member"


def test_existing_tasks_work_without_batch_and_time_dependent_overdue_is_not_exported():
    values = project("task", {"id": "fixture", "status": "completed"}, FIELDS)
    assert values["batch_id"] is None and values["original_owner_id"] is None
    assert values["completed_count"] == 1
    assert "overdue_count" not in FIELDS
    assert "completed_count" not in project("task", {"id": "fixture"}, {"title"})


def test_excluded_or_synthetic_tasks_never_export_batch_information():
    raw = {"id": "fixture", "batch_name": "private", "status": "completed"}
    assert project("task", {**raw, "excluded": True}, FIELDS) == {
        "system_id": "fixture", "record_status": "已归档"}
    assert project("task", {**raw, "data_kind": "synthetic"}, FIELDS) is None
    assert project("task", {**raw, "deleted_at": "2026-09-28"}, FIELDS)["active_count"] == 0


def test_task_mapping_allows_reporting_and_explicit_group_notifications_but_not_source_text():
    payload = {"workspace_id": str(UUID(int=1)), "connection_id": str(UUID(int=2)), "revision": 1,
               "app_id": "cli_test", "credential_ref": str(UUID(int=3)), "base_token": "testBase",
               "mappings": {"task": {"table_id": "tblTasks", "id_field_id": "fldSystemId",
                                      "fields": {key: "fld" + str(i)
                                                 for i, key in enumerate(sorted(FIELDS - {"system_id"}))}}}}
    assert SyncConfig.model_validate(payload).direction == "system_to_base"
    payload["mappings"]["task"]["fields"]["source_text"] = "fldSourceText"
    with pytest.raises(ValidationError):
        SyncConfig.model_validate(payload)
    del payload["mappings"]["task"]["fields"]["source_text"]
    payload["notification"] = {"on_create": ["task"]}
    assert SyncConfig.model_validate(payload).notification.on_create == {"task"}
