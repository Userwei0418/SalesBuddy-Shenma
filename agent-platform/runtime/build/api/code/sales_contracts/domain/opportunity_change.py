from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

class ChangeAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    color: Literal["green", "yellow", "red", "gray"]
    summary: str = Field(min_length=1, max_length=240)
    evidence_refs: list[str] = Field(min_length=1, max_length=8)

def validate_assessment(value, facts):
    result = ChangeAssessment.model_validate(value).model_dump()
    if not result["summary"].strip() or not set(result["evidence_refs"]) <= set(facts["evidence_refs"]):
        raise ValueError("变化评估必须引用本次提供的事实")
    return result
