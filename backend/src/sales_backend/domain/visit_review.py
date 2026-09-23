"""Deterministic review text and content matching, independent of persistence."""

from sales_backend.contracts.visit_schema import FIRST_VISIT_KEYS, TEXT_FIELDS
from sales_backend.domain.visit_contract import COMPATIBLE_PROMPT_VERSIONS, CONTENT_KEYS, is_first_visit


def review_text(fields):
    lines = [
        "拜访审核 v2",
        f"拜访结果：{str(fields.get('follow_up_record', '')).strip()}",
        f"下一步行动计划：{str(fields.get('next_action', '')).strip()}",
    ]
    if is_first_visit(fields):
        lines += ["拜访类型：首次拜访"] + [
            f"{TEXT_FIELDS[k]}：{str(fields.get(k, '')).strip()}" for k in FIRST_VISIT_KEYS
        ]
    return "\n".join(lines)


def matches_reviewed_content(payload, trigger_text, fields):
    """An extraction scores its stored output; an explicit re-review scores its input."""
    if not all(str(fields.get(key) or "").strip() for key in CONTENT_KEYS):
        return False
    if trigger_text.startswith("拜访审核 v2\n"):
        return trigger_text == review_text(fields)
    # Only compatible versioned contracts can reuse extraction scores. Legacy 16-field
    # results still require a fresh review; client-provided score/snapshot is ignored.
    return payload.get("prompt_version") in COMPATIBLE_PROMPT_VERSIONS and review_text(
        payload.get("fields") or {}
    ) == review_text(fields)

