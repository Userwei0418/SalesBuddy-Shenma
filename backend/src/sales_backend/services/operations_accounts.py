import asyncio

from sales_backend.auth.passwords import encode_password
from sales_backend.domain.concurrency import require_version
from sales_backend.repositories.operations_accounts import OperationsAccountRepository


def require_assignable_roles(actor, roles):
    if actor.role.value not in {"operations", "administrator"}:
        raise PermissionError("需要账号管理权限")
    if actor.role.value != "administrator" and set(roles) & {"operations", "administrator"}:
        raise PermissionError("运营及系统管理员账号由系统管理员管理")


class OperationsAccountService:
    def __init__(self):
        self.repository = OperationsAccountRepository()

    async def validate_organization(self, connection, data):
        if data.get("organization_team_id") is not None:
            await self.repository.validate_team(connection, data["organization_team_id"])
        company_roles = data.get("company_roles") or []
        if set(company_roles) - {"operations", "administrator"}:
            raise ValueError("公司管理权限仅支持运营及管理员")
        memberships = data.get("memberships")
        if memberships is None:
            memberships = [{"team_id": data["team_id"], "roles": [r for r in data["roles"] if r not in company_roles]}] if data.get("team_id") else []
        data["memberships"] = memberships
        data["company_roles"] = company_roles
        teams = [str(m["team_id"]) for m in memberships]
        if len(teams) != len(set(teams)) or (teams and str(data.get("team_id")) not in teams) or (not teams and data.get("team_id") is not None):
            raise ValueError("部门不能重复，主部门必须在任职部门中")
        if not data["roles"] or set(data["roles"]) != {r for m in memberships for r in m["roles"]} | set(company_roles):
            raise ValueError("账号角色必须与各部门岗位一致，并包含公司管理权限")
        for membership in memberships:
            if not membership["roles"]:
                raise ValueError("每个任职部门至少需要一个岗位")
            await self.repository.validate_team(connection, membership["team_id"])

    async def create(self, connection, actor, data, password):
        require_assignable_roles(actor, data["roles"])
        if not data["display_name"].strip():
            raise ValueError("请填写姓名")
        await self.repository.lock_workspace(connection, actor.workspace_id)
        await self.validate_organization(connection, data)
        encoded = await asyncio.to_thread(encode_password, password)
        result = await self.repository.create(connection, actor, data)
        await self.repository.set_password(connection, result["id"], encoded)
        policy = await self.repository.password_policy(connection)
        return {**result, "must_change_password": policy["require_initial_change"]}

    async def update(self, connection, actor, uid, data):
        require_assignable_roles(actor, data["roles"])
        if not data["display_name"].strip():
            raise ValueError("请填写姓名")
        await self.repository.lock_workspace(connection, actor.workspace_id)
        current = await self.repository.member(connection, uid)
        if current.get("platform_managed"):
            raise PermissionError("平台管理身份由公司授权维护，不能在成员列表修改")
        require_assignable_roles(actor, current["roles"])
        require_version(current["version_no"], data["version_no"])
        if (
            "administrator" in current["roles"]
            and current["status"] == "active"
            and (data["status"] != "active" or "administrator" not in data["roles"])
            and await self.repository.administrator_count(connection) <= 1
        ):
            raise ValueError("必须保留至少一位有效系统管理员")
        if data.get("company_roles") is None:
            data = {**data, "company_roles": current["company_roles"]}
        if data.get("memberships") is None:
            previous = await self.repository.assignments(connection, uid)
            if len(previous) > 1:
                primary = next((m["team_id"] for m in previous if m["is_primary"]), None)
                if str(data.get("team_id")) != primary or set(data["roles"]) != set(current["roles"]):
                    raise ValueError("此账号兼任多个部门，请刷新后台后按部门维护岗位")
                data = {
                    **data,
                    "memberships": [
                        {"team_id": m["team_id"], "roles": m["roles"], "acting": m.get("acting", False)}
                        for m in previous
                    ],
                }
        await self.validate_organization(connection, data)
        return await self.repository.update(connection, actor, uid, data)

    async def reset_password(self, connection, actor, uid, version, password):
        await self.repository.lock_workspace(connection, actor.workspace_id)
        current = await self.repository.member(connection, uid)
        if current.get("platform_managed"):
            raise PermissionError("平台管理身份由公司授权维护，不能在成员列表修改")
        require_assignable_roles(actor, current["roles"])
        require_version(current["version_no"], version)
        encoded = await asyncio.to_thread(encode_password, password)
        await self.repository.set_password(connection, uid, encoded)
        version = await self.repository.bump_version(connection, uid)
        policy = await self.repository.password_policy(connection)
        return {
            "id": str(uid),
            "version_no": version,
            "must_change_password": policy["require_initial_change"],
            **await self.repository.login_status(connection, uid),
        }

    async def rename_account(self, connection, actor, uid, version, account_code, email):
        """Maintenance rename only: keep roles, memberships and password untouched."""
        from pydantic import TypeAdapter
        from sales_backend.contracts.operations import AccountCode, AccountContact
        account_code = TypeAdapter(AccountCode).validate_python(account_code)
        # Reuse the complete email field contract, including normalization.
        email = AccountContact(email=email).email
        await self.repository.lock_workspace(connection, actor.workspace_id)
        current = await self.repository.member(connection, uid)
        if current.get("platform_managed"):
            raise PermissionError("平台管理身份不能批量更名")
        require_assignable_roles(actor, current["roles"])
        require_version(current["version_no"], version)
        return await self.repository.rename_account(connection, actor, uid, account_code, email)

    async def unlock_login(self, connection, actor, uid, version, reason):
        reason = reason.strip()
        if not reason or len(reason) > 500:
            raise ValueError("请填写解除原因（1–500 字）")
        await self.repository.lock_workspace(connection, actor.workspace_id)
        current = await self.repository.member(connection, uid)
        if current.get("platform_managed"):
            raise PermissionError("平台管理身份由公司授权维护，不能在成员列表修改")
        require_assignable_roles(actor, current["roles"])
        require_version(current["version_no"], version)
        await self.repository.unlock_login(connection, uid, reason)
        version = await self.repository.bump_version(connection, uid)
        return {"id": str(uid), "version_no": version, **await self.repository.login_status(connection, uid)}
