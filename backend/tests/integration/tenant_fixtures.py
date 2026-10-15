"""An existing foreign tenant is distinct from a nonexistent/closed tenant."""
from uuid import uuid4

from tests.integration.feishu_fixtures import seed_fetchval


async def active_foreign_workspace(connection):
    identifier=uuid4()
    return str(await seed_fetchval(connection,
        "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'隔离外国公司') RETURNING id",
        identifier,'foreign-'+identifier.hex))
