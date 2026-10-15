# ruff: noqa: S608 -- SQL composition uses only the fixed, tenant-scoped CTE below.
"""Intent-only history under current opportunity.read and existing table RLS."""

import json
from hashlib import sha256

from sales_backend.domain.concurrency import VersionConflict
from sales_backend.repositories.authorization_checks import require_permission

FIELD = "intent_product_line_ids"
SCOPED_ROWS = """SELECT b.id,b.version_no,b.created_at,b.actor_user_ref_id,b.workspace_id,p.changes
 FROM crm.business_change b
 CROSS JOIN LATERAL (
   SELECT jsonb_agg(item ORDER BY ordinal) AS changes
   FROM jsonb_array_elements(CASE WHEN jsonb_typeof(b.changes)='array'
     THEN b.changes ELSE '[]'::jsonb END) WITH ORDINALITY AS original(item,ordinal)
   WHERE jsonb_typeof(item)='object' AND item->>'field'='intent_product_line_ids'
 ) p
 WHERE b.opportunity_id=$1::uuid AND b.workspace_id=common.current_workspace_id()
   AND p.changes IS NOT NULL"""
REVISION_SQL = "WITH scoped AS (" + SCOPED_ROWS + """ ) SELECT encode(sha256(convert_to(
 COALESCE(jsonb_agg(jsonb_build_array(id,version_no,created_at,actor_user_ref_id,changes)
   ORDER BY created_at,id),'[]'::jsonb)::text,'UTF8')),'hex') FROM scoped"""
PAGE_SQL = "WITH scoped AS (" + SCOPED_ROWS + """ )
 SELECT b.id::text,b.version_no,b.created_at,b.changes,
   b.actor_user_ref_id::text AS actor_id,u.display_name AS actor_name
 FROM scoped b LEFT JOIN platform.user_ref u
   ON u.id=b.actor_user_ref_id AND u.workspace_id=b.workspace_id
 ORDER BY b.created_at DESC,b.id DESC LIMIT $2 OFFSET $3"""


def _ids(value):
    return value if isinstance(value, list) and all(isinstance(item, str) for item in value) else None


def _snapshot(value, before, after):
    if (not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1
            or value.get("basis") != "directory_at_change" or before is None or after is None):
        return None
    result = {"version": 1, "basis": "directory_at_change"}
    for side, ids in (("before", before), ("after", after)):
        rows = value.get(side)
        if (not isinstance(rows, list) or len(rows) != len(ids) or len(set(ids)) != len(ids)
                or any(not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in rows)):
            return None
        by_id = {row["id"]: row for row in rows}
        if len(by_id) != len(rows) or set(by_id) != set(ids):
            return None
        labels = []
        for item_id in ids:
            row = by_id[item_id]
            recorded = row.get("status") == "recorded"
            labels.append({"id": item_id, "status": "recorded" if recorded else "unavailable",
                "code": row.get("code") if recorded and isinstance(row.get("code"), str) else None,
                "name": row.get("name") if recorded and isinstance(row.get("name"), str) else None,
                "is_active": row.get("is_active") if recorded and type(row.get("is_active")) is bool else None})
        result[side] = labels
    return result


def intent_change_document(row):
    changes = row["changes"]
    if isinstance(changes, str):
        changes = json.loads(changes)
    fields = []
    for change in changes if isinstance(changes, list) else []:
        if not isinstance(change, dict) or change.get("field") != FIELD:
            continue
        before, after = _ids(change.get("before")), _ids(change.get("after"))
        fields.append({"field": FIELD,
            "label": change["label"] if isinstance(change.get("label"), str) and change["label"] else "意向产品线",
            "before": before, "after": after,
            "display_snapshot": _snapshot(change.get("display_snapshot"), before, after)})
    return {"id": row["id"], "version_no": row["version_no"], "created_at": row["created_at"],
        "actor": {"id": row["actor_id"], "name": row["actor_name"]}, "fields": fields,
        "detail_status": "recorded" if fields else "unavailable"}


async def read_intent_history(connection, actor, opportunity_id, *, limit=20, offset=0,
                              expected_history_revision=None):
    await require_permission(connection, "opportunity.read")
    row = await connection.fetchrow("""SELECT o.version_no FROM crm.opportunity o
      WHERE o.id=$1::uuid AND o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL
        AND security.authorization_opportunity('opportunity.read',o.id)""", str(opportunity_id))
    if row is None:
        raise LookupError("商机不存在或不在当前权限范围")
    digest = await connection.fetchval(REVISION_SQL, str(opportunity_id))
    revision = sha256(json.dumps({"schema_version": 1, "workspace_id": str(actor.workspace_id),
        "user_id": str(actor.user_id), "opportunity_id": str(opportunity_id), "field": FIELD,
        "opportunity_version": row["version_no"], "history_digest": digest},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if expected_history_revision is not None and revision != expected_history_revision:
        raise VersionConflict("变更记录已更新，请保留当前内容并重新读取第一页")
    rows = await connection.fetch(PAGE_SQL, str(opportunity_id), limit + 1, offset)
    has_more = len(rows) > limit
    return {"schema_version": 1, "opportunity_id": str(opportunity_id), "field": FIELD,
        "history_revision": revision, "changes": {
            "items": [intent_change_document(item) for item in rows[:limit]], "limit": limit, "offset": offset,
            "has_more": has_more, "next_offset": offset + limit if has_more else None,
            "order": "created_at_desc_id_desc",
            "coverage_note": ("仅展示已保存的意向产品线变更；名称沿用保存时快照，"
                              "缺失名称未记录并保留ID，不用当前目录补造。")}}
