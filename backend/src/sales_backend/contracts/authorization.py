"""Validated console inputs for permission templates and account overrides."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from sales_backend.domain.permission_catalog import CATALOG


class ScopedPermission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permission: str
    scope: Literal["inherit", "self", "assigned", "teams", "workspace", "departments"] = "inherit"
    team_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def supported_scope(self):
        definition = CATALOG.get(self.permission)
        if definition is None:
            raise ValueError("未知功能权限")
        if self.scope != "inherit" and self.scope not in definition.scopes:
            raise ValueError("此功能不支持选择的数据范围")
        if len(self.team_ids) != len(set(self.team_ids)):
            raise ValueError("授权团队不能重复")
        if (self.scope == "teams") != bool(self.team_ids):
            raise ValueError("指定团队范围必须选择团队，其他范围不能附带团队")
        return self


class RoleSave(BaseModel):
    expires_at: AwareDatetime | None = None

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    status: Literal["active", "inactive"] = "active"
    permissions: list[ScopedPermission] = Field(default_factory=list, max_length=300)
    version_no: int | None = Field(None, ge=1)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_permissions(self):
        if not self.name.strip() or not self.reason.strip():
            raise ValueError("请填写角色名称和变更原因")
        codes = [permission.permission for permission in self.permissions]
        if len(codes) != len(set(codes)):
            raise ValueError("同一角色不能重复配置功能权限")
        # Fixed-scope functions cannot inherit an incompatible account range.
        # Normalize only role saves; account denies must keep scope="inherit".
        self.permissions = [
            permission.model_copy(update={"scope": CATALOG[permission.permission].scopes[0]})
            if permission.scope == "inherit" and len(CATALOG[permission.permission].scopes) == 1
            else permission
            for permission in self.permissions
        ]
        return self


class AccountRoleAssignment(BaseModel):
    expires_at: AwareDatetime | None = None

    model_config = ConfigDict(extra="forbid")

    role_id: UUID
    scope: Literal["self", "assigned", "teams", "workspace"]
    team_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def teams_match(self):
        if len(self.team_ids) != len(set(self.team_ids)):
            raise ValueError("授权团队不能重复")
        if (self.scope == "teams") != bool(self.team_ids):
            raise ValueError("角色授权的指定团队不能为空，其他范围不能附带团队")
        return self


class AccountPermissionOverride(ScopedPermission):
    expires_at: AwareDatetime | None = None

    effect: Literal["allow", "deny"]

    @model_validator(mode="after")
    def explicit_scope(self):
        if self.effect == "allow" and self.scope in {"inherit", "departments"}:
            raise ValueError("单账号额外授权必须明确数据范围")
        if self.effect == "deny" and (self.scope != "inherit" or self.team_ids):
            raise ValueError("单账号禁用针对整个功能，不附带数据范围")
        return self


class AccountAuthorizationSave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: list[AccountRoleAssignment] = Field(default_factory=list, max_length=100)
    overrides: list[AccountPermissionOverride] = Field(default_factory=list, max_length=300)
    version_no: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_assignments(self):
        if not self.reason.strip():
            raise ValueError("请填写权限变更原因")
        codes = [permission.permission for permission in self.overrides]
        if len(codes) != len(set(codes)):
            raise ValueError("同一功能不能同时增加和禁用，也不能重复配置")
        roles = [role.role_id for role in self.roles]
        if len(roles) != len(set(roles)):
            raise ValueError("账号不能重复绑定同一权限角色")
        return self


PROTECTED_COMPANY_ACTIONS = frozenset(
    {
        "authorization.roles_manage",
        "authorization.accounts_manage",
        "account.create",
        "account.password_policy",
        "organization.manage",
    }
)


class DelegationPermission(ScopedPermission):
    @model_validator(mode="after")
    def explicit_ceiling(self):
        if self.scope == "inherit":
            raise ValueError("委派上限必须明确数据范围")
        if self.permission in PROTECTED_COMPANY_ACTIONS:
            raise ValueError("公司安全管理权限不能再次转授")
        return self


class DelegationSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    expires_at: AwareDatetime | None = None
    recipient_ids: list[UUID] = Field(default_factory=list, max_length=2000)
    permissions: list[DelegationPermission] = Field(default_factory=list, max_length=300)
    version_no: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_entries(self):
        if len(self.recipient_ids) != len(set(self.recipient_ids)):
            raise ValueError("委派对象不能重复")
        codes = [g.permission for g in self.permissions]
        if len(codes) != len(set(codes)):
            raise ValueError("同一功能的委派上限不能重复")
        if not self.reason.strip():
            raise ValueError("请填写委派变更原因")
        return self
