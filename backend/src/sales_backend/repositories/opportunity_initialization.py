"""Company-admin and object gates before minimal, whitelisted source projection."""

from sales_backend.db import json_value
from sales_backend.domain.opportunity_initialization import SOURCE_KEYS
from sales_backend.repositories.authorization_checks import require_permission
from sales_backend.repositories.collaboration import effective_ids
from sales_backend.repositories.opportunity_fields import PERSISTED_FIELDS, project_people


async def authorize(connection, ids, *, people=False):
    await require_permission(connection, "access.console")
    # The fresh console grant above checks the active user and tenant. Use the
    # public current-actor predicate; company_administrator_identity is private.
    if not await connection.fetchval(
        "SELECT security.has_active_role('administrator')"
    ):
        raise PermissionError("仅公司管理员可预览或确认旧字段初始化")
    for oid in sorted(map(str, ids)):
        await require_permission(connection, "opportunity.read", opportunity_id=oid)
        await require_permission(connection, "opportunity.update", opportunity_id=oid)
        if people:
            await require_permission(connection, "opportunity.fde_members", opportunity_id=oid)


async def records(connection, ids, *, lock=False):
    if lock:
        await connection.execute("SELECT security.finance_write_lock()")
    # Only exact approved source-field keys leave the DB, even for administrators.
    keys = sorted({alias for aliases in SOURCE_KEYS.values() for alias in aliases})
    rows = await connection.fetch(
        "SELECT o.id::text,o.version_no,"
        + ",".join("o." + key for key in PERSISTED_FIELDS)
        + """,
      jsonb_build_object('source_fields',COALESCE((SELECT jsonb_object_agg(e.key,e.value)
        FROM jsonb_each(CASE WHEN jsonb_typeof(o.import_meta->'source_fields')='object'
          THEN o.import_meta->'source_fields' ELSE '{}'::jsonb END) e WHERE e.key=ANY($2::text[])),'{}'::jsonb),
        'raw_fields',COALESCE((SELECT jsonb_object_agg(e.key,e.value)
        FROM jsonb_each(CASE WHEN jsonb_typeof(o.import_meta->'raw_fields')='object'
          THEN o.import_meta->'raw_fields' ELSE '{}'::jsonb END) e WHERE e.key=ANY($2::text[])),'{}'::jsonb)) AS source
      FROM crm.opportunity o WHERE o.workspace_id=common.current_workspace_id() AND o.id=ANY($1::uuid[])
      AND o.deleted_at IS NULL AND security.authorization_opportunity('opportunity.read',o.id)
      ORDER BY o.id"""
        + (" FOR UPDATE OF o" if lock else ""),
        list(map(str, ids)),
        keys,
    )
    if {r["id"] for r in rows} != set(map(str, ids)):
        raise LookupError("部分商机不存在或不在可维护范围，请刷新")
    participants = await project_people(connection, list(map(str, ids)))
    result = []
    for row in rows:
        value = dict(row)
        value["source"] = json_value(value["source"])
        value["fde_member_ids"] = sorted(await effective_ids(connection, row["id"]))
        value["participants"] = [{"user_id": p["user_id"], "role": p["role"]} for p in participants.get(row["id"], [])]
        result.append(value)
    return result


async def active_people(connection, ids):
    if not ids:
        return {}
    return {
        r["id"]: dict(r)
        for r in await connection.fetch(
            "SELECT id::text,display_name AS name,security.fde_user_is_active(id) AS is_fde FROM platform.user_ref "
            "WHERE workspace_id=common.current_workspace_id() AND status='active' AND deleted_at IS NULL AND id=ANY($1::uuid[])",
            ids,
        )
    }
