"""Read-only task outcomes. Lifecycle status and historical records stay intact."""

import json


NORMAL_ACTIONS = frozenset(("submit_completion", "review_completion"))


def completion_action(row):
    execution = row.get("execution")
    if isinstance(execution, str):
        try:
            execution = json.loads(execution)
        except (ValueError, TypeError):
            execution = None
    execution = execution if isinstance(execution, dict) else {}
    detail = execution.get("completion_detail") or execution.get("completion_result")
    return (row.get("completion_action") or execution.get("completion_action")
            or (detail.get("action") if isinstance(detail, dict) else None))


def task_end_reason(row):
    status = row.get("status")
    if status == "cancelled":
        event = row.get("end_event_type", row.get("last_event_type"))
        event = event if isinstance(event, str) else None
        return "rejected" if event in {"reject", "decline", "decline_task"} else "cancelled" if event in {"cancel", "cancel_task"} else "unknown"
    if status != "completed":
        return None
    if row.get("execution_mode") != "execution_v3":
        return "completed"
    action = completion_action(row)
    return "stopped" if action == "close" else "completed" if isinstance(action, str) and action in NORMAL_ACTIONS else "unknown"


def normally_completed(row):
    return task_end_reason(row) == "completed"
