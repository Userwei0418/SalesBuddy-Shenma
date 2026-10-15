"""Independent Review scope v2; old report selection and wire types stay intact."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

ScopeDigest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ScopeKind = Literal["self", "member", "team"]


class ReviewScopeSelectionV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ScopeKind = "self"
    member_ids: list[UUID] = Field(default_factory=list, max_length=100)
    team_ids: list[UUID] = Field(default_factory=list, max_length=100)
    member_id: UUID | None = None
    team_id: UUID | None = None
    expected_scope_revision: ScopeDigest | None = None

    @model_validator(mode="before")
    @classmethod
    def exclusive_aliases(cls, value):
        if isinstance(value, dict):
            aliases = any(value.get(key) is not None for key in ("member_id", "team_id"))
            arrays = any(value.get(key) is not None for key in ("member_ids", "team_ids"))
            if aliases and arrays:
                raise ValueError("旧单值选择不能与范围数组同时传入")
            # Omitted repeated query fields commonly arrive from API adapters
            # as None; normalize them to the same empty defaults as omission.
            value = {key: item for key, item in value.items()
                     if key not in ("member_ids", "team_ids") or item is not None}
        return value

    @field_validator("member_ids", "team_ids")
    @classmethod
    def canonical_ids(cls, value):
        # Field length is checked before deduplication, bounding raw input too.
        return sorted(set(value), key=str)

    @model_validator(mode="after")
    def same_kind_nonempty(self):
        members = self.member_ids or ([self.member_id] if self.member_id else [])
        teams = self.team_ids or ([self.team_id] if self.team_id else [])
        if (
            (self.scope == "self" and (members or teams))
            or (self.scope == "member" and (not members or teams))
            or (self.scope == "team" and (not teams or members))
        ):
            raise ValueError("范围必须是本人，或非空同类成员/团队选择")
        self.member_ids, self.team_ids = members, teams
        self.member_id = self.team_id = None
        return self


class WorkReportScopeSelectionV2(BaseModel):
    """Public report snapshot selector for the array-selected Review route.

    ``ReviewScopeSelectionV2`` is also used by the read-only overview API and
    intentionally keeps its historical ``scope`` field.  Work-report input
    uses ``kind`` and carries the D02 revision explicitly, so the two wire
    shapes must not be silently conflated.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["work_report.scope_selection.v2"] = "work_report.scope_selection.v2"
    kind: ScopeKind
    team_ids: list[UUID] = Field(default_factory=list, max_length=100)
    member_ids: list[UUID] = Field(default_factory=list, max_length=100)
    # Optional on initial report creation: the server resolves and freezes the
    # revision in the same repeatable-read transaction. Clients may echo a
    # previously returned revision to detect a changed selection before retry.
    expected_scope_revision: ScopeDigest | None = None

    @field_validator("member_ids", "team_ids")
    @classmethod
    def canonical_ids(cls, value):
        return sorted(set(value), key=str)

    @model_validator(mode="after")
    def same_kind_nonempty(self):
        if (
            (self.kind == "self" and (self.member_ids or self.team_ids))
            or (self.kind == "member" and (not self.member_ids or self.team_ids))
            or (self.kind == "team" and (not self.team_ids or self.member_ids))
        ):
            raise ValueError("范围必须是本人，或非空同类成员/团队选择")
        if self.kind == "self" and len(self.member_ids) != 0:
            raise ValueError("本人范围不能携带成员数组")
        return self

    def review_selection(self) -> ReviewScopeSelectionV2:
        """Adapt to the read-only scope resolver without changing wire data."""
        return ReviewScopeSelectionV2(
            scope=self.kind,
            member_ids=self.member_ids,
            team_ids=self.team_ids,
            expected_scope_revision=self.expected_scope_revision,
        )


class ReviewScopeSubjectV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_type: Literal["person", "team"]
    id: UUID
    label: str


class ReviewResolvedScopeV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["review.scope.v2"] = "review.scope.v2"
    kind: ScopeKind
    label: str
    subject_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    subjects: tuple[ReviewScopeSubjectV2, ...]
    resolved_member_ids: tuple[UUID, ...]
    scope_key: str = Field(pattern=r"^review-scope:v2:[0-9a-f]{64}$")
    scope_revision: ScopeDigest
    membership_basis: Literal["current_authorized_at_data_as_of"] = "current_authorized_at_data_as_of"

    @model_validator(mode="after")
    def canonical_subjects(self):
        expected = "team" if self.kind == "team" else "person"
        if (
            self.subject_ids != tuple(sorted(set(self.subject_ids), key=str))
            or tuple(subject.id for subject in self.subjects) != self.subject_ids
            or any(subject.scope_type != expected for subject in self.subjects)
            or self.resolved_member_ids != tuple(sorted(set(self.resolved_member_ids), key=str))
            or (self.kind == "self" and len(self.subject_ids) != 1)
        ):
            raise ValueError("已解析范围的主体类型、排序或成员集合不一致")
        return self
