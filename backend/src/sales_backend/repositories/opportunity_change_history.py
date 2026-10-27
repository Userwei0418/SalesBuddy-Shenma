"""All saved opportunity change events, under current object authorization and RLS."""

import json
from hashlib import sha256

from sales_backend.domain.concurrency import VersionConflict
from sales_backend.domain.opportunity_change_history import change_document
from sales_backend.repositories.authorization_checks import require_permission

REVISION_SQL = """SELECT encode(sha256(convert_to(
 COALESCE(jsonb_agg(jsonb_build_array(b.id,b.version_no,b.created_at,b.actor_user_ref_id,u.display_name,b.changes)
   ORDER BY b.created_at,b.id),'[]'::jsonb)::text,'UTF8')),'hex')
 FROM crm.business_change b LEFT JOIN platform.user_ref u
 ON u.id=b.actor_user_ref_id AND u.workspace_id=b.workspace_id
 WHERE b.opportunity_id=$1::uuid AND b.workspace_id=common.current_workspace_id()"""
PAGE_SQL = """SELECT b.id::text,b.version_no,b.created_at,b.changes::text,
 b.actor_user_ref_id::text AS actor_id,u.display_name AS actor_name
 FROM crm.business_change b LEFT JOIN platform.user_ref u
 ON u.id=b.actor_user_ref_id AND u.workspace_id=b.workspace_id
 WHERE b.opportunity_id=$1::uuid AND b.workspace_id=common.current_workspace_id()
 ORDER BY b.created_at DESC,b.id DESC LIMIT $2 OFFSET $3"""


async def read_change_history(connection, actor, opportunity_id, *, limit=20, offset=0, expected_history_revision=None):
    await require_permission(connection, "opportunity.read")
    parent = await connection.fetchrow(
        """SELECT o.version_no,(security.authorization_opportunity('actual.read',o.id)
        AND security.authorization_customer('actual.read',o.customer_id)) AS actual_allowed
        FROM crm.opportunity o WHERE o.id=$1::uuid AND o.workspace_id=common.current_workspace_id()
        AND o.deleted_at IS NULL AND security.authorization_opportunity('opportunity.read',o.id)""",
        str(opportunity_id),
    )
    if parent is None:
        raise LookupError("商机不存在或不在当前权限范围")
    digest = await connection.fetchval(REVISION_SQL, str(opportunity_id))
    revision = sha256(
        json.dumps(
            dict(
                schema_version=1,
                workspace_id=str(actor.workspace_id),
                user_id=str(actor.user_id),
                role=actor.role.value,
                opportunity_id=str(opportunity_id),
                opportunity_version=parent["version_no"],
                actual_allowed=parent["actual_allowed"],
                history_digest=digest,
            ),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if expected_history_revision is not None and revision != expected_history_revision:
        raise VersionConflict("变更记录已更新，请保留当前内容并重新读取第一页")
    rows = await connection.fetch(PAGE_SQL, str(opportunity_id), limit + 1, offset)
    has_more = len(rows) > limit
    return dict(
        schema_version=1,
        opportunity_id=str(opportunity_id),
        history_revision=revision,
        changes=dict(
            items=[change_document(row, actual_allowed=parent["actual_allowed"]) for row in rows[:limit]],
            limit=limit,
            offset=offset,
            has_more=has_more,
            next_offset=offset + limit if has_more else None,
            order="created_at_desc_id_desc",
            coverage_note="仅展示该商机已保存的变更，不补造缺失历史。字段名称只使用保存时快照；未记录名称保留ID。"
            "操作人未有历史姓名快照时展示当前姓名并明确标记。"
            "格式不可识别、超出显示边界或无相应查看权限的内容会明确提示。",
        ),
    )
