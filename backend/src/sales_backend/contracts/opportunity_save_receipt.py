"""Minimal confirmation of one direct opportunity save, never a new write intent."""

from sales_backend.contracts.collection_actuals import CollectionActualResult
from sales_backend.contracts.recognized_actuals import RecognizedActualResult
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StrictBool, model_validator


class OpportunitySaveResult(BaseModel):
    id: UUID
    customer_id: UUID
    operation: Literal["created", "updated", "unchanged"]
    changed: StrictBool
    version_no: Annotated[int, Field(strict=True, ge=1)]
    event_id: UUID | None = None
    recognized_actuals: list[RecognizedActualResult] | None = None
    collection_actuals: list[CollectionActualResult] | None = None

    @model_validator(mode="after")
    def complete_confirmation(self):
        if self.changed:
            if self.operation == "unchanged" or self.event_id is None:
                raise ValueError("A changed save requires its operation and event receipt")
        elif self.operation != "unchanged" or self.event_id is not None:
            raise ValueError("An unchanged save cannot claim a change event")
        return self


class OpportunitySaveCommitted(BaseModel):
    status: Literal["committed"] = "committed"
    request_key: UUID
    result: OpportunitySaveResult


class OpportunitySaveUnresolved(BaseModel):
    """Absence includes in-flight writes; it never authorizes a replacement key."""

    status: Literal["unresolved"] = "unresolved"
    request_key: UUID
    result: None = None


class OpportunitySaveCancelled(BaseModel):
    """The key is durably fenced; later arrivals cannot perform its business write."""

    status: Literal["cancelled"] = "cancelled"
    request_key: UUID
    result: None = None


OpportunitySaveReceipt = Annotated[
    OpportunitySaveCommitted | OpportunitySaveUnresolved | OpportunitySaveCancelled, Field(discriminator="status")
]
