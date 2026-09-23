"""Two model contracts; business identity and archival metadata stay server-owned."""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, create_model

from sales_backend.contracts.models import OpportunityCreate
from sales_backend.contracts.types import UUIDString
from sales_backend.contracts.visit_schema import TEXT_FIELDS
from sales_backend.domain.company_rules import VisitAdmissionPolicy

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


class StructureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: UUIDString
    opportunity_id: UUIDString | None = None
    text: str = Field(min_length=1, max_length=50000)
    is_first_visit: bool = False
    source_import_id: UUIDString | None = None


class QualityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: UUIDString
    opportunity_id: UUIDString | None = None
    source_run_id: UUIDString
    fields: VisitFields
    summary: str = Field(max_length=5000)
    source_import_id: UUIDString | None = None
    collaborator_ids: list[UUIDString] = Field(default_factory=list, max_length=30)
    fde_participant_ids: list[UUIDString] = Field(
        default_factory=list, max_length=30, description="本次拜访参与的FDE；归档时追加至商机名单，不覆盖已有成员"
    )
    opportunity_mutation: OpportunityCreate | None = None


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


def canonical_fields(fields):
    return VisitFields.model_validate(
        {key: fields.get(key, False if key == "is_first_visit" else "") for key in VisitFields.model_fields}
    ).model_dump()


def validate_quality_input(fields):
    from sales_backend.domain.visit_contract import validate_content

    validate_content(fields)
    for key, label in (("interaction_at", "跟进日期"), ("created_date", "创建时间")):
        try:
            date.fromisoformat(fields[key])
        except (ValueError, TypeError):
            raise ValueError(f"请填写有效的{label}") from None
    if not fields["contact_name"].strip():
        raise ValueError("请填写对接人")


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


def archival_snapshot(fields):
    """Only explicitly supported archival fields participate in the reviewed snapshot."""
    return {
        "fields": canonical_fields(fields),
        "opportunity_id": fields.get("opportunity_id") or None,
        "source_import_id": fields.get("source_import_id") or None,
        "collaborator_ids": sorted(set(fields.get("collaborator_ids") or [])),
        "fde_participant_ids": sorted(set(fields.get("_fde_participant_ids") or [])),
        "opportunity_mutation": OpportunityCreate.model_validate(fields["_opportunity_mutation"]).model_dump(
            mode="json"
        )
        if fields.get("_opportunity_mutation")
        else None,
    }


def reviewed_archival_snapshot(request):
    """Legacy reviews without attendance mean no explicitly selected attendees."""
    return {
        **{key: request.get(key) for key in ("fields", "opportunity_id", "source_import_id", "opportunity_mutation")},
        "collaborator_ids": sorted(set(request.get("collaborator_ids") or [])),
        "fde_participant_ids": sorted(set(request.get("fde_participant_ids") or [])),
    }
