"""Human acknowledgement of a suggestion is separate from risk resolution."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sales_backend.contracts.types import UUIDString


class RiskDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["adopt", "defer"]
    note: str = Field(min_length=1, max_length=2000)
    version_no: int = Field(ge=1, strict=True)
    request_id: UUIDString

    @field_validator("note")
    @classmethod
    def meaningful_note(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("请填写建议处理说明")
        return value
