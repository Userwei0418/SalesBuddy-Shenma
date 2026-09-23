"""The executable battle-map output contract shared by both inference providers."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sales_backend.domain.model_contract import ModelContractError


class StrictResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class BattleMapRationale(StrictResult):
    potential: str
    relationship: str


class BattleMapEvidence(StrictResult):
    source_type: Literal["visit", "opportunity", "contact"]
    source_id: str
    detail: str


class BattleMapResult(StrictResult):
    potential_score: float = Field(ge=0, le=100, allow_inf_nan=False)
    relationship_score: float = Field(ge=0, le=100, allow_inf_nan=False)
    summary: str
    rationale: BattleMapRationale
    evidence: list[BattleMapEvidence]


def validate_battle_map_result(value, facts, *, policy_id=None):
    # A custom company scoring policy adds a backend-specified receipt. It is
    # not an arbitrary extension point for fields supplied by the model.
    if policy_id is not None:
        if not isinstance(value, dict) or value.get("company_policy_id") != policy_id:
            raise ModelContractError("作战地图评分政策回执缺失或与本次要求不一致")
        value = {key: item for key, item in value.items() if key != "company_policy_id"}
    try:
        result = BattleMapResult.model_validate(value).model_dump()
    except ValidationError as exc:
        raise ModelContractError("作战地图结果字段、类型或分数范围不符合约定") from exc
    ids = {kind: {str(row["id"]) for row in facts[key]}
           for kind, key in (("visit", "visits"), ("opportunity", "opportunities"), ("contact", "contacts"))}
    for item in result["evidence"]:
        if item["source_id"] not in ids[item["source_type"]]:
            raise ModelContractError("作战地图引用了本次资料之外的证据")
    return result
