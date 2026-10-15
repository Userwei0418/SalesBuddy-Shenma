"""Small business selectors; these never expose the organization management directory."""
from pydantic import BaseModel, Field


class BusinessChoice(BaseModel):
    id: str
    name: str


class TargetPersonChoice(BusinessChoice):
    account_code: str | None = None


class CustomerCreateOptions(BaseModel):
    teams: list[BusinessChoice] = Field(description="当前公司有效且允许新建客户的业务团队；不要求组织目录权限")


class TargetManagementOptions(BaseModel):
    people: list[TargetPersonChoice] = Field(description="同时允许目标读取与运营维护的有效成员，仅返回姓名与账号")
    teams: list[BusinessChoice] = Field(description="同时允许目标读取与运营维护的有效团队")
    departments: list[BusinessChoice] = Field(description="允许维护的部门汇总；FDE 部门仍单独校验权限")
