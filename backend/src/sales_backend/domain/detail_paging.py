"""Opaque seek positions for visit history; a position never grants authorization."""

import base64
import binascii
from datetime import date
from uuid import UUID
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError


class VisitPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: UUID
    opportunity_id: UUID | None
    interaction_at: AwareDatetime | None
    recorded_date: date
    id: UUID


class CreatedVisitPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sort: Literal["created_desc"]
    customer_id: UUID
    opportunity_id: UUID | None
    created_at: AwareDatetime
    id: UUID


def visit_cursor(row, customer_id, opportunity_id, *, sort=None):
    if sort == "created_desc":
        position = CreatedVisitPosition(
            sort=sort,
            customer_id=customer_id,
            opportunity_id=opportunity_id,
            created_at=row["created_at"],
            id=row["id"],
        )
        return base64.urlsafe_b64encode(position.model_dump_json().encode()).decode().rstrip("=")
    position = VisitPosition(
        customer_id=customer_id,
        opportunity_id=opportunity_id,
        interaction_at=row["interaction_at"],
        recorded_date=row["created_date"],
        id=row["id"],
    )
    return base64.urlsafe_b64encode(position.model_dump_json().encode()).decode().rstrip("=")


def read_visit_cursor(value, customer_id, opportunity_id, *, sort=None):
    if not value:
        return None
    try:
        if len(value) > 1024:
            raise ValueError("invalid cursor")
        payload = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        position = (CreatedVisitPosition if sort == "created_desc" else VisitPosition).model_validate_json(payload)
        if position.customer_id != UUID(str(customer_id)) or position.opportunity_id != (
            UUID(str(opportunity_id)) if opportunity_id else None
        ):
            raise ValueError("cursor subject mismatch")
        return position
    except (ValueError, ValidationError, binascii.Error) as error:
        raise ValueError("拜访分页位置无效，请重新加载") from error
