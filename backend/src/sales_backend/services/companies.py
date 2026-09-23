"""Company selection and naming preserve explicit grants and optimistic versions."""

from sales_backend.domain.concurrency import VersionConflict
from sales_backend.repositories.companies import CompanyRepository


class CompanyService:
    def __init__(self):
        self.repository = CompanyRepository()

    async def directory(self, connection):
        return await self.repository.directory(connection)

    async def select(self, connection, actor, company_id):
        companies = await self.repository.directory(connection)
        selected = next((c for c in companies if c["id"] == str(company_id)), None)
        if selected is None:
            raise PermissionError("没有此公司的管理权限")
        await self.repository.record_selection(connection, actor, selected)
        return {"company": selected}

    async def rename(self, connection, actor, company_id, name, version_no):
        if str(company_id) != actor.workspace_id:
            raise PermissionError("请先切换至需要管理的公司")
        name = name.strip()
        if not name:
            raise ValueError("请填写公司名称")
        before = await self.repository.lock(connection, str(company_id))
        row = await self.repository.rename(connection, str(company_id), name, version_no)
        if not row:
            raise VersionConflict("公司信息已变化，请刷新后重试")
        await self.repository.record_rename(connection, actor, before, row)
        return dict(row)
