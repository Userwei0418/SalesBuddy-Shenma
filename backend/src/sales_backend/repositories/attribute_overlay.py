"""把已提升为正式列 / import_meta 的字段，再投影回 attributes，供前端继续按原路径读取。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

CUSTOMER_ATTRIBUTE_COLUMNS = (
    "next_action",
    "operation_type",
    "cooperation_years",
    "main_business",
    "customer_budget",
)

TASK_ATTRIBUTE_COLUMNS = {
    "source": "source_code",
    "agent_reason": "agent_reason",
    "follow_up_record": "source_follow_up_record",
    "interaction_at": "source_interaction_at",
    "rejection_comment": "rejection_comment",
}

RISK_ATTRIBUTE_COLUMNS = {
    "source": "source_code",
    "suggested_action": "suggested_action",
    "agent_key": "agent_key",
    "run_id": "source_run_id",
}

VISIT_ATTRIBUTE_COLUMNS = {
    "quality_review": "quality_review",
    "first_visit_profile": "first_visit_profile",
    "is_first_visit": "is_first_visit",
    "follow_up_score": "follow_up_score",
}


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _project(row: dict[str, Any], columns: dict[str, str] | tuple[str, ...]) -> dict[str, Any]:
    leftover = dict(row.get("attributes") or {})
    leftover.update(dict(row.get("import_meta") or {}))
    mapping = (
        {name: name for name in columns} if isinstance(columns, tuple) else columns
    )
    for attr_key, column in mapping.items():
        value = row.get(column)
        if value is None or value == "" or value == {}:
            continue
        leftover[attr_key] = _json_ready(value)
    row["attributes"] = leftover
    return row


def overlay_customer_attributes(row: dict[str, Any]) -> dict[str, Any]:
    """列上的值覆盖 jsonb 同名键；空值不写入，避免把「未填」显示成空字符串。"""
    return _project(row, CUSTOMER_ATTRIBUTE_COLUMNS)


def overlay_task_attributes(row: dict[str, Any]) -> dict[str, Any]:
    overlay = _project(row, TASK_ATTRIBUTE_COLUMNS)
    assignees = overlay.get("assignees") or []
    owner = next((person for person in assignees if person.get("responsibility") == "owner"), None)
    account = (owner or {}).get("account_code") if owner else None
    if account and "assignee_account_code" not in overlay["attributes"]:
        overlay["attributes"]["assignee_account_code"] = account
    return overlay


def overlay_risk_attributes(row: dict[str, Any]) -> dict[str, Any]:
    return _project(row, RISK_ATTRIBUTE_COLUMNS)


def overlay_visit_attributes(row: dict[str, Any]) -> dict[str, Any]:
    return _project(row, VISIT_ATTRIBUTE_COLUMNS)


def overlay_opportunity_attributes(row: dict[str, Any]) -> dict[str, Any]:
    return _project(row, {})
