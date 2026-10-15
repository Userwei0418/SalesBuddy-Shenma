# ruff: noqa: S608 -- SQL identifiers/fragments are internal constants or validated catalog identifiers.
"""Personal saved views, rechecked against current grants on every request."""

from pydantic import ValidationError

from sales_backend.contracts.opportunity_views import COLUMNS, DEFAULT_COLUMNS, OpportunityViewConfig
from sales_backend.db import json_value
from sales_backend.domain.concurrency import require_version
from sales_backend.repositories.authorization_checks import require_permission

PROJECTION = """SELECT id,name,owner_user_ref_id,config,version_no,created_at,updated_at
 FROM crm.opportunity_saved_view
 WHERE workspace_id=common.current_workspace_id()
 AND owner_user_ref_id=common.current_user_ref_id() AND deleted_at IS NULL"""


def item(row):
    # Validate each stored preference independently: an obsolete schema or a
    # damaged object must not turn the entire list into a response-validation 500.
    # Only contract-validation failures are recoverable; permission/SQL failures
    # still propagate normally. Never expose raw rejected configuration in errors.
    try:
        config = OpportunityViewConfig.model_validate(json_value(row["config"]))
        valid, error = True, None
    except ValidationError:
        config = OpportunityViewConfig()
        valid, error = False, "视图配置已失效，已恢复默认列和筛选，请重新保存。"
    return {
        **dict(row),
        "config": config.model_dump(mode="json"),
        "config_valid": valid,
        "config_error": error,
        "can_edit": True,
        "can_delete": True,
    }


class OpportunityViewRepository:
    async def options(self, connection):
        await require_permission(connection, "opportunity.read")
        return {
            "columns": COLUMNS,
            "default_columns": DEFAULT_COLUMNS,
            "can_create_private": True,
            "definition_version": "shenma_opportunity_view_v1",
        }

    async def list(self, connection, *, limit=100, offset=0):
        await require_permission(connection, "opportunity.read")
        total = await connection.fetchval("SELECT count(*) FROM (" + PROJECTION + ") own_views")
        rows = await connection.fetch(
            PROJECTION + " ORDER BY updated_at DESC,id DESC LIMIT $1 OFFSET $2", limit, offset
        )
        return {
            "items": [item(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "data_source": "database",
        }

    async def get(self, connection, view_id, *, lock=False):
        await require_permission(connection, "opportunity.read")
        row = await connection.fetchrow(PROJECTION + " AND id=$1::uuid" + (" FOR UPDATE" if lock else ""), view_id)
        if row is None:
            raise LookupError("视图不存在或不在当前权限范围")
        return item(row)

    async def create(self, connection, actor, body):
        await require_permission(connection, "opportunity.read")
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
            f"opportunity-view:{actor.workspace_id}:{actor.user_id}",
        )
        count = await connection.fetchval("SELECT count(*) FROM (" + PROJECTION + ") own_views")
        if count >= 100:
            raise ValueError("最多保存 100 个个人商机视图，请先删除不再使用的视图")
        view_id = await connection.fetchval(
            "INSERT INTO crm.opportunity_saved_view(workspace_id,owner_user_ref_id,name,config) "
            "VALUES($1::uuid,$2::uuid,$3,$4::jsonb) RETURNING id",
            actor.workspace_id,
            actor.user_id,
            body.name,
            body.config.model_dump(mode="json"),
        )
        return await self.get(connection, view_id)

    async def update(self, connection, actor, view_id, body, *, delete=False):
        current = await self.get(connection, view_id, lock=True)
        require_version(current["version_no"], body.version_no)
        if delete:
            await connection.execute(
                "UPDATE crm.opportunity_saved_view SET deleted_at=clock_timestamp() "
                "WHERE id=$1::uuid AND workspace_id=common.current_workspace_id() "
                "AND owner_user_ref_id=common.current_user_ref_id()",
                view_id,
            )
            return {"id": str(view_id), "deleted": True, "version_no": current["version_no"] + 1}
        if body.config is None:
            # Name-only updates must preserve even an invalid stored config.
            # Only an explicit full-config save repairs an obsolete preference.
            await connection.execute(
                "UPDATE crm.opportunity_saved_view SET name=$2 "
                "WHERE id=$1::uuid AND workspace_id=common.current_workspace_id() "
                "AND owner_user_ref_id=common.current_user_ref_id()",
                view_id,
                body.name,
            )
        else:
            await connection.execute(
                "UPDATE crm.opportunity_saved_view SET name=$2,config=$3::jsonb "
                "WHERE id=$1::uuid AND workspace_id=common.current_workspace_id() "
                "AND owner_user_ref_id=common.current_user_ref_id()",
                view_id,
                body.name,
                body.config.model_dump(mode="json"),
            )
        return await self.get(connection, view_id)
