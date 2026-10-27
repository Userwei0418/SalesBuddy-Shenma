from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, SecretStr


class PasswordLogin(BaseModel):
    account_code: str = Field(min_length=3, max_length=64)
    password: SecretStr
    workspace: str | None = Field(default=None, max_length=128)
    role: Literal["sales", "supervisor", "manager", "fde", "fde_lead", "operations", "administrator"] | None = None


class NativePasswordLogin(PasswordLogin):
    restricted_session_version: Literal[1] | None = Field(
        default=None, description="客户端是否支持独立只读会话；仅用于协议兼容，不授予权限。",
    )
    login_type: Literal["auto", "phone", "account"] = Field(
        default="auto",
        description=(
            "phone 仅匹配已绑定手机号；account 仅匹配账号名；auto 兼容旧客户端，匹配不唯一时拒绝登录。"
            "公司入口可选，不按密码尝试多个公司。"
        ),
    )


class PasswordChange(BaseModel):
    old_password: SecretStr
    new_password: SecretStr = Field(description="8–128 个字符，不限制字符组合；须与当前密码不同。")


class DemoLoginRequest(BaseModel):
    account_code: str = Field(min_length=3, max_length=64)
    workspace: str | None = Field(default=None, min_length=3, max_length=128)
    client: dict[str, str] = Field(default_factory=dict)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=256)


class TaskExecutionAvailability(BaseModel):
    protocol_version: Literal[1, 2] = 1
    available: bool
    legacy_available: bool = True
    supported_versions: list[Literal[1, 2, 3]] = Field(default_factory=lambda: [1])
    default_creation_mode: Literal["legacy", "execution_v1", "execution_v2", "execution_v3"] = "legacy"
    creation_enabled: bool = False
    v1_creation_enabled: bool = False
    v2_creation_enabled: bool = False
    v3_creation_enabled: bool = False
    checklist_owner_editable: bool = False


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
    company_code: str | None = None
    company_name: str | None = None
    capabilities: dict[str, bool | TaskExecutionAvailability] = Field(default_factory=dict)
    permissions: dict[str, bool] = Field(default_factory=dict)
    permission_grants: list[dict] = Field(default_factory=list)
    client_export_permissions: dict[str, bool] = Field(default_factory=dict)
    permission_version: str = ""


class CompanyEntry(BaseModel):
    company_code: str
    company_name: str


class SessionResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_at: datetime
    actor: ActorResponse
    auth_method: Literal["demo", "password"] = "demo"
    must_change_password: bool = False


class RestrictedCompany(BaseModel):
    id: str
    code: str
    name: str


class RestrictedSessionInfo(BaseModel):
    session_kind: Literal["demo_readonly"] = "demo_readonly"
    identity_id: str
    session_id: str
    account_code: str
    expires_at: datetime
    company: RestrictedCompany
    data_version: str
    modules: list[Literal["customers", "opportunities", "follow-ups", "tasks"]]


class RestrictedSessionResponse(RestrictedSessionInfo):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
