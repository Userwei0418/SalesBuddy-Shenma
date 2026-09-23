import math

from sales_contracts.domain.model_contract import ModelContractError

class CompetencyContractError(ModelContractError):
    """A fixed diagnostic code; never includes model or customer text."""

def framework_definitions(framework):
    definitions = framework.get("dimensions")
    if not isinstance(definitions, list) or len(definitions) != 6:
        raise ValueError("competency_framework.dimensions")
    result = {}
    for item in definitions:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("code"), str)
            or not item["code"].strip()
            or item["code"] in result
            or not isinstance(item.get("name"), str)
            or not item["name"].strip()
        ):
            raise ValueError("competency_framework.definition")
        weight = item.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight < 0:
            raise ValueError("competency_framework.weight")
        result[item["code"]] = item
    total_weight = sum(item["weight"] for item in result.values())
    if not math.isfinite(total_weight) or total_weight <= 0:
        raise ValueError("competency_framework.total_weight")
    return result

def _text(value, code, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise CompetencyContractError("competency_review." + code)
    return value.strip()

def validate_competency_result(result, framework, facts):
    definitions = framework_definitions(framework)
    if not isinstance(result, dict):
        raise CompetencyContractError("competency_review.object")
    dimensions = result.get("dimensions")
    if not isinstance(dimensions, list) or len(dimensions) != len(definitions):
        raise CompetencyContractError("competency_review.dimension_count")
    valid_visits = {row["visit_id"] for row in facts["visits"]}
    normalized = {}
    for item in dimensions:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str):
            raise CompetencyContractError("competency_review.dimension")
        code = item["code"]
        if code not in definitions or code in normalized:
            raise CompetencyContractError("competency_review.dimension_code")
        score = item.get("score")
        if (
            isinstance(score, bool) or not isinstance(score, (int, float))
            or not math.isfinite(score) or not 0 <= score <= 100
        ):
            raise CompetencyContractError("competency_review.score")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or len(evidence) > len(valid_visits):
            raise CompetencyContractError("competency_review.evidence")
        accepted, used = [], set()
        for source in evidence:
            if not isinstance(source, dict) or not isinstance(source.get("visit_id"), str):
                raise CompetencyContractError("competency_review.evidence_item")
            visit_id = source["visit_id"]
            if visit_id not in valid_visits or visit_id in used:
                raise CompetencyContractError("competency_review.evidence_source")
            used.add(visit_id)
            accepted.append({"visit_id": visit_id, "detail": _text(source.get("detail"), "evidence_detail", 1000)})
        normalized[code] = {
            "code": code,
            "score": float(score),
            "assessment": _text(item.get("assessment"), "assessment", 1000),
            "coaching_action": _text(item.get("coaching_action"), "coaching_action", 1000),
            "evidence": accepted,
        }
    strengths = result.get("strengths")
    improvements = result.get("improvements")
    if not isinstance(strengths, list) or len(strengths) > 5:
        raise CompetencyContractError("competency_review.strengths")
    if not isinstance(improvements, list) or len(improvements) != len(definitions):
        raise CompetencyContractError("competency_review.improvements")
    return {
        "summary": _text(result.get("summary"), "summary", 2000),
        "strengths": [_text(value, "strength", 500) for value in strengths],
        "improvements": [_text(value, "improvement", 1000) for value in improvements],
        "dimensions": [normalized[code] for code in definitions],
    }
