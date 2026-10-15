"""Review projections read the original effective and pending target records."""

from sales_backend.db import json_value


class ReviewRepository:
    async def target_options(self, connection, actor):
        rows = await connection.fetch("""WITH candidates AS (
          SELECT 'person'::text AS kind,u.id::text AS id,u.display_name AS name,u.account_code,
            u.id AS user_id,NULL::uuid AS team_id,'sales'::text AS department_code
          FROM platform.user_ref u WHERE u.workspace_id=$1::uuid AND u.status='active' AND u.deleted_at IS NULL
          UNION ALL
          SELECT 'team',t.id::text,t.name,NULL,NULL,t.id,'sales' FROM platform.team t
          WHERE t.workspace_id=$1::uuid AND t.status='active' AND t.deleted_at IS NULL
            AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
          UNION ALL SELECT 'department',code,name,NULL,NULL,NULL,code
          FROM (VALUES ('sales','销售部门'),('fde','FDE 部门')) d(code,name)
        ) SELECT kind,id,name,account_code,
          security.authorization_subject('target.manage',kind,user_id,team_id) AS can_manage
        FROM candidates WHERE security.target_scope_write(kind,user_id,team_id,department_code)
          AND security.target_scope_read(kind,user_id,team_id,department_code) ORDER BY kind,name,id""", actor.workspace_id)
        return {"people": [dict(r) for r in rows if r["kind"] == "person"],
                "teams": [dict(r) for r in rows if r["kind"] == "team"],
                "departments": [dict(r) for r in rows if r["kind"] == "department"], "data_source": "database"}

    async def metrics(self, connection, actor):
        rows = await connection.fetch(
            "SELECT * FROM crm.review_metric WHERE workspace_id=$1::uuid ORDER BY system_defined DESC,code",
            actor.workspace_id,
        )
        return {"items": [dict(row) for row in rows], "data_source": "database"}

    async def save_metric(self, connection, code, body, version=None):
        return json_value(await connection.fetchval(
            "SELECT security.save_review_metric($1,$2::jsonb,$3)", code, body, version,
        ))

    async def target_rows(self, connection, actor, *, period, scope, kind=None, q=None, limit=50, offset=0):
        # Do not use the legacy 100-request preview to reconstruct rows: a pending
        # first target in a mixed batch must survive refresh and pagination too.
        source = """
        WITH pending AS (
          SELECT r.workspace_id,r.scope_type,r.user_ref_id,r.team_id,r.department_code,
            r.period_type,r.period_start,r.period_end,i->>'kind' AS kind,
            jsonb_build_object('request_id',r.id,'request_type','batch','status','pending',
              'proposed_amount',i->>'proposed_amount','base_version',i->'base_version',
              'applicant_name',u.display_name,'created_at',r.created_at) AS pending_change
          FROM crm.sales_target_batch_request r
          CROSS JOIN LATERAL jsonb_array_elements(r.items) i
          LEFT JOIN platform.user_ref u ON u.id=r.applicant_user_ref_id AND u.workspace_id=r.workspace_id
          WHERE r.workspace_id=$1::uuid AND r.status='pending' AND r.period_type=$2 AND r.period_start=$3
            AND security.target_scope_read(r.scope_type,r.user_ref_id,r.team_id,r.department_code)
          UNION ALL
          SELECT t.workspace_id,t.scope_type,t.user_ref_id,t.team_id,t.department_code,
            t.period_type,t.period_start,t.period_end,t.kind,
            jsonb_build_object('request_id',r.id,'request_type','legacy','status','pending',
              'proposed_amount',r.proposed_amount::text,'base_version',r.base_version,
              'applicant_name',u.display_name,'created_at',r.created_at)
          FROM crm.sales_target_change_request r JOIN crm.sales_target t ON t.id=r.target_id
          LEFT JOIN platform.user_ref u ON u.id=r.applicant_user_ref_id AND u.workspace_id=r.workspace_id
          WHERE r.workspace_id=$1::uuid AND t.workspace_id=$1::uuid AND r.status='pending'
            AND t.period_type=$2 AND t.period_start=$3
        ), effective AS (
          SELECT t.* FROM crm.sales_target t WHERE t.workspace_id=$1::uuid
            AND t.period_type=$2 AND t.period_start=$3
        ), identities AS (
          SELECT workspace_id,scope_type,user_ref_id,team_id,department_code,period_type,period_start,period_end,kind
          FROM effective
          UNION
          SELECT workspace_id,scope_type,user_ref_id,team_id,department_code,period_type,period_start,period_end,kind
          FROM pending
        ), projected AS (
          SELECT t.id,s.workspace_id,s.scope_type,s.user_ref_id,s.team_id,s.department_code,s.period_type,
            s.period_start,s.period_end,s.kind,t.amount::text AS amount_text,t.amount::text AS effective_amount,
            t.version_no,t.source,t.change_reason,t.updated_at,t.updated_by_user_ref_id,
            u.display_name AS user_name,u.account_code,tm.name AS team_name,maintainer.display_name AS updated_by_name,
            to_jsonb(m) AS metric,p.pending_change,
            CASE WHEN t.id IS NULL THEN 'pending_new' WHEN p.pending_change IS NOT NULL THEN 'pending_change'
              ELSE 'effective' END AS state,
            concat_ws(':',s.workspace_id,s.period_type,s.period_start,s.scope_type,
              COALESCE(s.user_ref_id::text,'-'),COALESCE(s.team_id::text,'-'),s.department_code,s.kind) AS row_key,
            security.target_scope_write(s.scope_type,s.user_ref_id,s.team_id,s.department_code)
              AND m.active AND (security.authorization_subject('target.manage',s.scope_type,s.user_ref_id,s.team_id)
                OR (s.period_type='quarter' AND s.period_start=date_trunc('quarter',timezone('Asia/Shanghai',clock_timestamp()))::date)) AS editable,
            security.authorization_subject('target.manage',s.scope_type,s.user_ref_id,s.team_id) AS can_manage,
            t.id IS NOT NULL AND security.authorization_subject('target.history',s.scope_type,s.user_ref_id,s.team_id) AS can_history
          FROM identities s
          LEFT JOIN effective t ON t.scope_type=s.scope_type AND t.user_ref_id IS NOT DISTINCT FROM s.user_ref_id
            AND t.team_id IS NOT DISTINCT FROM s.team_id AND t.department_code=s.department_code AND t.kind=s.kind
          LEFT JOIN LATERAL (
            SELECT x.pending_change FROM pending x WHERE x.scope_type=s.scope_type
              AND x.user_ref_id IS NOT DISTINCT FROM s.user_ref_id AND x.team_id IS NOT DISTINCT FROM s.team_id
              AND x.department_code=s.department_code AND x.kind=s.kind
            ORDER BY x.pending_change->>'created_at' DESC,x.pending_change->>'request_id' DESC LIMIT 1
          ) p ON true
          JOIN crm.review_metric m ON m.workspace_id=s.workspace_id AND m.code=s.kind
          LEFT JOIN platform.user_ref u ON u.id=s.user_ref_id AND u.workspace_id=s.workspace_id
          LEFT JOIN platform.team tm ON tm.id=s.team_id AND tm.workspace_id=s.workspace_id
          LEFT JOIN platform.user_ref maintainer ON maintainer.id=t.updated_by_user_ref_id AND maintainer.workspace_id=s.workspace_id
          WHERE ($4::text IS NULL OR (s.scope_type=$4 AND s.user_ref_id IS NOT DISTINCT FROM $5::uuid
            AND s.team_id IS NOT DISTINCT FROM $6::uuid AND s.department_code=$7))
            AND ($8::text IS NULL OR s.kind=$8)
            AND ($9::text IS NULL OR COALESCE(u.display_name,tm.name,s.department_code) ILIKE '%'||$9||'%'
              OR u.account_code ILIKE '%'||$9||'%')
        )
        """
        values = [actor.workspace_id, period["type"], period["start"],
                  scope["scope_type"] if scope else None, scope["user_id"] if scope else None,
                  scope["team_id"] if scope else None, scope["department_code"] if scope else None, kind, q]
        total = await connection.fetchval(source + "SELECT count(*) FROM projected", *values)
        rows = await connection.fetch(source + "SELECT * FROM projected ORDER BY row_key LIMIT $10 OFFSET $11",
                                      *values, limit, offset)
        return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}
