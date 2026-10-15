from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from sales_backend.contracts.models import CustomerCreate


class CustomerCreationRequestBody(CustomerCreate):
    """销售提交的客户快照；审批前不写入 crm.customer。"""

    company_reference: str = Field(min_length=1, max_length=200)


class CustomerCreationRequestDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def rejected_requires_reason(self):
        if self.decision == "rejected" and not self.reason.strip():
            raise ValueError("驳回客户新建申请必须填写原因")
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


class CustomerCreationRequestPage(BaseModel):
    items: list[CustomerCreationRequestItem]
    total: int
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None = None


class CustomerCreationRequestResult(CustomerCreationRequestItem):
    customer: dict[str, Any] | None = None
