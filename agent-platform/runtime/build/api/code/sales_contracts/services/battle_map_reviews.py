from __future__ import annotations

import math

from typing import Any

from sales_contracts.domain.company_rules import QuadrantPolicy

def _changed_scoring(policy):
    defaults = QuadrantPolicy().model_dump()
    return any(policy["definition"][key] != defaults[key] for key in (
        "potential_guidance", "relationship_guidance", "calibration_examples"))

class BattleMapReviewHandler:
    @staticmethod
    def _normalize_result(result: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
        policy = facts.get("company_policy")
        if policy and _changed_scoring(policy) and result.get("company_policy_id") != policy["id"]:
            raise ValueError("company scoring policy receipt missing or mismatched")
        try:
            scores = [result["potential_score"], result["relationship_score"]]
            if any(isinstance(value, bool) or not math.isfinite(float(value)) for value in scores):
                raise ValueError("scores must be finite numbers")
            potential, relationship = [round(max(0, min(100, float(value))), 1) for value in scores]
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("battle-map Agent returned invalid scores") from exc
    
        valid_ids = {
            "visit": {item["id"] for item in facts["visits"]},
            "opportunity": {item["id"] for item in facts["opportunities"]},
            "contact": {item["id"] for item in facts["contacts"]},
        }
        evidence = []
        raw_evidence = result.get("evidence", [])
        if not isinstance(raw_evidence, list):
            raise ValueError("battle-map Agent returned invalid evidence")
        for item in raw_evidence:
            if not isinstance(item, dict):
                continue
            source_type = str(item.get("source_type") or "")
            source_id = str(item.get("source_id") or "")
            if source_id not in valid_ids.get(source_type, set()):
                continue
            evidence.append(
                {
                    "source_type": source_type,
                    "source_id": source_id,
                    "detail": str(item.get("detail") or "")[:1000],
                }
            )
        return {
            "potential_score": potential,
            "relationship_score": relationship,
            "summary": str(result.get("summary") or "已按最新业务事实完成重评。")[:1000],
            "rationale": result.get("rationale") if isinstance(result.get("rationale"), dict) else {},
            "evidence": evidence,
            "evaluation_mode": "agent",
        }
