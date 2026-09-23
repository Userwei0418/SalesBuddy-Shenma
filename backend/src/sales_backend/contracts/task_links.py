from pydantic import BaseModel

from sales_backend.contracts.types import UUIDString


class TaskLinkChoice(BaseModel):
    id: UUIDString
    name: str
    customer_id: UUIDString | None = None


class TaskLinkPage(BaseModel):
    items: list[TaskLinkChoice]
    has_more: bool
    next_offset: int | None
