from pydantic import BaseModel, ConfigDict, Field, field_validator

class SuggestionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    evidence: str = Field(min_length=1, max_length=1500)
    action: str = Field(min_length=5, max_length=500)
    evidence_refs: list[str] = Field(min_length=1, max_length=10)

class AdviceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=1500)
    suggestions: list[SuggestionOutput] = Field(max_length=3)

def evidence_references(facts):
    allowed = {"subject"}
    for kind, records in facts.get("records", {}).items():
        allowed.update(f"{kind}:{r['id']}" for r in records)
    return sorted(allowed)

def validate_advice(result, facts):
    output = AdviceOutput.model_validate(result).model_dump()
    allowed = set(evidence_references(facts))
    titles = set()
    for item in output["suggestions"]:
        title = item["title"].strip()
        if not title or title in titles or not set(item["evidence_refs"]).issubset(allowed):
            raise ValueError("建议重复或引用了未提供的事实")
        titles.add(title)
    return output
