from typing import Literal, Annotated
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, model_validator, field_validator

Role = Literal["sales", "supervisor", "manager", "fde", "fde_lead", "operations", "administrator"]


class DepartmentMembership(BaseModel):
    team_id: UUID
    roles: list[Role] = Field(min_length=1, max_length=7)
    acting: bool = False


class AccountContact(BaseModel):
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$", description="可选联系邮箱，独立于账号名，不作为登录别名。更新时省略则保留，null或空字符串清空。")

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return (value.strip() or None) if isinstance(value, str) else value


class AccountOrganization(AccountContact):
    organization_team_id: UUID | None = Field(default=None, description="组织归属部门，仅用于组织管理，不产生业务任职或权限。更新时省略保留，null清空。")
    phone_number: str | None = Field(default=None, pattern=r"^1[3-9][0-9]{9}$", description="可选11位手机号登录标识；与账号名共用密码，不发送验证码。传null或空字符串清空；更新时省略则保留。")

    @field_validator("phone_number", mode="before")
    @classmethod
    def normalize_phone(cls, value):
        return (value.strip() or None) if isinstance(value, str) else value

    team_id: UUID | None = None
    company_roles: list[Literal["operations", "administrator"]] | None = Field(
        default=None, max_length=2, description="公司级管理权限，不创建业务团队任职；更新时省略则保留。")
    roles: list[Role] = Field(min_length=1, max_length=7)
    memberships: list[DepartmentMembership] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def valid_memberships(self):
        if self.memberships is not None:
            teams = [m.team_id for m in self.memberships]
            if len(teams) != len(set(teams)) or (teams and self.team_id not in teams) or (not teams and self.team_id is not None):
                raise ValueError("部门不能重复，主部门必须在任职部门中")
            assigned = {r for m in self.memberships for r in m.roles}
            if self.company_roles is not None and set(self.roles) != assigned | set(self.company_roles):
                raise ValueError("账号角色必须与各部门岗位一致，并包含公司管理权限")
            if self.company_roles is None and (assigned - set(self.roles) or set(self.roles) - assigned - {"administrator", "operations"}):
                raise ValueError("账号角色必须与各部门岗位一致")
        return self


AccountCode = Annotated[str, Field(min_length=3, max_length=64, pattern=r"^(?:[A-Za-z0-9._-]{3,64}|[A-Za-z0-9._%+-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,})$")]


class AccountCreate(AccountOrganization):
    account_code: AccountCode
    display_name: str = Field(min_length=1, max_length=100)
    temporary_password: SecretStr = Field(description="8–128 个字符，不限制字符组合；是否须修改由公司登录策略决定。")


class AccountUpdate(AccountOrganization):
    account_code: AccountCode | None = None
    version_no: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=100)
    status: Literal["active", "inactive"]


class PasswordReset(BaseModel):
    version_no: int = Field(ge=1)
    temporary_password: SecretStr = Field(description="8–128 个字符，不限制字符组合；重置后须重新登录；是否须修改由公司登录策略决定。")


class PasswordPolicyUpdate(BaseModel):
    version_no: int = Field(ge=0)
    require_initial_change: bool = Field(description="是否要求仍使用初始或重置密码的账号登录后修改密码。")


class AccountLoginUnlock(BaseModel):
    version_no: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class DepartmentSave(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    name: str = Field(min_length=1, max_length=100)
    parent_team_id: UUID | None = None
    status: Literal["active", "inactive"] = "active"
    version_no: int | None = Field(default=None, ge=1)
    kind: Literal["sales", "product_sales", "fde", "general"] | None = None
