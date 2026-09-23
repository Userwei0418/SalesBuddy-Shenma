"""Reference-only company directory; separate from customer detail permissions."""

from pydantic import BaseModel, Field

from sales_backend.contracts.types import UUIDString


class CustomerDirectoryItem(BaseModel):
    id: UUIDString
    name: str
    industry_code: str | None
    level_code: str | None
    customer_type_code: str | None
    team_name: str | None
    owner_name: str | None
    ownership_state: str
    claimed: bool | None
    can_claim: bool
    claim_status: str | None


class CustomerDirectoryPage(BaseModel):
    items: list[CustomerDirectoryItem]
    total: int = Field(ge=0, description="当前搜索条件内的公司目录总数，包含已认领及待审批客户")
    has_more: bool
    next_offset: int | None = Field(default=None, ge=0)
