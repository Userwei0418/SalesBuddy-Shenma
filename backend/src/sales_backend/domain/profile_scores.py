"""Deterministic, versioned score calculation. Missing evidence is never a zero."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScoreWeights(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1

    @model_validator(mode="after")
    def weights_total(self):
        weights = self.model_dump(exclude={"schema_version"})
        if sum(weights.values()) != 100:
            raise ValueError("总分各项权重相加必须等于 100，停用某项可设为 0")
        return self


class MaturityWeights(ScoreWeights):
    collection: int = Field(40, ge=0, le=100)
    recognized: int = Field(40, ge=0, le=100)
    retention: int = Field(20, ge=0, le=100)


class EfficiencyWeights(ScoreWeights):
    followup: int = Field(40, ge=0, le=100)
    customers: int = Field(30, ge=0, le=100)
    opportunities: int = Field(30, ge=0, le=100)


class CompetencyWeights(ScoreWeights):
    needs_discovery: int = Field(18, ge=0, le=100)
    stakeholder_navigation: int = Field(16, ge=0, le=100)
    solution_communication: int = Field(18, ge=0, le=100)
    opportunity_advancement: int = Field(20, ge=0, le=100)
    relationship_management: int = Field(14, ge=0, le=100)
    followup_discipline: int = Field(14, ge=0, le=100)


SCORE_MODELS = {
    "score.maturity": MaturityWeights,
    "score.efficiency": EfficiencyWeights,
    "score.competency": CompetencyWeights,
}
SCORE_LABELS = {
    "score.maturity": "营销成熟度总分权重",
    "score.efficiency": "营销效率总分权重",
    "score.competency": "销售画像总分权重",
}
DIMENSION_LABELS = {
    "collection": "回款达成率",
    "recognized": "确收达成率",
    "retention": "客户保有率",
    "followup": "跟进记录平均分",
    "customers": "第一、二象限客户占比",
    "opportunities": "A+B 类商机占比",
    "needs_discovery": "需求洞察",
    "stakeholder_navigation": "决策链经营",
    "solution_communication": "方案沟通",
    "opportunity_advancement": "商机推进",
    "relationship_management": "客户关系",
    "followup_discipline": "跟进执行",
}


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def attainment(actual, target):
    actual, target = number(actual), number(target)
    return actual / target * 100 if actual is not None and target is not None and target > 0 else None


def weighted_score(values, snapshot):
    weights = SCORE_MODELS[snapshot["code"]](**snapshot["definition"]).model_dump(exclude={"schema_version"})
    inputs = []
    for code, weight in weights.items():
        raw = number(values.get(code))
        value = max(Decimal(0), min(Decimal(100), raw)) if raw is not None else None
        inputs.append(
            {"code": code, "name": DIMENSION_LABELS[code], "raw_value": raw, "value": value, "weight": weight}
        )
    valid = [item for item in inputs if item["value"] is not None and item["weight"] > 0]
    available_weight = sum(item["weight"] for item in valid)
    score = sum(item["value"] * item["weight"] for item in valid) / available_weight if available_weight else None
    explanation = "\n".join(
        f"{i['name']}："
        f"{'暂无数据' if i['value'] is None else str(i['value'].quantize(Decimal('0.1'))) + '分'}"
        f"（权重 {i['weight']}）"
        for i in inputs
    )
    return {
        "value": score,
        "text": str(score.quantize(Decimal(1), rounding=ROUND_HALF_UP)) if score is not None else "--",
        "count": len(valid),
        "total": len([w for w in weights.values() if w > 0]),
        "coverage_percent": available_weight,
        "inputs": inputs,
        "explanation": explanation,
        "rule": snapshot,
        "calculation": "clamp_each_0_100_then_normalize_available_weight_v1",
    }
