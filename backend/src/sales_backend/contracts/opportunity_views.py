"""Saved presentation settings; these never grant access to opportunity facts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Column = Literal["name", "stage", "owner", "amount", "signing", "signal", "money", "close", "product"]
DEFAULT_COLUMNS = ["name", "stage", "owner", "amount", "signing", "signal", "money", "close"]
COLUMNS = [
    {"value": value, "label": label} for value, label in (
        ("name", "商机 / 客户"), ("stage", "阶段"), ("owner", "负责人"),
        ("amount", "ACV / 金额"), ("signing", "签约方式"), ("signal", "状态"),
        ("money", "确收 / 回款"), ("close", "预计关单"), ("product", "产品线"),
    )
]


class ViewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpportunityViewFilters(ViewModel):
    scope: Literal["self", "team"] = "self"
    q: str = Field(default="", max_length=100)
    stages: list[Literal["identified", "qualified", "solution", "proposal", "negotiation", "won", "lost"]] = Field(default_factory=list, max_length=7)
    grade: Literal["A", "B", "C", "D"] | None = None
    owner_id: UUID | None = None
    team_id: UUID | None = None
    product_line: str | None = Field(default=None, max_length=100)
    year: int | None = Field(default=None, ge=2000, le=2100)
    quarters: list[Literal[1, 2, 3, 4]] = Field(default_factory=list, max_length=4)
    close_period: Literal["all", "month", "quarter", "year"] = "all"
    activity_window: Literal["all", "today", "day", "3d", "week", "month", "year"] = "all"
    overview_year: int | None = Field(default=None, ge=2000, le=2100)
    overview_quarters: list[Literal[1, 2, 3, 4]] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def valid_periods(self):
        if self.quarters and self.year is None:
            raise ValueError("关单季度必须带年份")
        if self.overview_quarters and self.overview_year is None:
            raise ValueError("统计季度必须带年份")
        self.stages = list(dict.fromkeys(self.stages))
        self.quarters = sorted(set(self.quarters))
        self.overview_quarters = sorted(set(self.overview_quarters))
        return self


class OpportunityViewConfig(ViewModel):
    schema_version: Literal[1] = 1
    columns: list[Column] = Field(default_factory=lambda: list(DEFAULT_COLUMNS), min_length=1, max_length=9)
    filters: OpportunityViewFilters = Field(default_factory=OpportunityViewFilters)
    order: Literal["quarter_stage"] = "quarter_stage"
    active_first: Literal[True] = True

    @field_validator("columns")
    @classmethod
    def normalize_columns(cls, values):
        columns = list(dict.fromkeys(values))
        return columns if "name" in columns else ["name", *columns]


class OpportunityViewCreate(ViewModel):
    name: str = Field(min_length=1, max_length=40)
    visibility: Literal["private", "team"] = "private"
    team_id: UUID | None = None
    config: OpportunityViewConfig = Field(default_factory=OpportunityViewConfig)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("请填写视图名称")
        return value

    @model_validator(mode="after")
    def valid_visibility(self):
        if (self.visibility == "team") != (self.team_id is not None):
            raise ValueError("团队视图必须指定团队，个人视图不能指定分享团队")
        return self


class OpportunityViewUpdate(OpportunityViewCreate):
    version_no: int = Field(ge=1)


class OpportunityViewDelete(ViewModel):
    version_no: int = Field(ge=1)


class OpportunityViewItem(OpportunityViewCreate):
    id: UUID
    owner_user_ref_id: UUID
    owner_name: str
    team_name: str | None = None
    version_no: int
    created_at: datetime
    updated_at: datetime
    can_edit: bool
    can_delete: bool


class OpportunityViewList(ViewModel):
    items: list[OpportunityViewItem]
    total: int
    limit: int
    offset: int
    data_source: Literal["database"] = "database"


class OpportunityViewTeam(ViewModel):
    id: UUID
    name: str
    can_publish: bool


class OpportunityViewOptions(ViewModel):
    columns: list[dict[str, str]]
    default_columns: list[Column]
    teams: list[OpportunityViewTeam]
    can_create_private: bool = True
    definition_version: Literal["opportunity_view_v1"] = "opportunity_view_v1"


class OpportunityViewDeleted(ViewModel):
    id: UUID
    deleted: Literal[True]
    version_no: int
