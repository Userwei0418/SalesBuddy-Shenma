"""Explicit platform operational metadata; never an arbitrary row projection."""
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class JobCounts(RuntimeModel):
    running: int = Field(ge=0)
    expired_leases: int = Field(ge=0)
    waiting: int = Field(ge=0)
    overdue: int = Field(ge=0)
    dead_letter: int = Field(ge=0)


class SyncCounts(RuntimeModel):
    enabled: bool
    waiting: int = Field(ge=0)
    overdue: int = Field(ge=0)
    expired_leases: int = Field(ge=0)
    dead_letter: int = Field(ge=0)
    unknown_deliveries: int = Field(ge=0)


class OriginalCounts(RuntimeModel):
    available: bool
    limit_bytes: int | None = Field(ge=0)
    stored_bytes: int | None = Field(ge=0)
    reserved_bytes: int | None = Field(ge=0)
    over_limit: bool
    pending: int | None = Field(ge=0)
    overdue: int | None = Field(ge=0)


class CallCounts(RuntimeModel):
    pending: int = Field(ge=0)
    overdue: int = Field(ge=0)
    unknown: int = Field(ge=0)
    failed_last_24h: int = Field(ge=0)


class TenantRuntime(RuntimeModel):
    workspace_id: UUID
    name: str
    company_code: str
    status: Literal['active', 'provisioning', 'suspended', 'closed']
    jobs: JobCounts
    sync: SyncCounts
    originals: OriginalCounts
    calls: CallCounts


class PlatformRuntimePage(RuntimeModel):
    sampled_at: AwareDatetime
    wait_minutes: int = Field(ge=1, le=1440)
    next_cursor: UUID | None
    items: list[TenantRuntime] = Field(max_length=20)
