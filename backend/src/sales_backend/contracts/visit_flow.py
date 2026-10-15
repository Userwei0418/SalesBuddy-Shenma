"""Two model contracts; business identity and archival metadata stay server-owned."""

from copy import deepcopy
from datetime import date
from typing import Any

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
        default_factory=list, max_length=30,
        description="本次拜访参与的FDE；schema2显式长期名单为最终值，出席不追加覆盖；未显式维护名单时保留既有追加规则"
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


class QualityReviewOutput(BaseModel):
    """Canonical quality-stage response.

    The reviewed fields and summary are already human-confirmed facts owned by
    the backend.  The model only needs to produce the review decision.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    quality_review: QualityReview


class QualityOutput(QualityReviewOutput):
    """Compatibility envelope for older Agents that echoed the reviewed facts.

    These two values are intentionally untyped and are discarded by
    ``validate_stage_result``.  They are accepted only during the transition so
    an older published Agent does not turn a valid review into a failed run.
    """

    fields: Any | None = None
    summary: Any | None = None


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
    if stage == "quality":
        # ``fields`` and ``summary`` are trusted snapshots loaded from the
        # reviewed request.  Never persist a model echo of either value: an
        # older Agent may still return them and a newer Agent omits them.
        output = QualityOutput.model_validate(result)
        quality = output.quality_review.model_dump()
        output = {
            "fields": deepcopy(facts["fields"]),
            "summary": facts["summary"],
            "quality_review": quality,
        }
        policy = facts["company_policy"]
        admission = VisitAdmissionPolicy(**policy["definition"])
        action = quality["next_action"]
        if action["passed"] and not (action["time_found"] and action["goal_or_plan_found"]):
            raise ValueError("下一步计划审核结论与依据不一致")
        quality["grade"] = admission.grade(quality["follow_up_score"])
        quality["admission_policy"] = admission.model_dump(mode="json")
        # Policy provenance comes from the trusted input, not a fabricated model receipt.
        output["company_policy"] = policy
    else:
        output = StructureOutput.model_validate(result).model_dump()
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
            mode="json", exclude={"amount_confirmed_value"}
        )
        if fields.get("_opportunity_mutation")
        else None,
    }


def reviewed_archival_snapshot(request):
    """Legacy reviews without attendance mean no explicitly selected attendees."""
    mutation = request.get("opportunity_mutation")
    # Human acknowledgement is not a reviewed business fact. Allow a final
    # acknowledgement after quality review, including drafts from older clients.
    # The actual amount remains bound to the reviewed snapshot and save guard.
    if mutation:
        mutation = {key: value for key, value in mutation.items() if key != "amount_confirmed_value"}
    return {
        **{key: request.get(key) for key in ("fields", "opportunity_id", "source_import_id", "opportunity_mutation")},
        "opportunity_mutation": mutation,
        "collaborator_ids": sorted(set(request.get("collaborator_ids") or [])),
        "fde_participant_ids": sorted(set(request.get("fde_participant_ids") or [])),
    }
