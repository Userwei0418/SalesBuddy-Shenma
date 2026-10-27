"""Public progress contract; provider credentials/IDs never leave this projection."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from sales_backend.contracts.knowledge import KnowledgeId, KnowledgeModel, Revision, VersionState


class KnowledgeIngestionInput(KnowledgeModel):
    expected_material_revision: Revision


class KnowledgeIngestionView(KnowledgeModel):
    material_id: KnowledgeId
    version_id: KnowledgeId
    ingestion_id: KnowledgeId | None = None
    state: VersionState
    availability: Literal[
        "unconfigured",
        "files_required",
        "links_not_archived",
        "ready",
        "running",
        "retryable",
        "unknown",
        "complete",
        "stale",
    ]
    can_retry: bool = False
    error_code: str | None = None
    processed_files: int = Field(default=0, ge=0)
    total_files: int = Field(default=0, ge=0)
    observed_at: datetime | None = None
