from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from sales_backend.domain.feishu_sync.config import SyncConfig


class SaveFeishuConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    config: SyncConfig
    migrate_target: bool = False
    app_secret: SecretStr | None = Field(default=None, repr=False)


class FeishuAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class FeishuRecovery(FeishuAction):
    event_id: UUID
    action: Literal["retry", "confirm_sent", "suppress"]
    delivery_key: str | None = Field(default=None, max_length=128)
    note: str = Field(min_length=1, max_length=1000)
