"""One target table, with separate immutable change requests."""

from sales_backend.db import json_value


class TargetRepository:
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
        joins = (
            " FROM crm.sales_target t LEFT JOIN platform.user_ref u ON u.id=t.user_ref_id "
            "LEFT JOIN platform.team tm ON tm.id=t.team_id WHERE "
        )
        total = await connection.fetchval("SELECT count(*)" + joins + where, *params)
        rows = await connection.fetch(
            "SELECT t.*,t.amount::text AS amount_text,u.display_name AS user_name,u.account_code,tm.name AS team_name"
            + joins
            + where
            + " ORDER BY t.updated_at DESC,t.id DESC LIMIT $10 OFFSET $11",
            *params,
            limit,
            offset,
        )
        return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}

    async def requests(self, connection, actor, *, period=None, scope=None, status=None, limit=50, offset=0):
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
        total = await connection.fetchval("SELECT count(*)" + source, *params)
        rows = await connection.fetch(
            "SELECT r.*,t.kind,t.scope_type,t.period_type,t.period_start,t.period_end,t.amount AS effective_amount,"
            "t.version_no AS effective_version,u.display_name AS applicant_name,u.account_code,"
            "reviewer.display_name AS reviewer_name"
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

    async def batches(self, connection, actor, *, period=None, scope=None, status=None, limit=50, offset=0):
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
        total = await connection.fetchval("SELECT count(*)" + source, *params)
        rows = await connection.fetch(
            "SELECT r.*,a.display_name AS applicant_name,a.account_code,u.display_name AS user_name,"
            "t.name AS team_name,reviewer.display_name AS reviewer_name" + source +
            " ORDER BY r.created_at DESC,r.id DESC LIMIT $9 OFFSET $10", *params, limit, offset)
        return {"items": [dict(row) for row in rows], "total": total, "limit": limit,
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
        exists = await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM crm.sales_target WHERE id=$1::uuid AND workspace_id=$2::uuid)",
            target_id,
            actor.workspace_id,
        )
        if not exists:
            raise LookupError("目标不存在")
        rows = await connection.fetch(
            "SELECT id,occurred_at,actor_name_snapshot AS actor_name,action_code,before_snapshot,after_snapshot "
            "FROM ops.audit_log WHERE workspace_id=$1::uuid AND object_type='sales_target' AND object_id=$2::uuid "
            "ORDER BY occurred_at DESC,id DESC LIMIT $3 OFFSET $4",
            actor.workspace_id,
            target_id,
            limit,
            offset,
        )
        return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset}
