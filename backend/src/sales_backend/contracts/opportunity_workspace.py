"""Typed v2 saved configuration. No client-provided SQL or permission claims."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_serializer, model_validator

from sales_backend.domain.opportunity_field_catalog import FIELD_CATALOG

CONFIG_MAX_BYTES = 32768


def config_storage_bytes(config):
    # jsonb::text emits unescaped UTF-8 and a space after commas/colons.
    # Object key ordering changes no bytes; the persisted document uses this
    # same normalized exclude_none projection, including all field defaults.
    document = config.model_dump(mode="json", exclude_none=True)
    def check_strings(value):
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("配置文本不能包含空字符")
        if isinstance(value, dict):
            for item in value.values():
                check_strings(item)
        elif isinstance(value, list):
            for item in value:
                check_strings(item)
    check_strings(document)
    encoded = json.dumps(document, ensure_ascii=False, separators=(", ", ": "))
    try:
        return len(encoded.encode("utf-8"))
    except UnicodeError:
        raise ValueError("配置包含无效Unicode字符") from None


class WorkspaceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def field_definition(key):
    if key not in FIELD_CATALOG:
        raise ValueError(f"不支持的商机字段：{key}")
    return FIELD_CATALOG[key]


class WorkspaceColumn(WorkspaceModel):
    key: str
    width: int = Field(default=160, ge=80, le=600, strict=True)
    pinned: Literal["left", "right"] | None = None

    @field_validator("key")
    @classmethod
    def known_field(cls, value):
        field_definition(value)
        return value

    @field_validator("pinned")
    @classmethod
    def explicit_pin(cls, value):
        if value is None:
            raise ValueError("未固定的列请省略pinned")
        return value

    @model_serializer(mode="wrap")
    def omit_unpinned(self, handler):
        result = handler(self)
        if self.pinned is None:
            result.pop("pinned", None)
        return result


def typed_filter_value(field, value):
    kind = field["type"]
    if value is None:
        raise ValueError("空值请使用is_empty或is_not_empty")
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError("布尔筛选必须使用true或false")
        return value
    if kind in {"person", "people"}:
        if not isinstance(value, str):
            raise ValueError("人员筛选必须使用UUID字符串")
        return str(UUID(value))
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, int) or abs(value) > 2147483647:
            raise ValueError("数量筛选必须使用有效整数")
        return value
    if kind == "money":
        if not isinstance(value, str) or not re.fullmatch(r"-?[0-9]{1,16}(?:\.[0-9]{1,2})?", value):
            raise ValueError("金额筛选必须使用十进制字符串，单位元")
        try:
            amount = Decimal(value)
        except InvalidOperation:
            raise ValueError("金额格式无效") from None
        if not amount.is_finite() or abs(amount) >= Decimal("1e16") or amount.as_tuple().exponent < -2:
            raise ValueError("金额最多16位整数和2位小数")
        return format(amount, "f")
    if not isinstance(value, str) or len(value) > 5000:
        raise ValueError("筛选值必须使用有效字符串")
    if kind in {"enum", "multi_enum"}:
        if value not in {option["value"] for option in field["options"]}:
            raise ValueError(f"{field['label']}选项无效")
    elif kind == "date":
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError("日期必须使用YYYY-MM-DD")
    elif kind == "datetime":
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("时间筛选必须包含时区")
    return value


class WorkspaceFilter(WorkspaceModel):
    field: str
    operator: Literal[
        "eq", "ne", "in", "contains", "gt", "gte", "lt", "lte", "between",
        "has_any", "has_all", "is_empty", "is_not_empty",
    ]
    value: Any = None

    @model_validator(mode="after")
    def typed(self):
        definition = field_definition(self.field)
        if self.operator not in definition["filter_ops"]:
            raise ValueError("该字段不支持所选筛选操作")
        if self.operator in {"is_empty", "is_not_empty"}:
            if "value" in self.model_fields_set:
                raise ValueError("空值筛选不应传value")
            return self
        if self.operator in {"in", "has_any", "has_all", "between"}:
            if not isinstance(self.value, list) or not 1 <= len(self.value) <= 100:
                raise ValueError("多值筛选需要1至100个值")
            if self.operator == "between" and len(self.value) != 2:
                raise ValueError("范围筛选需要两个端点")
            self.value = [typed_filter_value(definition, item) for item in self.value]
            if self.operator == "between":
                values = self.value
                if definition["type"] == "money":
                    values = [Decimal(v) for v in values]
                elif definition["type"] == "datetime":
                    values = [datetime.fromisoformat(v.replace("Z", "+00:00")) for v in values]
                if values[0] > values[1]:
                    raise ValueError("筛选范围起点不能大于终点")
        else:
            self.value = typed_filter_value(definition, self.value)
        return self


class WorkspaceSort(WorkspaceModel):
    field: str
    direction: Literal["asc", "desc"]

    @field_validator("field")
    @classmethod
    def sortable_field(cls, value):
        if not field_definition(value)["sortable"]:
            raise ValueError("该字段不支持排序")
        return value


class WorkspaceScope(WorkspaceModel):
    kind: Literal["self", "authorized"] = "authorized"
    team_id: UUID | None = None
    owner_id: UUID | None = None
    team_ids: list[UUID] = Field(default_factory=list, max_length=100)
    owner_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @field_validator("team_ids", "owner_ids")
    @classmethod
    def unique_subjects(cls, value):
        return list(dict.fromkeys(value))

    @property
    def selected_team_ids(self):
        return self.team_ids or ([self.team_id] if self.team_id else [])

    @property
    def selected_owner_ids(self):
        return self.owner_ids or ([self.owner_id] if self.owner_id else [])

    @model_validator(mode="after")
    def self_has_no_other_subject(self):
        for single, multiple in ((self.team_id, self.team_ids), (self.owner_id, self.owner_ids)):
            if single is not None and multiple and multiple != [single]:
                raise ValueError("同一维度的单选与多选范围不一致，请只传一种选择")
        if self.kind == "self" and (self.selected_team_ids or self.selected_owner_ids):
            raise ValueError("本人范围不能附带其他团队或负责人")
        return self

    @model_serializer(mode="wrap")
    def omit_empty_arrays(self, handler):
        # Preserve old single-select documents and idempotency payloads exactly.
        result = handler(self)
        for key in ("team_ids", "owner_ids"):
            if not getattr(self, key):
                result.pop(key, None)
        return result


class WorkspaceDateFilter(WorkspaceModel):
    field: Literal["business_created_on", "expected_close_date"]
    start: date
    end: date

    @field_validator("start", "end", mode="before")
    @classmethod
    def calendar_date(cls, value):
        if type(value) is date:
            return value
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("日期范围必须使用YYYY-MM-DD")
        return date.fromisoformat(value)

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.start > self.end:
            raise ValueError("日期范围起点不能大于终点")
        return self


class WorkspacePeriod(WorkspaceModel):
    kind: Literal["current_quarter", "fixed"] = "current_quarter"
    year: int | None = Field(default=None, ge=2000, le=2100, strict=True)
    quarter: int | None = Field(default=None, ge=1, le=4, strict=True)

    @model_validator(mode="after")
    def exact_period(self):
        if self.kind == "fixed" and (self.year is None or self.quarter is None):
            raise ValueError("固定期间须指定年份和季度")
        if self.kind == "current_quarter" and (self.year is not None or self.quarter is not None):
            raise ValueError("当前季度不能同时保存固定年份或季度")
        return self


class OpportunityWorkspaceConfig(WorkspaceModel):
    schema_version: Literal[2] = 2
    columns: list[WorkspaceColumn] = Field(min_length=1, max_length=60, default_factory=lambda: [
        WorkspaceColumn(key="name", width=240, pinned="left"),
        WorkspaceColumn(key="customer_name", width=180), WorkspaceColumn(key="stage", width=130),
        WorkspaceColumn(key="owner", width=130), WorkspaceColumn(key="amount", width=150),
    ])
    q: str = Field(default="", max_length=100, strict=True)
    filters: list[WorkspaceFilter] = Field(default_factory=list, max_length=30)
    sorts: list[WorkspaceSort] = Field(default_factory=lambda: [
        WorkspaceSort(field="status", direction="asc"), WorkspaceSort(field="expected_close_date", direction="asc"),
    ], max_length=5)
    group_by: str | None = None
    scope: WorkspaceScope = Field(default_factory=WorkspaceScope)
    period: WorkspacePeriod = Field(default_factory=WorkspacePeriod)
    active_quarter: StrictBool = Field(default=False, description="仅显示所选财务季度预测确收大于0或预测回款大于0的商机；默认不限制")
    date_filter: WorkspaceDateFilter | None = None
    density: Literal["compact", "comfortable"] = "comfortable"
    wrap: StrictBool = False

    @model_serializer(mode="wrap")
    def omit_unset_date_filter(self, handler):
        result = handler(self)
        if self.date_filter is None:
            result.pop("date_filter", None)
        if not self.active_quarter:
            result.pop("active_quarter", None)
        return result

    @field_validator("group_by")
    @classmethod
    def groupable_field(cls, value):
        if value is not None and not field_definition(value)["groupable"]:
            raise ValueError("该字段不支持分组")
        return value

    @model_validator(mode="after")
    def unique_fields(self):
        if len(self.columns) != len({column.key for column in self.columns}):
            raise ValueError("显示列不能重复")
        if len(self.sorts) != len({sort.field for sort in self.sorts}):
            raise ValueError("排序字段不能重复")
        if config_storage_bytes(self) > CONFIG_MAX_BYTES:
            raise ValueError("视图配置序列化后最多32KiB，请减少筛选项或文本长度")
        return self


class OpportunityWorkspaceQuery(WorkspaceModel):
    config: OpportunityWorkspaceConfig
    offset: int = Field(default=0, ge=0, le=100000, strict=True)
    limit: int = Field(default=50, ge=1, le=100, strict=True)


class OpportunityWorkspaceCreate(WorkspaceModel):
    name: str = Field(min_length=1, max_length=40, strict=True)
    visibility: Literal["private", "team", "company"] = "private"
    team_id: UUID | None = None
    config: OpportunityWorkspaceConfig = Field(default_factory=OpportunityWorkspaceConfig)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def valid_visibility(self):
        if (self.visibility == "team") != (self.team_id is not None):
            raise ValueError("仅团队视图必须指定分享团队")
        return self


class OpportunityWorkspaceUpdate(OpportunityWorkspaceCreate):
    version_no: int = Field(ge=1, strict=True)


class OpportunityWorkspacePreference(WorkspaceModel):
    default_view_id: UUID | None
    version_no: int = Field(ge=0, strict=True)


class OpportunityWorkspaceDelete(WorkspaceModel):
    version_no: int = Field(ge=1, strict=True)


class OpportunityWorkspaceItem(WorkspaceModel):
    id: UUID
    name: str
    visibility: Literal["private", "team", "company"]
    team_id: UUID | None = None
    owner_user_ref_id: UUID
    owner_name: str
    team_name: str | None = None
    config: OpportunityWorkspaceConfig | None
    version_no: int
    created_at: datetime
    updated_at: datetime
    can_edit: bool
    can_delete: bool
    migration_notice: dict[str, str] | None = None
    migration_blocked: bool = False


class OpportunityWorkspaceList(WorkspaceModel):
    items: list[OpportunityWorkspaceItem]
    total: int
    limit: int
    offset: int
    data_source: Literal["database"] = "database"
