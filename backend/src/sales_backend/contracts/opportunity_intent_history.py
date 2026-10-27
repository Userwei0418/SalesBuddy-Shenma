"""Only recorded intent product-line changes; no finance or current catalog data."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class IntentHistoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntentHistoryLabel(IntentHistoryModel):
    id: str
    code: str | None
    name: str | None
    is_active: bool | None
    status: Literal["recorded", "unavailable"]


class IntentHistorySnapshot(IntentHistoryModel):
    version: Literal[1]
    basis: Literal["directory_at_change"]
    before: list[IntentHistoryLabel]
    after: list[IntentHistoryLabel]


class IntentHistoryField(IntentHistoryModel):
    field: Literal["intent_product_line_ids"]
    label: str
    before: list[str] | None = Field(description="保存时的ID列表；null表示原值缺失，不等于空选择。")
    after: list[str] | None
    display_snapshot: IntentHistorySnapshot | None = Field(
        description="仅保存时且与原ID完整匹配的名称快照；缺失或无效为null，客户端显示名称未记录和ID。"
    )


class IntentHistoryActor(IntentHistoryModel):
    id: str | None
    name: str | None


class IntentHistoryChange(IntentHistoryModel):
    id: str
    version_no: int
    created_at: datetime
    actor: IntentHistoryActor
    fields: list[IntentHistoryField]
    detail_status: Literal["recorded", "unavailable"]


class IntentHistoryChanges(IntentHistoryModel):
    items: list[IntentHistoryChange]
    limit: int
    offset: int
    has_more: bool
    next_offset: int | None
    order: Literal["created_at_desc_id_desc"]
    coverage_note: str


class OpportunityIntentHistory(IntentHistoryModel):
    schema_version: Literal[1]
    opportunity_id: UUID
    field: Literal["intent_product_line_ids"]
    history_revision: str = Field(pattern=r"^[0-9a-f]{64}$", description="绑定当前身份、商机和该字段历史的分页摘要。")
    changes: IntentHistoryChanges
