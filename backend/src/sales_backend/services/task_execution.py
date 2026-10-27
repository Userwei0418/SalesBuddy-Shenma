"""Transaction boundary belongs to the authenticated caller; PostgreSQL owns invariants."""

import base64
import json
from datetime import datetime
from uuid import UUID

import asyncpg

from sales_backend.domain.task_outcome import task_end_reason
from sales_backend.repositories.task_execution import TaskExecutionRepository


class TaskExecutionError(RuntimeError):
    def __init__(self, code, status):
        super().__init__(code)
        self.code, self.status = code, status


async def read_execution(connection, task_id):
    result = _normalize_execution(await _call(TaskExecutionRepository().read(connection, task_id)))
    if isinstance(result, dict):
        if result.get("execution_mode") in {"legacy", "execution_v1"}:
            # The V285 summary/adoption projection supplies the authoritative
            # checklist baseline. Do not alter normal V1 command/receipt bodies.
            result["initialize_versions"] = {
                field: result[field]
                for field in ("version_no", "requirements_version", "responsibility_version", "checklist_revision")
            }
        for kind in ("events", "submissions"):
            rows = await _call(TaskExecutionRepository().history(connection, task_id, kind, None, None, 51))
            page = _history_page(task_id, kind, rows, 50)
            result[kind] = page["items"]
            result[kind + "_next_cursor"] = page["next_cursor"]
            result[kind + "_has_more"] = page["next_cursor"] is not None
    return result


def _normalize_event(item):
    item = dict(item)
    item["actor_user_id"] = item.get("actor_user_id") or item.get("actor_user_ref_id")
    for field in ("body", "payload", "snapshot"):
        if isinstance(item.get(field), str):
            item[field] = json.loads(item[field])
    return item


def _normalize_execution(result):
    if not isinstance(result, dict):
        return result
    result = dict(result)
    if "status" in result:
        result["end_reason"] = task_end_reason(result)
        detail = result.get("completion_detail") or {}
        result["ended_at"] = detail.get("occurred_at") if result.get("status") == "completed" and isinstance(detail, dict) else None
    for key in ("events", "submissions"):
        if key in result:
            result[key] = [_normalize_event(item) for item in result[key]]
    return result


async def read_history(connection, task_id, kind, cursor, limit):
    before_at = before_id = None
    if cursor:
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if value["task_id"] != task_id or value["kind"] != kind:
                raise ValueError("cursor scope mismatch")
            before_at = datetime.fromisoformat(value["at"])
            before_id = str(UUID(value["id"]))
            if before_at.tzinfo is None:
                raise ValueError("cursor requires timezone")
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise TaskExecutionError("TASK_EXECUTION_CURSOR_INVALID", 422) from exc
    # Check visibility even for an empty history; an ID is never an authorization grant.
    if not await _call(TaskExecutionRepository().visible(connection, task_id)):
        raise TaskExecutionError("TASK_NOT_FOUND", 404)
    rows = await _call(TaskExecutionRepository().history(connection, task_id, kind, before_at, before_id, limit + 1))
    return _history_page(task_id, kind, rows, limit)


def _history_page(task_id, kind, rows, limit):
    items = [_normalize_event(row) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        at = last["occurred_at" if kind == "events" else "created_at"]
        payload = {"task_id": str(task_id), "kind": kind, "id": str(last["id"]),
                   "at": at.isoformat() if isinstance(at, datetime) else at}
        next_cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return {"items": items, "next_cursor": next_cursor}


async def execute_command(connection, *, task_id, action, body, key):
    if key is None:
        raise TaskExecutionError("TASK_EXECUTION_IDEMPOTENCY_KEY_REQUIRED", 422)
    # Preserve the receipt body used before optional contribution support.
    if action == "submit_completion" and body.get("actual_contribution") is None:
        body = {field: value for field, value in body.items() if field != "actual_contribution"}
    result = await _call(TaskExecutionRepository().command(connection, task_id, action, body, key))
    if isinstance(result, dict) and "execution" in result:
        result = {**result, "execution": _normalize_execution(result["execution"])}
    return result


async def initialize_execution(connection, task_id, checklist, *, protocol_version=1, confirmed_self=False):
    return await _call(TaskExecutionRepository().initialize(
        connection, task_id, checklist, protocol_version=protocol_version, confirmed_self=confirmed_self))


async def execute_command_v3(connection, *, task_id, action, body, key):
    if key is None:
        raise TaskExecutionError("TASK_EXECUTION_IDEMPOTENCY_KEY_REQUIRED", 422)
    result = await _call(TaskExecutionRepository().command_v3(connection, task_id, action, body, key))
    if isinstance(result, dict) and "execution" in result:
        result = {**result, "execution": _normalize_execution(result["execution"])}
    return result


async def _call(operation):
    try:
        return await operation
    except asyncpg.PostgresError as exc:
        mapping = {"42501": 403, "P0002": 404, "40001": 409, "22023": 422, "23505": 409, "23514": 422}
        if exc.sqlstate not in mapping:
            raise
        message = str(exc).splitlines()[0]
        # Only explicit domain exceptions are actionable task conflicts. A missing
        # database privilege/schema is an infrastructure error for global logging.
        if not message.startswith("TASK_"):
            raise
        raise TaskExecutionError(message, mapping[exc.sqlstate]) from exc


async def list_candidates(connection, task_id, purpose, query, limit, offset):
    result = await _call(TaskExecutionRepository().candidates(connection, task_id, purpose, query, limit, offset))
    if isinstance(result, dict):
        next_offset = offset + len(result.get("items") or [])
        more = next_offset < int(result.get("total") or 0)
        result = {**result, "has_more": more, "next_offset": next_offset if more else None}
    return result
