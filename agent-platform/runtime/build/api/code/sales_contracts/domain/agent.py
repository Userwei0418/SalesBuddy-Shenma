from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

class RoleCode(StrEnum):
    SALES = "sales"
    SUPERVISOR = "supervisor"
    MANAGER = "manager"
    FDE = "fde"
    FDE_LEAD = "fde_lead"
    OPERATIONS = "operations"
    ADMINISTRATOR = "administrator"

class DataScope(StrEnum):
    SELF = "self"
    TEAM = "team"
    WORKSPACE = "workspace"

class ActorContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    workspace_id: str
    user_id: str
    role: RoleCode
    data_scope: DataScope
    team_ids: tuple[str, ...] = ()
