from pydantic import BaseModel, ConfigDict, Field, create_model

from sales_contracts.contracts.visit_schema import TEXT_FIELDS

from sales_contracts.domain.company_rules import VisitAdmissionPolicy

VisitFields = create_model(
    "VisitFields",
    __config__=ConfigDict(extra="forbid", strict=True),
    **{
        key: (
            str,
            Field(
                max_length=15000
                if key in {"follow_up_record", "next_action", "visit_goal", "customer_main_business", "customer_needs"}
                else 300
            ),
        )
        for key in TEXT_FIELDS
    },
    is_first_visit=(bool, ...),
)

class StructureOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    fields: VisitFields
    summary: str = Field(max_length=5000)

class NextActionReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    passed: bool
    time_found: bool
    goal_or_plan_found: bool
    suggestions: list[str] = Field(max_length=10)

class QualityReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    follow_up_score: int = Field(ge=0, le=100)
    suggestions: list[str] = Field(max_length=10)
    next_action: NextActionReview

class QualityOutput(StructureOutput):
    quality_review: QualityReview

def validate_stage_result(result, facts):
    stage = facts["visit_stage"]
    output = (QualityOutput if stage == "quality" else StructureOutput).model_validate(result).model_dump()
    if stage == "quality":
        if output["fields"] != facts["fields"] or output["summary"] != facts["summary"]:
            raise ValueError("质检改写了已核对的内容，请重新质检")
        policy = facts["company_policy"]
        admission = VisitAdmissionPolicy(**policy["definition"])
        quality = output["quality_review"]
        action = quality["next_action"]
        if action["passed"] and not (action["time_found"] and action["goal_or_plan_found"]):
            raise ValueError("下一步计划审核结论与依据不一致")
        quality["grade"] = admission.grade(quality["follow_up_score"])
        quality["admission_policy"] = admission.model_dump(mode="json")
        # Policy provenance comes from the trusted input, not a fabricated model receipt.
        output["company_policy"] = policy
    else:
        for key in ("customer_name", "customer_type", "created_date"):
            output["fields"][key] = facts["server_fields"][key]
        output["fields"]["is_first_visit"] = facts["is_first_visit"]
    return {**output, "visit_stage": stage, "prompt_version": "visit-split-v1"}
