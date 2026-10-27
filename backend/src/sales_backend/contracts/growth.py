"""Sales growth v2 public detail and explicit request receipts."""
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field


class GrowthReviewReceipt(BaseModel):
    review_id: UUID
    status: Literal['queued', 'running', 'succeeded', 'failed']
    generation_no: int = Field(ge=1)
    input_fingerprint: str
    reused: bool
    trigger_type: Literal['manual', 'visit.archived', 'visit.supplemented']


class GrowthReviewSubject(BaseModel):
    id: UUID
    name: str | None = None
    account_code: str | None = None


class GrowthReviewEvidence(BaseModel):
    visit_id: UUID | None = None
    visit_version: int | None = Field(default=None, ge=1)
    dimension_codes: list[str]
    detail: str | None = None
    readable: bool


class EvidenceSummary(BaseModel):
    total: int = Field(ge=0)
    readable: int = Field(ge=0)
    unavailable: int = Field(ge=0)


class GrowthFrozenReview(BaseModel):
    id: UUID
    review_date: date
    reviewed_at: datetime | None = None
    generation_no: int | None = Field(default=None, ge=1)
    overall_score: float | None = None
    summary: str | None = None
    evaluation_state: Literal['assessed', 'partially_assessed', 'insufficient_data', 'legacy_unknown']
    dimension_scores: dict[str, dict]
    strengths: list[str]
    improvements: list[str]
    score_basis: dict
    score_summary: dict | None
    evidence_coverage: dict
    input_fingerprint: str | None
    data_as_of: datetime | None


class GrowthReviewDetail(BaseModel):
    contract_version: Literal['sales.growth.v2']
    assessment_kind: Literal['sales_competency']
    subject: GrowthReviewSubject
    review: GrowthFrozenReview
    evidence: list[GrowthReviewEvidence]
    evidence_summary: EvidenceSummary
    can_review: bool
