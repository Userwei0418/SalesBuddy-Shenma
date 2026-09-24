"""Registered Demo deliverables; all visibility remains opportunity scoped."""

from sales_backend.domain.concurrency import require_version
from sales_backend.repositories.authorization_checks import require_permission


class DemoSceneRepository:
    async def opportunity(self, connection, opportunity_id, *, write=False):
        if write:
            await require_permission(connection, 'demo_scene.create', opportunity_id=opportunity_id)
        row = await connection.fetchrow(
            "SELECT id,security.authorization_opportunity('demo_scene.create',id) AS can_create,"
            "security.authorization_opportunity('demo_scene.update',id) AS writable,"
            "security.authorization_opportunity('demo_scene.delete',id) AS can_delete FROM crm.opportunity "
            "WHERE id=$1::uuid AND deleted_at IS NULL",
            opportunity_id,
        )
        if not row:
            raise LookupError("商机不存在或不在当前权限范围")
        return row

    async def list(self, connection, opportunity_id, *, limit=50, offset=0):
        permission = await self.opportunity(connection, opportunity_id)
        args = [opportunity_id]
        total = await connection.fetchval(
            "SELECT count(*) FROM crm.opportunity_demo_scenes WHERE opportunity_id=$1::uuid AND deleted_at IS NULL",
            *args,
        )
        rows = await connection.fetch(
            "SELECT d.*,u.display_name AS creator_name FROM crm.opportunity_demo_scenes d "
            "LEFT JOIN platform.user_ref u ON u.id=d.created_by AND u.workspace_id=d.workspace_id "
            "WHERE d.opportunity_id=$1::uuid AND d.deleted_at IS NULL "
            "ORDER BY d.created_at DESC,d.id DESC LIMIT $2 OFFSET $3",
            *args,
            limit,
            offset,
        )
        return {
            "items": [{**dict(r), "can_edit": permission["writable"], "can_delete": permission["can_delete"]} for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "editable": permission["can_create"], "can_create": permission["can_create"],
            "data_source": "database",
        }

    async def get(self, connection, scene_id, *, lock=False):
        row = await connection.fetchrow(
            "SELECT d.*,u.display_name AS creator_name,security.can_write_demo_scene(d.opportunity_id) AS editable,"
            "security.authorization_opportunity('demo_scene.delete',d.opportunity_id) AS can_delete "
            "FROM crm.opportunity_demo_scenes d LEFT JOIN platform.user_ref u "
            "ON u.id=d.created_by AND u.workspace_id=d.workspace_id "
            "WHERE d.id=$1::uuid AND d.deleted_at IS NULL" + (" FOR UPDATE OF d" if lock else ""),
            scene_id,
        )
        if not row:
            raise LookupError("Demo不存在或已删除")
        return {**dict(row), "can_edit": row["editable"]}

    async def create(self, connection, actor, opportunity_id, body):
        await self.opportunity(connection, opportunity_id, write=True)
        scene_id = await connection.fetchval(
            "INSERT INTO crm.opportunity_demo_scenes(workspace_id,opportunity_id,name,description,created_by) "
            "VALUES($1::uuid,$2::uuid,$3,$4,$5::uuid) RETURNING id",
            actor.workspace_id,
            opportunity_id,
            body.name,
            body.description,
            actor.user_id,
        )
        return await self.get(connection, scene_id)

    async def create_batch(self, connection, actor, opportunity_id, body):
        await self.opportunity(connection, opportunity_id, write=True)
        items = [await self.create(connection, actor, opportunity_id, scene) for scene in body.scenes]
        return {"items": items, "created_count": len(items), "data_source": "database"}

    async def update(self, connection, scene_id, body, *, delete=False):
        current = await self.get(connection, scene_id, lock=True)
        await require_permission(connection, 'demo_scene.delete' if delete else 'demo_scene.update',
                                 opportunity_id=current['opportunity_id'])
        require_version(current["version_no"], body.version_no)
        if delete:
            await connection.execute(
                "UPDATE crm.opportunity_demo_scenes SET deleted_at=clock_timestamp() WHERE id=$1::uuid", scene_id
            )
            return {"id": str(scene_id), "deleted": True, "version_no": current["version_no"] + 1}
        if current["name"] == body.name and current["description"] == body.description:
            return current
        await connection.execute(
            "UPDATE crm.opportunity_demo_scenes SET name=$2,description=$3 WHERE id=$1::uuid",
            scene_id,
            body.name,
            body.description,
        )
        return await self.get(connection, scene_id)

    async def history(self, connection, scene_id, *, limit=50, offset=0):
        rows = await connection.fetch(
            "SELECT * FROM security.demo_scene_history($1::uuid,$2,$3)", scene_id, limit, offset
        )
        return {
            "items": [{k: v for k, v in dict(r).items() if k != "total"} for r in rows],
            "total": rows[0]["total"] if rows else 0,
            "limit": limit,
            "offset": offset,
            "data_source": "database",
        }
