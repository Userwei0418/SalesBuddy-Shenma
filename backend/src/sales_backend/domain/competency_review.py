"""Sales competency output contract and server-owned score projection.

Both providers must supply complete, attributable dimensions. Missing or invalid
model output is rejected, never converted into a successful zero-score review.
"""

import math
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sales_backend.domain.model_contract import ModelContractError

CONTRACT_VERSION = "sales.competency.v2"
WINDOW_DAYS = 30
VISIT_LIMIT = 80


class CompetencyContractError(ModelContractError):
    """A fixed diagnostic code; never includes model or customer text."""


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def review_window(review_date):
    """Thirty Beijing calendar days, including the review date, end exclusive."""
    zone = ZoneInfo("Asia/Shanghai")
    start = datetime.combine(review_date - timedelta(days=WINDOW_DAYS - 1), time.min, zone)
    end = datetime.combine(review_date + timedelta(days=1), time.min, zone)
    return start, end


def framework_definitions(framework):
    if not isinstance(framework, dict):
        raise ValueError("competency_framework.object")
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
        if not _finite_number(weight) or weight < 0:
            raise ValueError("competency_framework.weight")
        result[item["code"]] = item
    total_weight = sum(item["weight"] for item in result.values())
    if not _finite_number(total_weight) or total_weight <= 0:
        raise ValueError("competency_framework.total_weight")
    minimum_visit_count(framework)
    return result


def minimum_visit_count(framework):
    """Use only this generation's frozen rule; never infer a new threshold."""
    rules = framework.get("scoring_rules", {})
    if not isinstance(rules, dict):
        raise ValueError("competency_framework.scoring_rules")
    minimum = rules.get("minimum_visit_count", 1)
    if type(minimum) is not int or not 1 <= minimum <= VISIT_LIMIT:
        raise ValueError("competency_framework.minimum_visit_count")
    return minimum


def _visit_ids(facts):
    visits = facts.get("visits") if isinstance(facts, dict) else None
    if not isinstance(visits, list) or len(visits) > VISIT_LIMIT:
        raise ValueError("competency_facts.visits")
    ids = set()
    for visit in visits:
        visit_id = visit.get("visit_id") if isinstance(visit, dict) else None
        if not isinstance(visit_id, str) or not visit_id.strip() or visit_id in ids:
            raise ValueError("competency_facts.visit_id")
        ids.add(visit_id)
    return ids


def has_minimum_evidence(framework, facts):
    """Preflight for the handler: total eligible but unread visits do not count."""
    framework_definitions(framework)
    return len(_visit_ids(facts)) >= minimum_visit_count(framework)


def insufficient_competency_result(framework, reason_code="insufficient_evidence"):
    """Build a v2 no-score result without calling a model or judging a person."""
    definitions = framework_definitions(framework)
    summaries = {
        "insufficient_evidence": "当前材料不足以完成六维能力评估，暂不提供评分。",
        "no_visits": "本次没有可用于评估的正式跟进材料，暂不提供评分。",
        "below_minimum_visit_count": "本次实际纳入的材料未达到冻结框架规定的最低数量，暂不提供评分。",
    }
    if not isinstance(reason_code, str) or reason_code not in summaries:
        raise ValueError("competency_review.insufficient_reason")
    dimensions = [{
        "code": code, "evaluation_state": "insufficient_data", "score": None,
        "assessment": "当前材料不足，无法判断该维度表现。",
        "coaching_action": f"补充与{definition['name']}有关的正式跟进事实及结果，再进行评估。",
        "evidence": [],
    } for code, definition in definitions.items()]
    return {
        "contract_version": CONTRACT_VERSION, "evaluation_state": "insufficient_data",
        "summary": summaries[reason_code], "strengths": [],
        "improvements": [f"{definitions[item['code']]['name']}：{item['coaching_action']}" for item in dimensions],
        "dimensions": dimensions,
    }


def _text(value, code, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise CompetencyContractError("competency_review." + code)
    return value.strip()


def validate_competency_result(result, framework, facts):
    definitions = framework_definitions(framework)
    if not isinstance(result, dict):
        raise CompetencyContractError("competency_review.object")
    if result.get("contract_version") != CONTRACT_VERSION:
        raise CompetencyContractError("competency_review.contract_version")
    dimensions = result.get("dimensions")
    if not isinstance(dimensions, list) or len(dimensions) != len(definitions):
        raise CompetencyContractError("competency_review.dimension_count")
    valid_visits = _visit_ids(facts)
    enough_visits = len(valid_visits) >= minimum_visit_count(framework)
    normalized = {}
    for item in dimensions:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str):
            raise CompetencyContractError("competency_review.dimension")
        code = item["code"]
        if code not in definitions or code in normalized:
            raise CompetencyContractError("competency_review.dimension_code")
        state = item.get("evaluation_state")
        if state not in ("assessed", "insufficient_data"):
            raise CompetencyContractError("competency_review.dimension_state")
        score = item.get("score")
        if state == "assessed" and (
            not _finite_number(score) or not 0 <= score <= 100
        ):
            raise CompetencyContractError("competency_review.score")
        if state == "insufficient_data" and ("score" not in item or score is not None):
            raise CompetencyContractError("competency_review.insufficient_score")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or len(evidence) > len(valid_visits):
            raise CompetencyContractError("competency_review.evidence")
        if state == "assessed" and (not evidence or not enough_visits):
            raise CompetencyContractError("competency_review.evidence_required")
        if state == "insufficient_data" and evidence:
            raise CompetencyContractError("competency_review.insufficient_evidence")
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
            "evaluation_state": state,
            "score": float(score) if score is not None else None,
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
    assessed = sum(item["evaluation_state"] == "assessed" for item in normalized.values())
    state = "assessed" if assessed == len(definitions) else "partially_assessed" if assessed else "insufficient_data"
    if result.get("evaluation_state") != state:
        raise CompetencyContractError("competency_review.evaluation_state")
    if state == "insufficient_data" and strengths:
        raise CompetencyContractError("competency_review.insufficient_strengths")
    return {
        "contract_version": CONTRACT_VERSION,
        "evaluation_state": state,
        "summary": _text(result.get("summary"), "summary", 2000),
        "strengths": [_text(value, "strength", 500) for value in strengths],
        "improvements": [_text(value, "improvement", 1000) for value in improvements],
        "dimensions": [normalized[code] for code in definitions],
    }


def scored_review(result, framework, facts):
    """Project a validated answer using the persisted framework, never model weights."""
    result = validate_competency_result(result, framework, facts)
    definitions = framework_definitions(framework)
    dimensions, evidence = {}, {}
    weighted_score = 0.0
    complete = True
    total_weight = sum(definition["weight"] for definition in definitions.values())
    for item in result["dimensions"]:
        code = item["code"]
        definition = definitions[code]
        weight = float(definition["weight"])
        if weight > 0:
            if item["evaluation_state"] == "assessed":
                weighted_score += item["score"] * (weight / total_weight)
            else:
                complete = False
        dimensions[code] = {
            "name": definition["name"],
            "short_name": definition.get("short_name") or definition["name"],
            "evaluation_state": item["evaluation_state"],
            "score": round(item["score"], 1) if item["score"] is not None else None,
            "weight": weight,
            "assessment": item["assessment"],
            "coaching_action": item["coaching_action"],
        }
        evidence[code] = item["evidence"]
    return {
        "contract_version": CONTRACT_VERSION,
        "evaluation_state": result["evaluation_state"],
        "overall_score": round(weighted_score, 2) if complete else None,
        "dimension_scores": dimensions,
        "summary": result["summary"],
        "strengths": result["strengths"],
        # Canonical names/order and per-dimension actions are authoritative for UI.
        "improvements": [f"{item['name']}：{item['coaching_action']}" for item in dimensions.values()],
        "evidence": evidence,
    }
