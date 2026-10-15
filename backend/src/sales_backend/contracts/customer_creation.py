from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sales_backend.contracts.models import CustomerCreate
from sales_backend.contracts.customer_request_history import CustomerRequestHistorySummary
from sales_backend.domain.customer_creation_options import normalize_creation_source


class CustomerCreationRequestBody(CustomerCreate):
    """客户建档申请。公司编号可选；旧来源“自主拓展”规范为“销售自拓”，其他非规范选项需重新选择。"""

    model_config = ConfigDict(extra="forbid")

    @field_validator("source", mode="before")
    @classmethod
    def normalize_legacy_source(cls, value: object) -> object:
        return normalize_creation_source(value)


class CustomerCreationRequestDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approved", "rejected"]
    reason: str = Field(default="", max_length=2000)
    version_no: int | None = Field(default=None, ge=1)
    approved_payload: CustomerCreationRequestBody | None = None

    @model_validator(mode="after")
    def rejected_requires_reason(self):
        if self.decision == "rejected" and not self.reason.strip():
            raise ValueError("驳回客户新建申请必须填写原因")
        if self.decision != "approved" and self.approved_payload is not None:
            raise ValueError("只有批准时可以提交修正后的建档内容")
        return self


class CustomerCreationRequestItem(BaseModel):
    id: UUID
    status: Literal["pending", "approved", "rejected", "cancelled"]
    applicant_user_ref_id: UUID
    applicant_name: str | None = None
    reviewer_name: str | None = None
    payload: dict[str, Any]
    requested_at: datetime
    reviewed_at: datetime | None = None
    reviewer_user_ref_id: UUID | None = None
    decision_reason: str = ""
    created_customer_id: UUID | None = None
    reviewed_payload: dict[str, Any] | None = None
    version_no: int = 1


class CustomerCreationRequestPage(BaseModel):
    items: list[CustomerCreationRequestItem]
    total: int
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None = None
    summary: CustomerRequestHistorySummary
