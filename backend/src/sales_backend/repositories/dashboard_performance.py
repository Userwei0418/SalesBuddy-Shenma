"""Actor-scoped ranking inputs. No definer, impersonation or business writes."""
from sales_backend.db import json_value


async def cohort(connection, actor, *, actual):
    records = await connection.fetch(
        """/* dashboard_performance_cohort_v1 */
        WITH effective_person_roles AS (
          SELECT rb.user_ref_id,
            bool_or(rb.role_code IN ('sales','supervisor'))
              AND NOT bool_or(rb.role_code IN ('manager','fde','fde_lead','operations')) AS participates
          FROM platform.role_binding rb
          WHERE rb.workspace_id=$1::uuid
            AND rb.role_code IN ('sales','supervisor','manager','fde','fde_lead','operations')
            AND transaction_timestamp()>=rb.valid_from AND transaction_timestamp()<rb.valid_to
          GROUP BY rb.user_ref_id
        ), subjects AS (
          SELECT 'team'::text AS kind,t.id,t.name,NULL::uuid AS team_id,NULL::text AS team_name,
            true AS participates
          FROM platform.team t
          WHERE t.workspace_id=$1::uuid AND t.deleted_at IS NULL AND t.status='active'
            AND transaction_timestamp()>=t.valid_from AND transaction_timestamp()<t.valid_to
            AND NOT EXISTS(SELECT 1 FROM platform.team child WHERE child.workspace_id=t.workspace_id
              AND child.parent_team_id=t.id AND child.deleted_at IS NULL)
          UNION ALL
          SELECT 'person',u.id,u.display_name,primary_team.id,primary_team.name,roles.participates
          FROM platform.user_ref u JOIN effective_person_roles roles ON roles.user_ref_id=u.id
          LEFT JOIN LATERAL (
            SELECT t.id,t.name FROM platform.team_membership tm
            JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
            WHERE tm.workspace_id=u.workspace_id AND tm.user_ref_id=u.id
              AND transaction_timestamp()>=tm.valid_from AND transaction_timestamp()<tm.valid_to
              AND t.deleted_at IS NULL AND t.status='active'
              AND transaction_timestamp()>=t.valid_from AND transaction_timestamp()<t.valid_to
              AND security.authorization_subject('dashboard.ranking','team',NULL,t.id)
              AND security.authorization_subject('dashboard.read','team',NULL,t.id)
            ORDER BY tm.is_primary DESC,tm.valid_from DESC,tm.id LIMIT 1
          ) primary_team ON true
          WHERE u.workspace_id=$1::uuid AND u.deleted_at IS NULL AND u.status='active'
        ), scoped AS (
          SELECT *,CASE WHEN kind='person' THEN id END AS person_id,
                   CASE WHEN kind='team' THEN id END AS subject_team_id FROM subjects
        ) SELECT kind,id::text,name,team_id::text,team_name,participates,
          (security.authorization_subject('customer.read',kind,person_id,subject_team_id)
           AND security.authorization_subject('opportunity.read',kind,person_id,subject_team_id)
           AND (NOT $2::boolean OR security.authorization_subject('actual.read',kind,person_id,subject_team_id)))
             AS source_complete,
          (security.authorization_subject('target.read',kind,person_id,subject_team_id)
           AND security.target_scope_read(kind,person_id,subject_team_id,'sales')) AS target_allowed
        FROM scoped
        WHERE security.authorization_subject('dashboard.ranking',kind,person_id,subject_team_id)
          AND security.authorization_subject('dashboard.read',kind,person_id,subject_team_id)
        ORDER BY kind,id""", actor.workspace_id, actual,
    )
    return [dict(row) for row in records]


async def targets(connection, subjects, *, start, kind):
    allowed = [dict(scope=s["kind"], id=s["id"]) for s in subjects if s["target_allowed"]]
    if not allowed or kind is None:
        return {}
    rows = await connection.fetch(
        """/* dashboard_performance_targets_v1 */
        SELECT s.scope,s.id::text AS subject_id,t.id::text,t.kind,t.version_no,t.change_reason AS note,
          CASE WHEN (COALESCE(t.source,'') LIKE '%Q4目标表 revision54 导入%'
                     OR COALESCE(t.change_reason,'') LIKE '%Q4目标表 revision54 导入%')
               THEN (t.amount*10000)::text ELSE t.amount::text END AS value
        FROM jsonb_to_recordset($1::jsonb) AS s(scope text,id uuid)
        JOIN crm.sales_target t ON t.workspace_id=common.current_workspace_id() AND t.scope_type=s.scope
          AND t.user_ref_id IS NOT DISTINCT FROM CASE WHEN s.scope='person' THEN s.id END
          AND t.team_id IS NOT DISTINCT FROM CASE WHEN s.scope='team' THEN s.id END
        WHERE t.period_type='quarter' AND t.period_start=$2 AND t.department_code='sales' AND t.kind=$3
          AND security.authorization_subject('target.read',s.scope,
            CASE WHEN s.scope='person' THEN s.id END,CASE WHEN s.scope='team' THEN s.id END)
          AND security.target_scope_read(s.scope,CASE WHEN s.scope='person' THEN s.id END,
            CASE WHEN s.scope='team' THEN s.id END,'sales')""", allowed, start, kind,
    )
    return {(r["scope"], r["subject_id"]): dict(r) for r in rows}


async def ranked_facts(connection, rows, *, actual):
    if not rows:
        return []
    inputs = [dict(idx=i, opportunity_id=r.get("opportunity_id") if actual else r["id"],
                   customer_id=r["customer_id"], owner_id=r.get("owner_id"), team_id=r.get("team_id"),
                   unresolved=actual and r.get("attribution_status") == "unresolved")
              for i, r in enumerate(rows)]
    allowed = await connection.fetch(
        """/* dashboard_performance_ranked_facts_v1 */
        SELECT r.idx FROM jsonb_to_recordset($1::jsonb)
          AS r(idx integer,opportunity_id uuid,customer_id uuid,owner_id uuid,team_id uuid,unresolved boolean)
        WHERE (r.opportunity_id IS NOT NULL
                 AND security.authorization_opportunity('dashboard.ranking',r.opportunity_id)
               OR r.opportunity_id IS NULL AND security.authorization_customer('dashboard.ranking',r.customer_id))
          AND (NOT $2::boolean OR r.unresolved OR security.authorization_allows('dashboard.ranking',
            common.current_workspace_id(),r.owner_id,ARRAY[r.team_id],r.owner_id=common.current_user_ref_id()))
        ORDER BY r.idx""", inputs, actual,
    )
    return [rows[r["idx"]] for r in allowed]


async def material_rows(connection, ids):
    if not ids:
        return {}, {}
    parents = await connection.fetch(
        """/* dashboard_performance_material_parents_v1 */
        SELECT id::text,trade_relation FROM crm.opportunity
        WHERE workspace_id=common.current_workspace_id() AND id=ANY($1::uuid[]) AND deleted_at IS NULL
          AND security.authorization_opportunity('opportunity.read',id)
          AND security.authorization_opportunity('dashboard.read',id)
          AND security.authorization_opportunity('dashboard.ranking',id)""", ids,
    )
    rows = await connection.fetch(
        """/* dashboard_performance_material_rows_v1 */
        SELECT m.* FROM crm.opportunity_material m
        WHERE m.workspace_id=common.current_workspace_id() AND m.opportunity_id=ANY($1::uuid[]) AND m.is_active
          AND security.authorization_opportunity('opportunity.read',m.opportunity_id)
          AND security.authorization_opportunity('dashboard.read',m.opportunity_id)
          AND security.authorization_opportunity('dashboard.ranking',m.opportunity_id)
        ORDER BY m.opportunity_id,m.line_no,m.id""", [p["id"] for p in parents],
    )
    grouped = {}
    for raw in rows:
        row = dict(raw)
        # Defer malformed legacy snapshots to the per-opportunity calculator;
        # one bad source must not discard every other authorized subject.
        grouped.setdefault(str(row["opportunity_id"]), []).append(row)
    return {p["id"]: p["trade_relation"] for p in parents}, grouped


def material_line(raw, relation):
    return {**raw, "catalog_snapshot": json_value(raw["catalog_snapshot"]), "trade_relation": relation}
