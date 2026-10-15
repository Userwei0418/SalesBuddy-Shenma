"""Validate explanatory candidates; authenticated Backend owns the original preview.

No calculation, persistence, authority or semantic-truth attestation happens here.
The model's source_kind is a legacy input-origin label, never Backend provenance.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sales_backend.services.agent_platform.routing import InvalidAgentResult


class Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=4000)
    source_paths: list[str] = Field(min_length=1, max_length=64)


class PreviewExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contract_version: Literal["finance_assistant.v1"]
    task: Literal["explain_preview"]
    status: Literal["reference_only"]
    source_kind: Literal["user_provided"]
    candidate_ids: list[str] = Field(max_length=0)
    proposed_fields: dict[str, Any] = Field(max_length=0)
    field_evidence: list[dict[str, Any]] = Field(max_length=0)
    explanations: list[Explanation] = Field(min_length=1, max_length=40)
    unresolved_fields: list[str] = Field(max_length=40)
    warnings: list[str] = Field(max_length=40)
    requires_human_confirmation: Literal[True]


PATH = re.compile(r"preview(?:(?:\.[A-Za-z_][A-Za-z0-9_-]*)|(?:\[(?:0|[1-9][0-9]{0,5})\]))*\Z")
PART = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)|\[([0-9]+)\]")


def _exists(path: str, preview: dict[str, Any]) -> bool:
    if len(path) > 400 or not PATH.fullmatch(path):
        return False
    value: Any = preview
    for key, index in PART.findall(path[len("preview"):]):
        if key:
            if not isinstance(value, dict) or key not in value:
                return False
            value = value[key]
        else:
            if not isinstance(value, list) or int(index) >= len(value):
                return False
            value = value[int(index)]
    return True


def validate_preview_explanation(payload: Any, *, preview: dict[str, Any]) -> dict[str, Any]:
    """Accept references to this preview only; never merge prose into financial facts.

Backend must retain and display its own preview and provenance, and recheck the
same authorization/version before delivery. Existing paths do not prove prose
truth: semantic acceptance is a separate check. Errors contain no input data.
    """
    try:
        if not isinstance(payload, dict) or payload.get("requires_human_confirmation") is not True:
            raise ValueError("explicit human confirmation flag required")
        result = PreviewExplanation.model_validate(payload)
        if not isinstance(preview, dict) or not preview:
            raise ValueError("preview required")
        if any(len(text) > 1000 for text in [*result.warnings, *result.unresolved_fields]):
            raise ValueError("oversized caveat")
        if any(not _exists(path, preview) for item in result.explanations for path in item.source_paths):
            raise ValueError("unknown source path")
        return result.model_dump(mode="json")
    except (ValidationError, ValueError, TypeError):
        raise InvalidAgentResult("finance_assistant.invalid_explanation") from None
