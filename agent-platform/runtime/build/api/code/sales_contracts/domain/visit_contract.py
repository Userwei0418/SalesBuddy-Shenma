from sales_contracts.contracts.visit_schema import (
    CONTACT_ROLES,
    CONTENT_KEYS,
    FIRST_VISIT_KEYS,
    TEXT_FIELDS,
)

from sales_contracts.domain.company_rules import VisitAdmissionPolicy, visit_semantics_changed

VISIT_FIELDS = TEXT_FIELDS  # compatibility name; canonical metadata lives in contracts/visit_schema.py

PROMPT_VERSION = "visit-v3-20260910.2"

COMPATIBLE_PROMPT_VERSIONS = {PROMPT_VERSION, "visit-v3-20260910.1", "visit-v2-20260910.1", "visit-v2-20260910.2"}

SERVER_FIELDS = frozenset({"customer_name", "customer_type", "created_date", "recorder_user_id"})

def is_first_visit(fields):
    return fields.get("is_first_visit") in (True, 1, "1", "true")

def ensure_visit_result(result, policy=None, *, server_fields=None):
    admission = VisitAdmissionPolicy(**((policy or {}).get("definition") or {}))
    if visit_semantics_changed(policy) and result.get("company_policy_id") != policy["id"]:
        raise ValueError("拜访审核未返回当前公司评分政策回执")
    fields = result.get("fields")
    quality = result.get("quality_review")
    if not isinstance(fields, dict) or not isinstance(quality, dict):
        raise ValueError("AI未返回完整拜访结构，请重试")
    score = quality.get("follow_up_score")
    if type(score) is not int or not 0 <= score <= 100:
        raise ValueError("AI总分格式不正确，请重新审核")
    next_action = quality.get("next_action")
    if not isinstance(next_action, dict) or type(next_action.get("passed")) is not bool:
        raise ValueError("AI未返回下一步审核结果")
    next_action = dict(next_action)
    for key in ("time_found", "goal_or_plan_found"):
        if key in next_action and type(next_action[key]) is not bool:
            raise ValueError("下一步时间和行动审核格式不正确")
        if visit_semantics_changed(policy) and key not in next_action:
            raise ValueError("下一步审核缺少时间或行动依据")
        if next_action.get(key) is False:
            next_action["passed"] = False
    clean = {}
    for key, label in VISIT_FIELDS.items():
        value = fields.get(key, fields.get(label, ""))
        clean[key] = str(value).strip() if isinstance(value, (str, int, float)) else ""
    if server_fields is not None:
        if (not isinstance(server_fields, dict) or set(server_fields) - SERVER_FIELDS
                or not {"customer_type", "created_date", "recorder_user_id"} <= set(server_fields)
                or any(not isinstance(value, str) for value in server_fields.values())):
            raise ValueError("拜访系统字段上下文不完整")
        # Only the fact loader supplies this keyword. The model's fields and
        # same-named envelope cannot override bound CRM data or actor identity.
        clean.update(server_fields)
    clean["is_first_visit"] = is_first_visit(fields)
    grade = admission.grade(score)
    suggestions = quality.get("suggestions")
    if not isinstance(suggestions, list):
        suggestions = []
    return {
        **{k: v for k, v in result.items() if k not in {"company_policy", "company_policy_id", "server_fields"}},
        **({"company_policy": policy} if policy else {}),
        "fields": clean,
        "prompt_version": PROMPT_VERSION,
        "missing_fields": [
            VISIT_FIELDS[k] for k in (*CONTENT_KEYS, *(FIRST_VISIT_KEYS if clean["is_first_visit"] else ()))
            if not clean[k]
        ],
        "quality_review": {
            "follow_up_score": score,
            "grade": grade,
            "suggestions": [str(s) for s in suggestions if isinstance(s, str)][:admission.suggestion_count],
            "next_action": next_action,
            **({"admission_policy": admission.model_dump(mode="json")} if policy else {}),
        },
    }
