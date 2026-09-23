"""Bounded, attributable collaboration material; independent of radar metrics."""

from sales_backend.domain.model_contract import ModelContractError
from sales_backend.domain.model_contract import contract_failure_details as contract_failure_details

COACHING_CONTRACT_VERSION = "fde.coaching.v1"
VISIT_LIMIT = 12
TASK_LIMIT = 20
TEXT_LIMIT = 1600
PLAN_LIMIT = 800
NAME_LIMIT = 160


def _text(value, limit):
    return str(value or "").strip()[:limit]


def coaching_inputs(visits, tasks):
    """Rows are already self-scoped and RLS-filtered, in deterministic priority order."""
    sources = []
    for row in visits[:VISIT_LIMIT]:
        communication = _text(row.get("communication"), TEXT_LIMIT)
        next_action = _text(row.get("next_action"), PLAN_LIMIT)
        if not (communication or next_action):
            continue
        sources.append(
            {
                "source_ref": f"visit:{row['id']}",
                "source_type": "visit",
                "project_name": _text(row.get("project_name"), NAME_LIMIT),
                "recorded_at": row["interaction_at"].isoformat(),
                "communication": communication,
                "next_action": next_action,
                "truncated": len(str(row.get("communication") or "").strip()) > TEXT_LIMIT
                or len(str(row.get("next_action") or "").strip()) > PLAN_LIMIT,
            }
        )
    for row in [task for task in tasks if task["can_execute"]][:TASK_LIMIT]:
        sources.append(
            {
                "source_ref": f"task:{row['id']}",
                "source_type": "task",
                "title": _text(row.get("title"), NAME_LIMIT),
                "project_name": _text(row.get("project_name"), NAME_LIMIT),
                "status": row["status"],
                "due_at": row["due_at"].isoformat() if row["due_at"] else None,
                "can_execute": row["can_execute"],
            }
        )
    return {"contract_version": COACHING_CONTRACT_VERSION, "sources": sources}


def model_facts(profile):
    """Never send display ratios, record grades or statistical assessments to a coach."""
    return {
        key: profile[key]
        for key in (
            "data_source",
            "data_as_of",
            "period",
            "scope",
            "permission_version",
            "facts_fingerprint",
            "coaching_inputs",
        )
    }


class CoachingContractError(ModelContractError):
    """Only fixed error codes leave this validator; no model/user text is retained."""

def validate_coaching(result, facts):
    if not isinstance(result, dict) or not isinstance(result.get("action_plan"), list):
        raise CoachingContractError("fde_coaching.action_plan_missing")
    if len(result["action_plan"]) > 3:
        raise CoachingContractError("fde_coaching.too_many_actions")
    available = {source["source_ref"] for source in facts["coaching_inputs"]["sources"]}
    normalized = []
    for item in result["action_plan"]:
        if not isinstance(item, dict) or any(
            not isinstance(item.get(key), str) or not item[key].strip() or len(item[key]) > limit
            for key, limit in (("title", 80), ("detail", 800))
        ):
            raise CoachingContractError("fde_coaching.invalid_item")
        refs = item.get("source_refs")
        if (
            not isinstance(refs, list)
            or not 1 <= len(refs) <= 3
            or any(not isinstance(ref, str) for ref in refs)
            or len(set(refs)) != len(refs)
        ):
            raise CoachingContractError("fde_coaching.invalid_sources")
        if any(ref not in available for ref in refs):
            raise CoachingContractError("fde_coaching.unknown_source")
        normalized.append({"title": item["title"].strip(), "detail": item["detail"].strip(), "source_refs": refs})
    return {
        "title": "FDE本人协作建议",
        "period": f"近{facts['period']['days']}天",
        "scope": "本人",
        "summary": "",
        "action_plan": normalized,
    }
