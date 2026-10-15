"""Scoped operational finance workspace; caller owns one readonly RR snapshot."""

import json
from hashlib import sha256

from sales_backend.domain.concurrency import VersionConflict
from sales_backend.repositories.authorization_checks import require_permission
from sales_backend.repositories.materials import OpportunityMaterialRepository
from sales_backend.repositories.opportunity_finance import OpportunityFinanceRepository
from sales_backend.repositories.opportunity_product_lines import attach_product_lines


def change_document(row):
    """Preserve recorded labels/units and exact before/after values, without inference."""
    item = dict(row)
    changes = item.pop("changes")
    if isinstance(changes, str):
        changes = json.loads(changes, parse_float=str)
    fields = []
    for change in changes if isinstance(changes, list) else []:
        if not isinstance(change, dict):
            continue
        fields.append({
            **change,
            "field": change.get("field"),
            "label": change.get("label") or change.get("field") or "未标注字段",
            "before": change.get("before"),
            "after": change.get("after"),
        })
    return {
        "id": item["id"], "version_no": item["version_no"],
        "created_at": item["created_at"],
        "actor": {"id": item["actor_id"], "name": item["actor_name"]},
        "fields": fields,
        "detail_status": "recorded" if fields else "unavailable",
    }


class FinanceWorkspaceRepository:
    async def read(self, connection, opportunity_id, *, year, quarter, history_limit=20, history_offset=0,
                   expected_history_revision=None):
        if not 1 <= history_limit <= 100 or history_offset < 0:
            raise ValueError("变化过程分页参数无效")
        if not 2000 <= year <= 2100 or not 1 <= quarter <= 4:
            raise ValueError("请选择有效年份和季度")
        if history_offset and expected_history_revision is None:
            raise ValueError('继续翻页须携带首屏history_revision，请重新打开财务核对')
        # This checks actual.read on the opportunity, customer scope, tenant,
        # opportunity.read and existing RLS before any surrounding facts return.
        actual = await OpportunityFinanceRepository().read(connection, opportunity_id, year=year, quarter=quarter)
        await require_permission(connection, 'opportunity.read', opportunity_id=opportunity_id)
        row = await connection.fetchrow(
            """SELECT o.id::text,o.customer_id::text,o.name,o.version_no,o.amount::text,
              o.currency,o.product_line,o.status,o.probability,o.expected_close_date,
              o.trade_relation,o.upstream_opportunity_id::text,o.created_at,o.updated_at,
              COALESCE(c.name,security.customer_reference(o.customer_id)->>'name') AS customer_name,
              o.owner_user_ref_id::text,u.display_name AS owner_name
              FROM crm.opportunity o
              LEFT JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id AND c.deleted_at IS NULL
              LEFT JOIN platform.user_ref u ON u.id=o.owner_user_ref_id AND u.workspace_id=o.workspace_id
              WHERE o.id=$1::uuid AND o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL
                AND security.authorization_opportunity('opportunity.read',o.id)
                AND security.authorization_opportunity('actual.read',o.id)
                AND security.authorization_customer('actual.read',o.customer_id)""",
            str(opportunity_id),
        )
        if not row:
            raise LookupError("商机不存在或不在财务查看范围")
        opportunity = dict(row)
        # Actual-confirmation events have their own period version and do not
        # increment opportunity.version_no. Hash the complete scoped history,
        # including corrections/removals, instead of relying on parent version.
        history_digest = await connection.fetchval(
            """SELECT md5(COALESCE(string_agg(md5(jsonb_build_array(
              b.id,b.version_no,b.created_at,b.changes)::text),'' ORDER BY b.created_at,b.id),''))
              FROM crm.business_change b WHERE b.opportunity_id=$1::uuid
              AND b.workspace_id=common.current_workspace_id()""", str(opportunity_id),
        )
        revision = sha256(json.dumps({
            'opportunity_id': str(opportunity_id), 'opportunity_version': opportunity['version_no'],
            'year': year, 'quarter': quarter, 'history_digest': history_digest,
        }, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if expected_history_revision is not None and revision != expected_history_revision:
            raise VersionConflict('商机或变化记录已更新，请保留未提交输入，重新打开财务核对后再翻页')
        await attach_product_lines(connection, [opportunity])
        forecast = await connection.fetchrow(
            """SELECT f.year,f.quarter,f.recognized_amount::text,f.collection_amount::text,
              f.collection_confidence,f.updated_at,f.updated_by_user_ref_id::text AS actor_id,
              u.display_name AS actor_name
              FROM crm.opportunity_forecast f LEFT JOIN platform.user_ref u
                ON u.id=f.updated_by_user_ref_id AND u.workspace_id=f.workspace_id
              WHERE f.opportunity_id=$1::uuid AND f.workspace_id=common.current_workspace_id()
                AND f.year=$2 AND f.quarter=$3""",
            str(opportunity_id), year, quarter,
        )
        saved = dict(forecast) if forecast else {}
        sales_forecast = {
            "status": "saved" if forecast else "missing", "year": year, "quarter": quarter,
            "recognized_amount": saved.get("recognized_amount"),
            "collection_amount": saved.get("collection_amount"),
            "collection_confidence": saved.get("collection_confidence"),
            "updated_at": saved.get("updated_at"),
            "updated_by": {"id": saved.get("actor_id"), "name": saved.get("actor_name")},
        }
        materials_repository = OpportunityMaterialRepository()
        materials = await materials_repository.read(connection, opportunity_id)
        reference = await materials_repository.preview(connection, opportunity_id, year, quarter)
        records = await connection.fetch(
            """SELECT b.id::text,b.version_no,b.changes::text,b.created_at,
              b.actor_user_ref_id::text AS actor_id,u.display_name AS actor_name
              FROM crm.business_change b LEFT JOIN platform.user_ref u
                ON u.id=b.actor_user_ref_id AND u.workspace_id=b.workspace_id
              WHERE b.opportunity_id=$1::uuid AND b.workspace_id=common.current_workspace_id()
              ORDER BY b.created_at DESC,b.id DESC LIMIT $2 OFFSET $3""",
            str(opportunity_id), history_limit + 1, history_offset,
        )
        has_more = len(records) > history_limit
        changes = {
            "items": [change_document(record) for record in records[:history_limit]],
            "limit": history_limit, "offset": history_offset, "has_more": has_more,
            "next_offset": history_offset + history_limit if has_more else None,
            "order": "created_at_desc_id_desc",
            "coverage_note": "仅展示系统已保存的变化记录；缺失历史不补造。字段标签与金额单位沿用保存时记录。",
        }
        return {
            "schema_version": 1, "currency": "CNY", "period": {"year": year, "quarter": quarter},
            "history_revision": revision,
            "opportunity": opportunity, "sales_forecast": sales_forecast,
            "materials": materials, "calculation_reference": reference, "actual": actual, "changes": changes,
        }
