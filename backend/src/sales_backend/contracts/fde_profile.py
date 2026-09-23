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


class ProfileFramework(BaseModel):
    dimensions: list[DimensionDefinition] = Field(min_length=6, max_length=6)


class ProfilePeriod(BaseModel):
    days: int = Field(ge=7, le=90)
    start: datetime
    end: datetime


class CoachingAdvice(BaseModel):
    title: str
    content: str


class ProfileLatest(BaseModel):
    dimensions: list[ProfileDimension] = Field(min_length=6, max_length=6)
    overall_score: None = None
    summary: str
    advice: list[CoachingAdvice]
    reviewed_at: datetime | None


class ProfileHistoryPoint(BaseModel):
    date: datetime
    dimensions: list[ProfileDimension] = Field(min_length=6, max_length=6)
    overall_score: None = None


class FdeProfileResponse(BaseModel):
    data_source: Literal["database"]
    contract_version: Literal["fde.profile.v1"]
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
