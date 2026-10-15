"""Public self-profile contract; coaching and numerical evidence are separate."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DimensionDefinition(BaseModel):
    code: str
    name: str
    short_name: str


class ProfileDimension(DimensionDefinition):
    score: float | None = Field(ge=0, le=100)
    assessment: str
    coaching_action: str = ""
    evidence_count: int = Field(ge=0)
    numerator: float
    denominator: int = Field(ge=0)
    percent: bool | None = None


class FdeScoreBasis(BaseModel):
    basis_id: str | None = None
    calculation_version: Literal["fde_portrait_ratios_v1"] | None = None
    snapshot_status: Literal["frozen", "legacy_unknown"] = "legacy_unknown"


class CoachingCoverage(BaseModel):
    status: Literal["known", "legacy_unknown"] = "legacy_unknown"
    visit_limit: Literal[12] = 12
    task_limit: Literal[20] = 20
    selected_visits: int | None = Field(default=None, ge=0, le=12)
    selected_tasks: int | None = Field(default=None, ge=0, le=20)
    truncated_text_sources: int | None = Field(default=None, ge=0)


class ProfileFramework(BaseModel):
    dimensions: list[DimensionDefinition] = Field(min_length=6, max_length=6)


class ProfilePeriod(BaseModel):
    days: int = Field(ge=7, le=90)
    start: datetime
    end: datetime


class CoachingAdvice(BaseModel):
    title: str
    content: str
    source_refs: list[str] = Field(default_factory=list)


class ProfileLatest(BaseModel):
    dimensions: list[ProfileDimension] = Field(min_length=6, max_length=6)
    overall_score: None = None
    summary: str
    advice: list[CoachingAdvice]
    reviewed_at: datetime | None
    score_basis: FdeScoreBasis = Field(default_factory=FdeScoreBasis)


class ProfileHistoryPoint(BaseModel):
    date: datetime
    dimensions: list[ProfileDimension] = Field(min_length=6, max_length=6)
    overall_score: None = None
    run_id: str | None = None
    reviewed_at: datetime | None = None
    score_basis: FdeScoreBasis = Field(default_factory=FdeScoreBasis)
    evidence_coverage: dict[str, int | None] | None = None
    coaching_coverage: CoachingCoverage = Field(default_factory=CoachingCoverage)


class FdeProfileResponse(BaseModel):
    data_source: Literal["database"]
    contract_version: Literal["fde.profile.v1"]
    assessment_kind: Literal["fde_factual_portrait"] = "fde_factual_portrait"
    as_of: datetime
    period: ProfilePeriod
    sample_count: int = Field(ge=0)
    framework: ProfileFramework
    latest: ProfileLatest
    review_status: Literal["empty", "missing", "queued", "running", "succeeded", "failed"]
    facts_fingerprint: str
    review_run_id: str | None
    evidence_coverage: dict[str, int]
    scope_note: str
    scope: Literal["self", "team"] = "self"
    member_id: str | None = None
    member_name: str | None = None
    can_review: bool = True
    history: list[ProfileHistoryPoint] = Field(default_factory=list)
    coaching_coverage: CoachingCoverage = Field(default_factory=CoachingCoverage)


class FdeReviewSubject(BaseModel):
    id: str
    name: str | None = None
    account_code: str | None = None


class FdeReviewEvidence(BaseModel):
    source_type: Literal["visit", "task"]
    source_id: str | None = None
    source_version: int | None = None
    source_ref: str | None = None
    detail: str | None = None
    readable: bool


class FdeReviewSnapshot(ProfileHistoryPoint):
    period: ProfilePeriod | None = None
    sample_count: int | None = Field(default=None, ge=0)
    as_of: datetime | None = None
    advice: list[CoachingAdvice] = Field(default_factory=list)


class FdeEvidenceSummary(BaseModel):
    total: int = Field(ge=0)
    readable: int = Field(ge=0)
    unavailable: int = Field(ge=0)


class FdeProfileReviewDetail(BaseModel):
    contract_version: Literal["fde.profile.v1"]
    assessment_kind: Literal["fde_factual_portrait"]
    subject: FdeReviewSubject
    review: FdeReviewSnapshot
    evidence: list[FdeReviewEvidence]
    evidence_summary: FdeEvidenceSummary
    can_review: bool
