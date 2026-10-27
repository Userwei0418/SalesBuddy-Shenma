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
        if column not in row:
            continue
        value = row[column]
        if value is None or value == "" or value == {}:
            # An explicitly cleared column is authoritative too. Import evidence
            # remains stored, but must not resurrect the previous current value.
            leftover.pop(attr_key, None)
            continue
        leftover[attr_key] = _json_ready(value)
    row["attributes"] = leftover
    return row


def overlay_customer_attributes(row: dict[str, Any]) -> dict[str, Any]:
    """正式列优先；已清空的列移除兼容键，未选取的列保留原兼容值。"""
    if "customer" not in row.get("business_identities", ["customer"]):
        row["assessment_status"] = "not_applicable"
        row["customer_analysis_applicable"] = False
        for key in ("potential_score", "relationship_score", "quadrant_code", "quadrant_policy"):
            if key in row: row[key] = None
    else:
        row["customer_analysis_applicable"] = True
    return _project(row, CUSTOMER_ATTRIBUTE_COLUMNS)


def overlay_task_attributes(row: dict[str, Any]) -> dict[str, Any]:
    overlay = _project(row, TASK_ATTRIBUTE_COLUMNS)
    if "assignees" not in overlay:
        return overlay
    assignees = overlay.get("assignees") or []
    owner = next((person for person in assignees if person.get("responsibility") == "owner"), None)
    account = (owner or {}).get("account_code") if owner else None
    if account:
        overlay["attributes"]["assignee_account_code"] = account
    else:
        overlay["attributes"].pop("assignee_account_code", None)
    return overlay


def overlay_risk_attributes(row: dict[str, Any]) -> dict[str, Any]:
    return _project(row, RISK_ATTRIBUTE_COLUMNS)


def overlay_visit_attributes(row: dict[str, Any]) -> dict[str, Any]:
    return _project(row, VISIT_ATTRIBUTE_COLUMNS)


def overlay_opportunity_attributes(row: dict[str, Any]) -> dict[str, Any]:
    return _project(row, {})
