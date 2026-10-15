"""One target table, with separate immutable change requests."""

from sales_backend.db import json_value

LEGACY_KINDS_SQL = "('collection','collection_ppl','recognized','acv','opportunity_count','visit_count','demo_count')"


class TargetRepository:
    async def management_options(self, connection, actor):
        from sales_backend.repositories.authorization_checks import require_permission

        await require_permission(connection, "target.manage")
        # Reuse the very same subject/write/read checks as target management.
        # A self submit grant must not enlarge an unrelated manage grant.
        allowed = """security.authorization_subject('target.manage',kind,user_id,team_id)
          AND security.target_scope_write(kind,user_id,team_id,department_code)
          AND security.target_scope_read(kind,user_id,team_id,department_code)"""
        rows = await connection.fetch("""WITH candidates AS (
            SELECT 'person'::text AS kind,u.id::text AS id,u.display_name AS name,u.account_code,
              u.id AS user_id,NULL::uuid AS team_id,'sales'::text AS department_code
            FROM platform.user_ref u WHERE u.workspace_id=$1::uuid AND u.status='active' AND u.deleted_at IS NULL
            UNION ALL
            SELECT 'team',t.id::text,t.name,NULL,NULL,t.id,'sales' FROM platform.team t
            WHERE t.workspace_id=$1::uuid AND t.status='active' AND t.deleted_at IS NULL
              AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
            UNION ALL
            SELECT 'department',code,name,NULL,NULL,NULL,code
            FROM (VALUES ('sales','销售部门'),('fde','FDE 部门')) d(code,name)
        ) SELECT kind,id,name,account_code FROM candidates WHERE """ + allowed + " ORDER BY kind,name,id", actor.workspace_id)
        return {
            "people": [{"id": r["id"], "name": r["name"], "account_code": r["account_code"]}
                       for r in rows if r["kind"] == "person"],
            "teams": [{"id": r["id"], "name": r["name"]} for r in rows if r["kind"] == "team"],
            "departments": [{"id": r["id"], "name": r["name"]} for r in rows if r["kind"] == "department"],
        }

    async def allowed(self, connection, scope, *, write=False):
        function = "target_scope_write" if write else "target_scope_read"
        return await connection.fetchval(
            f"SELECT security.{function}($1,$2::uuid,$3::uuid,$4)",
            scope["scope_type"], scope["user_id"], scope["team_id"], scope.get("department_code", "sales"),
        )

    async def list(self, connection, actor, *, period, scope=None, kind=None, limit=50, offset=0, q=None):
        params = [
            actor.workspace_id,
            period["type"],
            period["start"],
            scope["scope_type"] if scope else None,
            scope["user_id"] if scope else None,
            scope["team_id"] if scope else None,
            kind,
            q,
            scope.get("department_code", "sales") if scope else None,
        ]
        where = """t.workspace_id=$1::uuid AND t.period_type=$2 AND t.period_start=$3
          AND ($4::text IS NULL OR (t.scope_type=$4 AND t.user_ref_id IS NOT DISTINCT FROM $5::uuid
           AND t.team_id IS NOT DISTINCT FROM $6::uuid AND t.department_code=$9)) AND ($7::text IS NULL OR t.kind=$7)
          AND ($8::text IS NULL OR COALESCE(u.display_name,tm.name,'全部团队') ILIKE '%'||$8||'%'
           OR u.account_code ILIKE '%'||$8||'%')"""
        # This repository serves the legacy six-kind target list. The Review
        # projection has its own full dictionary query and pagination.
        where += " AND t.kind IN " + LEGACY_KINDS_SQL
        joins = (
            " FROM crm.sales_target t LEFT JOIN platform.user_ref u ON u.id=t.user_ref_id "
            "LEFT JOIN platform.team tm ON tm.id=t.team_id WHERE "
        )
        total = await connection.fetchval("SELECT count(*)" + joins + where, *params)
        rows = await connection.fetch(
            "SELECT t.*,t.amount::text AS amount_text,u.display_name AS user_name,u.account_code,tm.name AS team_name,"
            "(security.authorization_subject('target.manage',t.scope_type,t.user_ref_id,t.team_id) AND "
            "security.target_scope_write(t.scope_type,t.user_ref_id,t.team_id,t.department_code)) AS can_manage,"
            "security.authorization_subject('target.history',t.scope_type,t.user_ref_id,t.team_id) AS can_history"
            + joins
            + where
            + " ORDER BY t.updated_at DESC,t.id DESC LIMIT $10 OFFSET $11",
            *params,
            limit,
            offset,
        )
        return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}

    async def requests(self, connection, actor, *, period=None, scope=None, status=None, limit=50, offset=0,
                       legacy_only=False):
        params = [
            actor.workspace_id,
            status,
            period["type"] if period else None,
            period["start"] if period else None,
            scope["scope_type"] if scope else None,
            scope["user_id"] if scope else None,
            scope["team_id"] if scope else None,
            scope.get("department_code", "sales") if scope else None,
        ]
        source = """ FROM crm.sales_target_change_request r JOIN crm.sales_target t ON t.id=r.target_id
          LEFT JOIN platform.user_ref u ON u.id=r.applicant_user_ref_id
          LEFT JOIN platform.user_ref reviewer ON reviewer.id=r.reviewer_user_ref_id
          WHERE r.workspace_id=$1::uuid AND ($2::text IS NULL OR r.status=$2)
          AND ($3::text IS NULL OR (t.period_type=$3 AND t.period_start=$4))
          AND ($5::text IS NULL OR (t.scope_type=$5 AND t.user_ref_id IS NOT DISTINCT FROM $6::uuid
           AND t.team_id IS NOT DISTINCT FROM $7::uuid AND t.department_code=$8))"""
        if legacy_only:
            source += " AND t.kind IN " + LEGACY_KINDS_SQL
        amount_projection = "" if legacy_only else (
            ",r.previous_amount::text AS previous_amount_text,r.proposed_amount::text AS proposed_amount_text"
        )
        total = await connection.fetchval("SELECT count(*)" + source, *params)
        rows = await connection.fetch(
            "SELECT r.*,t.kind,t.scope_type,t.period_type,t.period_start,t.period_end,t.amount AS effective_amount,"
            "t.version_no AS effective_version,u.display_name AS applicant_name,u.account_code,"
            "reviewer.display_name AS reviewer_name"
            + amount_projection
            + source
            + " ORDER BY r.created_at DESC,r.id DESC LIMIT $9 OFFSET $10",
            *params,
            limit,
            offset,
        )
        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "data_source": "database",
        }

    async def save(self, connection, *, scope, period, kind, amount, version_no=None):
        return json_value(
            await connection.fetchval(
                "SELECT security.save_period_target($1,$2::uuid,$3::uuid,$4,$5,$6,$7,$8,$9)",
                scope["scope_type"],
                scope["user_id"],
                scope["team_id"],
                period["type"],
                period["start"],
                period["end"],
                kind,
                amount,
                version_no,
            )
        )

    async def save_batch(self, connection, *, scope, period, items, reason):
        return json_value(await connection.fetchval(
            "SELECT security.save_target_batch($1,$2::uuid,$3::uuid,$4,$5,$6,$7::jsonb,$8,$9)",
            scope["scope_type"], scope["user_id"], scope["team_id"],
            period["type"], period["start"], period["end"], items, reason,
            scope.get("department_code", "sales"),
        ))

    async def batches(self, connection, actor, *, period=None, scope=None, status=None, limit=50, offset=0,
                      legacy_only=False):
        params = [actor.workspace_id, status, period["type"] if period else None,
                  period["start"] if period else None, scope["scope_type"] if scope else None,
                  scope["user_id"] if scope else None, scope["team_id"] if scope else None,
                  scope.get("department_code", "sales") if scope else None]
        source = """ FROM crm.sales_target_batch_request r
          LEFT JOIN platform.user_ref a ON a.id=r.applicant_user_ref_id
          LEFT JOIN platform.user_ref u ON u.id=r.user_ref_id
          LEFT JOIN platform.user_ref reviewer ON reviewer.id=r.reviewer_user_ref_id
          LEFT JOIN platform.team t ON t.id=r.team_id
          WHERE r.workspace_id=$1::uuid AND ($2::text IS NULL OR r.status=$2)
          AND ($3::text IS NULL OR (r.period_type=$3 AND r.period_start=$4))
          AND ($5::text IS NULL OR (r.scope_type=$5 AND r.user_ref_id IS NOT DISTINCT FROM $6::uuid
            AND r.team_id IS NOT DISTINCT FROM $7::uuid AND r.department_code=$8))"""
        projection = ""
        if legacy_only:
            condition = "i->>'kind' IN " + LEGACY_KINDS_SQL
            source += " AND EXISTS(SELECT 1 FROM jsonb_array_elements(r.items) i WHERE " + condition + ")"
            projection = ", (SELECT jsonb_agg(i ORDER BY ord) FROM jsonb_array_elements(r.items) WITH ORDINALITY x(i,ord) WHERE " + condition + ") AS legacy_items"
        else:
            # JSONB numeric must become text before the application JSON codec
            # can round it. Retain old fields and add exact approval display
            # values only to the full console projection, not legacy Mini rows.
            projection = (
                ", (SELECT jsonb_agg(i || jsonb_build_object("
                "'previous_amount_text',i->>'previous_amount',"
                "'proposed_amount_text',i->>'proposed_amount') ORDER BY ord)"
                " FROM jsonb_array_elements(r.items) WITH ORDINALITY x(i,ord)) AS approval_items"
            )
        total = await connection.fetchval("SELECT count(*)" + source, *params)
        rows = await connection.fetch(
            "SELECT r.*,a.display_name AS applicant_name,a.account_code,u.display_name AS user_name,"
            "t.name AS team_name,reviewer.display_name AS reviewer_name" + projection + source +
            " ORDER BY r.created_at DESC,r.id DESC LIMIT $9 OFFSET $10", *params, limit, offset)
        items = [dict(row) for row in rows]
        if legacy_only:
            for row in items:
                row["items"] = json_value(row.pop("legacy_items"))
        else:
            for row in items:
                row["items"] = json_value(row.pop("approval_items"))
        return {"items": items, "total": total, "limit": limit,
                "offset": offset, "data_source": "database"}

    async def decide_batch(self, connection, request_id, body):
        return json_value(await connection.fetchval(
            "SELECT security.review_target_batch($1::uuid,$2,$3)", request_id, body.decision, body.reason))

    async def decide(self, connection, request_id, body):
        return json_value(
            await connection.fetchval(
                "SELECT security.review_target_change($1::uuid,$2,$3)", request_id, body.decision, body.reason
            )
        )

    async def history(self, connection, actor, target_id, *, limit=50, offset=0):
        target = await connection.fetchrow(
            "SELECT security.authorization_subject('target.history',scope_type,user_ref_id,team_id) AS can_history "
            "FROM crm.sales_target WHERE id=$1::uuid AND workspace_id=$2::uuid",
            target_id,
            actor.workspace_id,
        )
        if target is None:
            raise LookupError("目标不存在")
        if not target["can_history"]:
            raise PermissionError("无权查看该目标的历史")
        rows = await connection.fetch(
            "SELECT * FROM security.target_history_rows($1::uuid,$2,$3)",
            target_id,
            limit,
            offset,
        )
        return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset}
