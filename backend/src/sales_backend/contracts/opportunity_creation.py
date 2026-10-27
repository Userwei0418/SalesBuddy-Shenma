"""Customer-side creation capability; owner/team scopes are checked on save."""
from typing import Literal
from pydantic import BaseModel
from sales_backend.contracts.types import UUIDString


class OpportunityCreateCustomer(BaseModel):
    id: UUIDString
    name: str


class OpportunityCreateCustomers(BaseModel):
    items: list[OpportunityCreateCustomer]
    total: int
    limit: int
    offset: int


class OpportunityCreateAccess(BaseModel):
    customer_reference: OpportunityCreateCustomer | None
    can_create_opportunity: bool
    denial_reason: Literal["OPPORTUNITY_CREATE_FORBIDDEN", "OPPORTUNITY_CUSTOMER_NOT_FOUND",
        "OPPORTUNITY_CUSTOMER_CLAIM_REQUIRED", "OPPORTUNITY_CUSTOMER_CLAIM_SCOPE_DENIED"] | None
