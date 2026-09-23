"""Explicit source-to-business projection. No caller-supplied expressions or SQL."""
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

ALIASES = {
    "customer": {"industry": "industry_code", "customer_type": "customer_type_code", "priority": "level_code",
                 "needs": "demand_summary", "budget": "customer_budget", "owner_id": "owner_user_ref_id",
                 "department_id": "owner_team_id"},
    "opportunity": {"stage": "stage_code"},
    "visit": {"content": "follow_up_record", "recorder_id": "recorder_user_ref_id",
              "contact_name": "contact_name_snapshot", "contact_title": "contact_title_snapshot",
              "interaction_mode": "interaction_mode_code", "contact_role": "contact_category_snapshot",
              "partner_name": "partner_name_snapshot"},
    "task": {"creator_id": "creator_user_ref_id", "review_note": "completion_review_note"},
    "demo_scene": {"creator_id": "created_by"},
    "forecast": {"updated_by": "updated_by_user_ref_id"},
    "actual": {"confirmed_by": "confirmed_by_user_ref_id"},
    "target": {"user_id": "user_ref_id"},
    "member": {"name": "display_name", "account": "account_code"},
    "contact": {"role": "relationship_role_code"},
}
RELATIONS = {"customer_ids": "customer", "associated_partner_ids": "partner",
             "customer_id": "customer", "opportunity_id": "opportunity", "partner_id": "partner",
             "opportunity_ids": "opportunity", "manager_user_ref_id": "member", "channel_manager_user_ref_id": "member",
             "owner_id": "member", "recorder_id": "member", "creator_id": "member",
             "updated_by": "member", "confirmed_by": "member", "user_id": "member"}


def project(kind, raw, field_names):
    if raw.get("excluded"):
        return {"system_id": str(raw["id"]), "record_status": "已归档"}
    if raw.get("data_kind") not in (None, "production"):
        return None
    if kind == "visit" and not raw.get("archived_at") and not raw.get("deleted"):
        return None
    status = "已归档" if raw.get("deleted_at") or raw.get("deleted") else "有效"
    if raw.get("voided_at"):
        status = "已作废"
    if raw.get("status") in {"inactive", "withdrawn"}:
        status = "已停用" if raw["status"] == "inactive" else "已撤回"
    values = {key: raw.get(ALIASES.get(kind, {}).get(key, key)) for key in field_names}
    if kind == "visit" and "partner_name" in field_names:
        # Historical visits can reference a formal partner without a legacy
        # free-text snapshot. Prefer that linked name, retaining old snapshots.
        values["partner_name"] = raw.get("partner_name") or raw.get("partner_name_snapshot")
    if kind == "opportunity":
        amount = raw.get("amount")
        values["grade"] = None if amount is None or float(amount) < 0 else next(
            grade for minimum, grade in ((1000000, "A"), (500000, "B"), (100000, "C"), (0, "D"))
            if float(amount) >= minimum)
    values["source_version"] = raw.get("version_no")
    values["synced_at"] = datetime.now(UTC).isoformat()
    values.update(system_id=str(raw["id"]), record_status=status,
                  source_created_at=raw.get("created_at"), source_updated_at=raw.get("updated_at"))
    return values


def date_milliseconds(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, datetime.min.time())
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return int(value.timestamp() * 1000)


def encode_scalar(value, field_type):
    if value is None:
        return None  # Explicit clear on remote update, not omission.
    if field_type == 5:
        return date_milliseconds(value)
    if field_type == 2:
        return float(value)
    if field_type == 7:
        if not isinstance(value, bool):
            raise ValueError("INVALID_BOOLEAN_VALUE")
        return value
    if field_type == 4:
        return list(value) if isinstance(value, (list, tuple)) else [str(value)]
    if field_type in {1, 3, 13}:
        return "、".join(str(v) for v in value) if isinstance(value, (list, tuple)) else str(value)
    raise ValueError("UNSUPPORTED_REMOTE_FIELD_TYPE")
