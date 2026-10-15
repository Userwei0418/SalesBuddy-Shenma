"""Public frozen FDE metadata and quote redaction; never recalculate history."""

from copy import deepcopy

from sales_backend.domain.fde_profile import CALCULATION_VERSION

UNAVAILABLE = "当前无法查看该建议依据"


def score_basis(snapshot):
    value = snapshot.get("score_basis") or {}
    if (value.get("snapshot_status") == "frozen"
            and value.get("calculation_version") == CALCULATION_VERSION
            and isinstance(value.get("basis_id"), str) and value["basis_id"]):
        return {key: value[key] for key in ("basis_id", "calculation_version", "snapshot_status")}
    return {"basis_id": None, "calculation_version": None, "snapshot_status": "legacy_unknown"}


def coaching_coverage(snapshot):
    value = snapshot.get("coaching_coverage") or {}
    keys = ("selected_visits", "selected_tasks", "truncated_text_sources")
    known = value.get("status") == "known" and all(type(value.get(k)) is int and value[k] >= 0 for k in keys)
    return {"status": "known" if known else "legacy_unknown", "visit_limit": 12, "task_limit": 20,
            **{key: value[key] if known else None for key in keys}}


def evidence_coverage(snapshot):
    value = snapshot.get("evidence_coverage")
    if not isinstance(value, dict):
        return None
    keys = ("archived_visits", "readable_archived_visits", "assigned_projects", "assigned_customers", "owned_tasks")
    return {key: value.get(key) if type(value.get(key)) is int and value[key] >= 0 else None for key in keys}


def advice(items, readable_refs):
    """A multi-source sentence is disclosed only when every source remains readable."""
    readable = set(readable_refs)
    result = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        refs = item.get("source_refs")
        visible = (isinstance(refs, list) and 1 <= len(refs) <= 3
                   and all(isinstance(ref, str) and ref in readable for ref in refs))
        result.append({"title": str(item.get("title", "")) if visible else UNAVAILABLE,
                       "content": str(item.get("detail", item.get("content", ""))) if visible else UNAVAILABLE,
                       "source_refs": list(refs) if visible else []})
    return result


def dimensions(snapshot):
    result = []
    keys = ("code", "name", "short_name", "score", "assessment", "coaching_action", "evidence_count",
            "numerator", "denominator", "percent")
    for row in snapshot.get("dimensions", []):
        result.append({key: deepcopy(row[key]) for key in keys if key in row})
    return result


def history_point(row):
    snapshot = row.get("profile") or {}
    return {"date": row["reviewed_at"], "run_id": str(row["run_id"]), "reviewed_at": row["reviewed_at"],
            "dimensions": dimensions(snapshot), "overall_score": None, "score_basis": score_basis(snapshot),
            "evidence_coverage": evidence_coverage(snapshot), "coaching_coverage": coaching_coverage(snapshot)}


def detail(row, *, can_review=False):
    snapshot = row.get("profile") or {}
    evidence = []
    for source in row.get("evidence") or []:
        visible = source.get("readable") is True
        evidence.append({"source_type": source["source_type"], "readable": visible,
                         **{key: source.get(key) if visible else None
                            for key in ("source_id", "source_ref", "source_version", "detail")}})
    refs = [source["source_ref"] for source in evidence if source["readable"]]
    point = history_point(row)
    return {"contract_version": "fde.profile.v1", "assessment_kind": "fde_factual_portrait",
            "subject": {"id": str(row["subject_id"]), "name": row.get("subject_name"),
                        "account_code": row.get("account_code")},
            "review": {**point, "period": deepcopy(snapshot.get("period")),
                       "sample_count": snapshot.get("sample_count"), "as_of": snapshot.get("data_as_of"),
                       "advice": advice(row.get("advice"), refs)},
            "evidence": evidence,
            "evidence_summary": {"total": len(evidence), "readable": len(refs),
                                 "unavailable": len(evidence) - len(refs)},
            "can_review": can_review}
