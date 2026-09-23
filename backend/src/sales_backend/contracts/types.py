"""Reusable input types; repositories continue receiving canonical strings."""

from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, Field


def _uuid_string(value: str) -> str:
    return str(UUID(value))


UUIDString = Annotated[
    str,
    AfterValidator(_uuid_string),
    Field(json_schema_extra={"format": "uuid"}),
]
