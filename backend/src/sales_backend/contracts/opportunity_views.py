"""Personal presentation settings; never a source of business authorization."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Column = Literal["name", "stage", "owner", "amount", "signal", "money", "close", "product"]
DEFAULT_COLUMNS = ["name", "stage", "signal", "money", "close"]
COLUMNS = [
    {"value": value, "label": label}
    for value, label in (
        ("name", "商机 / 客户"),
        ("stage", "阶段"),
        ("owner", "负责人"),
        ("amount", "商机金额"),
        ("signal", "状态"),
        ("money", "确收 / 回款"),
        ("close", "预计关单"),
        ("product", "产品线"),
    )
]


class ViewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpportunityViewFilters(ViewModel):
    q: str = Field(default="", max_length=100)
    scope: Literal["self", "team"] | None = None
    team_id: UUID | None = None
    owner_id: UUID | None = None
    stages: list[Literal["identified", "qualified", "solution", "proposal", "negotiation", "won", "lost"]] = Field(
        default_factory=list, max_length=7
    )
    grade: Literal["A", "B", "C", "D"] | None = None
    product_line: str | None = Field(default=None, max_length=100)
    year: int | None = Field(default=None, ge=2000, le=2100)
    quarters: list[Literal[1, 2, 3, 4]] = Field(default_factory=list, max_length=4)
    close_period: Literal["all", "month", "quarter", "year"] = "all"
    include_closed: bool = True

    @model_validator(mode="after")
    def valid_periods(self):
        if self.quarters and self.year is None:
            raise ValueError("关单季度必须带年份")
        if self.quarters and self.close_period != "all":
            raise ValueError("指定季度与相对关单日期不能同时使用")
        self.q = self.q.strip()
        self.stages = list(dict.fromkeys(self.stages))
        self.quarters = sorted(set(self.quarters))
        return self


class OpportunityViewConfig(ViewModel):
    schema_version: Literal[1] = 1
    columns: list[Column] = Field(default_factory=lambda: list(DEFAULT_COLUMNS), min_length=1, max_length=8)
    filters: OpportunityViewFilters = Field(default_factory=OpportunityViewFilters)
    order: Literal["quarter_stage", "close_date"] = "quarter_stage"
    group_by: Literal["quarter", "none"] = "quarter"

    @field_validator("columns")
    @classmethod
    def normalize_columns(cls, values):
        columns = list(dict.fromkeys(values))
        return columns if "name" in columns else ["name", *columns]


class OpportunityViewName(ViewModel):
    name: str = Field(min_length=1, max_length=40)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("请填写视图名称")
        return value


class OpportunityViewCreate(OpportunityViewName):
    config: OpportunityViewConfig = Field(default_factory=OpportunityViewConfig)


class OpportunityViewUpdate(OpportunityViewName):
    version_no: int = Field(ge=1)
    config: OpportunityViewConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_config(cls, values):
        if isinstance(values, dict) and "config" in values and values["config"] is None:
            raise ValueError("配置不能为 null；重命名时请省略 config")
        return values


class OpportunityViewDelete(ViewModel):
    version_no: int = Field(ge=1)


class OpportunityViewItem(OpportunityViewCreate):
    id: UUID
    owner_user_ref_id: UUID
    version_no: int
    created_at: datetime
    updated_at: datetime
    config_valid: bool = True
    config_error: str | None = None
    can_edit: Literal[True] = True
    can_delete: Literal[True] = True


class OpportunityViewList(ViewModel):
    items: list[OpportunityViewItem]
    total: int
    limit: int
    offset: int
    data_source: Literal["database"] = "database"


class OpportunityViewOptions(ViewModel):
    columns: list[dict[str, str]]
    default_columns: list[Column]
    can_create_private: Literal[True] = True
    definition_version: Literal["shenma_opportunity_view_v1"] = "shenma_opportunity_view_v1"


class OpportunityViewDeleted(ViewModel):
    id: UUID
    deleted: Literal[True]
    version_no: int
