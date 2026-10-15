"""Shared report primitives; preserve existing wire serialization and hashes."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_serializer, model_validator

Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]


PeriodKind = Literal["day", "week", "two_weeks", "month", "quarter"]


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_serializer(mode="wrap")
    def omit_absent_week_extension(self, handler):
        # Older immutable payload/config hashes must not gain new null keys.
        data = handler(self)
        for key in (
            "facts_contract",
            "sales_week_facts",
            "section_id",
            "actual_lineage",
            "general_scheme",
            "builtin_schemes",
            "meeting_facts",
            "overview_facts",
            "overview_dependencies",
            "overview_binding",
            "source_notes",
            "review_facts",
            "review_dependencies",
            "review_mode",
            "review_content",
        ):
            if key in data and data[key] is None:
                del data[key]
        return data

    @model_validator(mode="before")
    @classmethod
    def valid_text(cls, value):
        def check(item):
            if isinstance(item, str):
                if "\x00" in item:
                    raise ValueError("文本不能包含空字符")
                try:
                    item.encode("utf-8")
                except UnicodeError:
                    raise ValueError("文本包含无效Unicode字符") from None
            elif isinstance(item, dict):
                for key, child in item.items():
                    check(key)
                    check(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    check(child)

        check(value)
        return value


class PeriodSelection(ReportModel):
    kind: PeriodKind = "week"
    anchor_date: date | None = None  # omitted => server's Shanghai today
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"


class ResolvedPeriod(ReportModel):
    kind: PeriodKind
    start: AwareDatetime
    end_exclusive: AwareDatetime
    cutoff: AwareDatetime
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    label: ShortText

    @model_validator(mode="after")
    def bounds(self):
        if not self.start < self.end_exclusive or not self.start <= self.cutoff <= self.end_exclusive:
            raise ValueError("周期起止或实际截止时间不一致")
        return self


class ReportError(ReportModel):
    detail: ShortText
    code: str = Field(pattern=r"^(WORK_REPORT_[A-Z_]+|IDEMPOTENCY_CONFLICT)$")
    retryable: bool = False
    current_version: int | None = Field(default=None, ge=1, strict=True)


class ScopeSelection(ReportModel):
    kind: Literal['self', 'member', 'team', 'workspace'] = 'self'
    member_id: UUID | None = None
    team_id: UUID | None = None

    @model_validator(mode='after')
    def selector(self):
        if ((self.kind in ('self', 'workspace') and (self.member_id or self.team_id))
                or (self.kind == 'member' and (not self.member_id or self.team_id))
                or (self.kind == 'team' and (not self.team_id or self.member_id))):
            raise ValueError('范围类型与成员/团队选择不一致')
        return self




class ResolvedScope(ScopeSelection):
    label: ShortText
    member_ids: list[UUID]  # backend-resolved scope, never accepted as API selection

    @model_validator(mode='after')
    def resolved_members(self):
        if len(set(self.member_ids)) != len(self.member_ids):
            raise ValueError('已解析成员不能重复')
        if self.kind == 'member' and self.member_ids != [self.member_id]:
            raise ValueError('个人范围必须与已解析成员一致')
        if self.kind == 'self' and len(self.member_ids) != 1:
            raise ValueError('本人范围必须恰好包含本人')
        return self
