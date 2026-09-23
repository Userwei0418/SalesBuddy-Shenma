"""Workspace partner directory. All SQL runs with the caller's RLS identity."""

from sales_backend.domain.concurrency import require_version


class PartnerRepository:
    async def list(self, connection, *, q=None, status="active", limit=50, offset=0):
        args = (q, status)
        total = await connection.fetchval(
            "SELECT count(*) FROM crm.partner WHERE "
            "($1::text IS NULL OR name ILIKE '%'||$1||'%') AND ($2::text IS NULL OR status=$2)",
            *args,
        )
        rows = await connection.fetch(
            "SELECT id::text,name,status,version_no,created_at,updated_at FROM crm.partner WHERE "
            "($1::text IS NULL OR name ILIKE '%'||$1||'%') AND ($2::text IS NULL OR status=$2) "
            "ORDER BY name,id LIMIT $3 OFFSET $4",
            *args,
            limit,
            offset,
        )
        return {"items": [dict(row) for row in rows], "total": total}

    async def save(self, connection, actor, data):
        name = data["name"].strip()
        if not name or name == "直销":
            raise ValueError("请填写有效伙伴名称；直销不需要创建伙伴")
        if data.get("id"):
            before = await connection.fetchrow("SELECT * FROM crm.partner WHERE id=$1 FOR UPDATE", data["id"])
            if not before:
                raise LookupError("伙伴不存在")
            if data.get("version_no") is None:
                raise ValueError("请刷新伙伴后再修改")
            require_version(before["version_no"], data["version_no"])
            row = await connection.fetchrow(
                "UPDATE crm.partner SET name=$2,status=$3,version_no=version_no+1,"
                "updated_at=clock_timestamp(),updated_by_user_ref_id=$4::uuid WHERE id=$1 RETURNING *",
                data["id"],
                name,
                data["status"],
                actor.user_id,
            )
        else:
            row = await connection.fetchrow(
                "INSERT INTO crm.partner(workspace_id,name,status,created_by_user_ref_id,updated_by_user_ref_id) "
                "VALUES($1::uuid,$2,$3,$4::uuid,$4::uuid) RETURNING *",
                actor.workspace_id,
                name,
                data["status"],
                actor.user_id,
            )
        return dict(row)


async def resolve_opportunity_partner(connection, before, data):
    """Validate a directory identity; tolerate an old name only when it resolves uniquely.

    Omitted channel on a historical update preserves its original classification. New
    records default to direct. Selecting or changing a partner always requires active ID.
    """
    channel = data.get("sales_channel")
    partner_id = data.get("partner_id")
    name = str(data.get("partner_name") or "").strip()
    if channel is None:
        if partner_id or name and name != "直销":
            channel = "partner"
        elif name == "直销":
            channel = "direct"
        elif before:
            return {key: before.get(key) for key in ("sales_channel", "partner_id", "partner_name")}
        else:
            channel = "direct"
    if channel == "direct":
        if partner_id:
            raise ValueError("直销不能关联伙伴")
        return {"sales_channel": "direct", "partner_id": None, "partner_name": "直销"}
    if channel != "partner":
        raise ValueError("请选择直销或合作伙伴")
    if not partner_id and name:
        partner_id = await connection.fetchval(
            "SELECT id FROM crm.partner WHERE normalized_name=lower(btrim($1))",
            name,
        )
    if not partner_id:
        raise ValueError("请选择已有伙伴；目录中没有时请联系运营维护")
    row = await connection.fetchrow("SELECT id,name,status FROM crm.partner WHERE id=$1::uuid", str(partner_id))
    if not row:
        raise ValueError("伙伴不存在或不属于本公司")
    unchanged = before and str(before.get("partner_id")) == str(row["id"])
    if row["status"] != "active" and not unchanged:
        raise ValueError("伙伴已停用，请重新选择")
    return {"sales_channel": "partner", "partner_id": str(row["id"]), "partner_name": row["name"]}
