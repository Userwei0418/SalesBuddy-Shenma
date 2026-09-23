from sales_contracts.domain.model_contract import ModelContractError

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
