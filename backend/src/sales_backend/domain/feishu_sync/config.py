"""Secret-free, tenant-bound configuration. No SQL or expressions in mappings."""
from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

RemoteId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,160}$")]
ChatId = Annotated[str, Field(pattern=r"^oc_[A-Za-z0-9]+$")]
FieldId = Annotated[str, Field(pattern=r"^fld[A-Za-z0-9]+$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObjectKind(StrEnum):
    CUSTOMER = "customer"
    OPPORTUNITY = "opportunity"
    VISIT = "visit"
    PARTNER = "partner"
    TASK = "task"
    DEMO_SCENE = "demo_scene"
    FORECAST = "forecast"
    ACTUAL = "actual"
    PERIOD_ACTUAL_SNAPSHOT = "period_actual_snapshot"
    TARGET = "target"
    MEMBER = "member"
    CONTACT = "contact"


# Projection field names, not DB column names. Adapters must explicitly produce these.
COMMON_FIELDS = frozenset({"system_id", "source_created_at", "source_updated_at", "record_status",
                           "company_name", "source_version", "synced_at"})
SOURCE_FIELDS = {
    ObjectKind.CUSTOMER: {"name", "industry", "customer_type", "priority", "main_business", "needs",
                          "budget", "next_action", "owner_id", "department_id", "owner_name",
                          "owner_account", "department_name", "lifecycle_status", "fde_members", "sales_members",
                          "potential_score", "relationship_score", "quadrant_code", "risk_title"},
    ObjectKind.OPPORTUNITY: {"name", "customer_id", "stage", "amount", "currency", "probability",
                             "original_created_at_raw", "original_created_at_source",
                             "expected_close_date", "expected_close_year", "expected_close_quarter",
                             "associated_partner_ids", "associated_partner_names",
                             "original_owner_name", "ownership_resolution", "status", "product_line",
                             "sales_channel", "partner_id", "customer_name", "owner_name", "owner_account",
                             "department_name", "fde_members", "grade", "follow_up_plan", "partner_name"},
    ObjectKind.VISIT: {"customer_id", "opportunity_id", "content", "next_action", "interaction_at",
                       "archived_at", "recorder_id", "contact_name", "contact_title", "interaction_mode",
                       "name", "customer_name", "opportunity_name", "owner_name", "owner_account",
                       "department_name", "visit_goal", "visit_location", "contact_role", "partner_name",
                       "is_first_visit", "follow_up_score", "collaborators", "partner_id",
                       "original_recorder_name", "manager_user_ref_id", "manager_name", "opportunity_ids",
                       "opportunity_names", "customer_ids", "customer_names"},
    ObjectKind.PARTNER: {"name", "status", "short_name", "principal_name", "channel_manager_user_ref_id",
                         "original_channel_manager_name", "priority", "progress", "grade", "signed_on",
                         "partner_type", "region", "province", "note"},
    ObjectKind.TASK: {"title", "description", "status", "due_at", "creator_id", "owner_id",
                      "customer_id", "opportunity_id", "completion_note", "review_note"},
    ObjectKind.DEMO_SCENE: {"name", "description", "opportunity_id", "creator_id"},
    ObjectKind.FORECAST: {"customer_id", "customer_name", "opportunity_id", "opportunity_name",
                          "year", "quarter", "recognized_amount", "collection_amount",
                          "collection_confidence", "updated_by"},
    ObjectKind.PERIOD_ACTUAL_SNAPSHOT: {"customer_id", "customer_name", "opportunity_id", "opportunity_name",
                          "year", "quarter", "kind", "source_field", "raw_amount", "source_unit", "tax_basis",
                          "source_system", "source_base_id", "source_table_id", "source_external_record_id"},
    ObjectKind.ACTUAL: {"kind", "amount", "occurred_on", "customer_id", "opportunity_id",
                        "source_ref", "note", "confirmed_by", "voided_at", "void_reason"},
    ObjectKind.TARGET: {"period_type", "period_start", "period_end", "scope_type", "user_id",
                        "team_id", "team_name", "department_code", "kind", "amount"},
    ObjectKind.MEMBER: {"name", "account", "status", "primary_department_id", "department_ids",
                        "primary_department_name", "department_names", "roles",
                        "organization_team_id", "organization_team_name"},
    ObjectKind.CONTACT: {"name", "title", "role", "customer_id", "is_primary"},
}
NOTIFIABLE = frozenset({ObjectKind.CUSTOMER, ObjectKind.OPPORTUNITY, ObjectKind.VISIT})


class TableMapping(StrictModel):
    enabled: bool = False
    table_id: Annotated[str, Field(pattern=r"^tbl[A-Za-z0-9]+$")]
    id_field_id: FieldId
    fields: dict[str, FieldId] = Field(default_factory=dict)
    archive_policy: Literal["mark_status"] = "mark_status"

    @model_validator(mode="after")
    def unique_targets(self):
        values = [self.id_field_id, *self.fields.values()]
        if len(values) != len(set(values)):
            raise ValueError("多个源字段不能写入同一个目标字段")
        if "system_id" in self.fields:
            raise ValueError("系统ID必须通过id_field_id单独映射")
        return self


class DepartmentRoute(StrictModel):
    department_id: UUID
    chat_ids: tuple[ChatId, ...] = Field(min_length=1, max_length=20)


class NotificationConfig(StrictModel):
    enabled: bool = False
    routing_mode: Literal["single_group", "department_routes"] = "single_group"
    default_chat_id: ChatId | None = None
    on_create: frozenset[ObjectKind] = NOTIFIABLE
    routes: tuple[DepartmentRoute, ...] = ()

    @model_validator(mode="after")
    def validate_routes(self):
        if self.on_create - NOTIFIABLE:
            raise ValueError("当前只允许客户、商机、归档跟进的新增通知")
        if self.enabled and not self.default_chat_id:
            raise ValueError("启用通知需要配置默认群")
        departments = [route.department_id for route in self.routes]
        if len(departments) != len(set(departments)):
            raise ValueError("同一部门只能配置一条路由")
        return self


class SyncConfig(StrictModel):
    schema_version: Literal[1] = 1
    workspace_id: UUID
    connection_id: UUID
    revision: int = Field(ge=1)
    provider: Literal["feishu"] = "feishu"
    enabled: bool = False
    app_id: RemoteId
    credential_ref: UUID
    base_token: RemoteId
    base_url: str | None = Field(default=None, max_length=300)
    direction: Literal["system_to_base", "bidirectional"] = "system_to_base"
    mappings: dict[ObjectKind, TableMapping] = Field(default_factory=dict)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)

    @model_validator(mode="after")
    def validate_mappings(self):
        if self.base_url:
            url = urlsplit(self.base_url)
            if (url.scheme != "https" or not url.hostname or not url.hostname.endswith(".feishu.cn")
                    or url.username or url.password or url.port not in (None, 443)
                    or url.path != f"/base/{self.base_token}" or url.query or url.fragment):
                raise ValueError("详情链接须为目标飞书Base的HTTPS地址，不含查询参数或凭证")
        if self.notification.enabled and not self.base_url:
            raise ValueError("启用通知需要填写Base详情链接")
        tables = []
        for kind, mapping in self.mappings.items():
            unknown = set(mapping.fields) - SOURCE_FIELDS[kind] - COMMON_FIELDS
            if unknown:
                raise ValueError(f"{kind}: 未支持的源字段 {', '.join(sorted(unknown))}")
            tables.append(mapping.table_id)
            if mapping.enabled and "record_status" not in mapping.fields:
                raise ValueError("启用对象必须映射记录状态，以同步归档/作废")
        if len(tables) != len(set(tables)):
            raise ValueError("不同业务对象不能共享同一目标表")
        if self.enabled and not any(m.enabled for m in self.mappings.values()):
            raise ValueError("启用连接至少需要一个启用的业务对象")
        return self
