from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, SecretStr


class PasswordLogin(BaseModel):
    account_code: str = Field(min_length=3, max_length=64)
    password: SecretStr
    workspace: str | None = Field(default=None, max_length=128)
    role: Literal["sales", "supervisor", "manager", "fde", "fde_lead", "operations", "administrator"] | None = None


class PasswordChange(BaseModel):
    old_password: SecretStr
    new_password: SecretStr = Field(description="8–128 个字符，不限制字符组合；须与当前密码不同。")


class DemoLoginRequest(BaseModel):
    account_code: str = Field(min_length=3, max_length=64)
    workspace: str | None = Field(default=None, min_length=3, max_length=128)
    client: dict[str, str] = Field(default_factory=dict)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=256)


class ActorResponse(BaseModel):
    workspace_id: str
    user_id: str
    account_code: str | None
    display_name: str
    role: str
    role_name: str
    data_scope: str
    scope_name: str
    team_ids: list[str]
    team_names: list[str]
    capabilities: dict[str, bool] = Field(default_factory=dict)
    permission_version: str = ""


class SessionResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_at: datetime
    actor: ActorResponse
    auth_method: Literal["demo", "password"] = "demo"
    must_change_password: bool = False
