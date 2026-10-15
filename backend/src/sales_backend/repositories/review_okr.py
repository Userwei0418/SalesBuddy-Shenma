"""RLS-scoped O/KR reads and narrowly granted database mutations."""

# SQL fragments below are fixed source literals; all request values are bound.
# ruff: noqa: S608
from sales_backend.db import json_value
from sales_backend.repositories.opportunity_business_date import creation_date_projection


class ReviewOkrRepository:
    async def objectives(self, connection, *, objective_id=None, period=None, limit=50, offset=0):
        where = """ WHERE o.workspace_id=common.current_workspace_id()
          AND ($1::uuid IS NULL OR o.id=$1)
          AND ($2::date IS NULL OR (o.period_start<=$2 AND o.period_end>=$2))"""
        rows = await connection.fetch(
            """SELECT o.*,
          security.target_scope_write(o.scope_type,o.user_ref_id,o.team_id,o.department_code) AS editable,
          (SELECT count(*) FROM crm.review_key_result k WHERE k.objective_id=o.id AND k.workspace_id=o.workspace_id)
            AS key_result_count
          FROM crm.review_objective o"""
            + where
            + " ORDER BY o.created_at,o.id LIMIT $3 OFFSET $4",
            objective_id,
            period,
            limit,
            offset,
        )
        total = await connection.fetchval("SELECT count(*) FROM crm.review_objective o" + where, objective_id, period)
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "data_source": "database",
        }

    async def key_results(self, connection, *, key_result_id=None, objective_id=None, limit=50, offset=0):
        where = """ WHERE k.workspace_id=common.current_workspace_id()
          AND ($1::uuid IS NULL OR k.id=$1) AND ($2::uuid IS NULL OR k.objective_id=$2)"""
        rows = await connection.fetch(
            """SELECT k.*,to_jsonb(m) AS metric,o.status AS objective_status,
          (k.status='active' AND o.status='active' AND m.active) AS linkable,
          jsonb_build_object('type',o.period_type,'start',o.period_start,'end',o.period_end,
            'timezone','Asia/Shanghai') AS period,
          jsonb_build_object('scope_type',o.scope_type,'user_id',o.user_ref_id,'team_id',o.team_id,
            'department_code',o.department_code,'user_name',scope_user.display_name,
            'team_name',scope_team.name) AS scope,
          security.target_scope_write(o.scope_type,o.user_ref_id,o.team_id,o.department_code) AS editable,
          CASE WHEN t.id IS NULL THEN NULL ELSE jsonb_build_object('id',t.id,'version_no',t.version_no,
            'value',t.amount::text,'status','effective','kind',t.kind) END AS effective_target,
          CASE WHEN m.source_kind='manual' AND m.unit NOT IN ('元','万元','人民币','CNY','RMB')
            THEN 'manual' WHEN m.source_kind IN ('actual','forecast') THEN 'automatic'
            ELSE 'unavailable' END AS progress_mode,
          CASE WHEN p.id IS NULL THEN jsonb_build_object('value',NULL,'status','missing',
            'reason',CASE WHEN m.source_kind='manual' THEN 'no_confirmed_progress'
              ELSE 'unified_metric_projection_required' END)
          ELSE jsonb_build_object('value',p.value::text,'evidence',p.evidence,
            'confirmed_by_user_id',p.confirmed_by_user_id,
            'confirmed_at',p.created_at,'version_no',p.key_result_version,'status','confirmed','source_ref',p.id)
          END AS progress
          FROM crm.review_key_result k
          JOIN crm.review_objective o ON o.id=k.objective_id AND o.workspace_id=k.workspace_id
          LEFT JOIN platform.user_ref scope_user ON scope_user.id=o.user_ref_id
            AND scope_user.workspace_id=o.workspace_id
          LEFT JOIN platform.team scope_team ON scope_team.id=o.team_id AND scope_team.workspace_id=o.workspace_id
          JOIN crm.review_metric m ON m.workspace_id=k.workspace_id AND m.code=k.metric_code
          LEFT JOIN crm.sales_target t ON t.id=k.target_id AND t.workspace_id=k.workspace_id
          LEFT JOIN LATERAL (SELECT p.* FROM crm.review_key_result_progress p
            WHERE p.workspace_id=k.workspace_id AND p.key_result_id=k.id
            ORDER BY p.key_result_version DESC LIMIT 1) p ON true
          """
            + where
            + " ORDER BY k.created_at,k.id LIMIT $3 OFFSET $4",
            key_result_id,
            objective_id,
            limit,
            offset,
        )
        total = await connection.fetchval(
            "SELECT count(*) FROM crm.review_key_result k" + where, key_result_id, objective_id
        )
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "data_source": "database",
        }

    async def actual_facts(self, connection, *, scope, as_of):
        """Use the existing resolved actual view; tasks and forecasts cannot enter it."""
        allowed = await connection.fetchval(
            "SELECT security.authorization_subject('actual.read',$1,$2::uuid,$3::uuid)"
            " AND security.authorization_subject('customer.read',$1,$2::uuid,$3::uuid)"
            " AND security.authorization_subject('opportunity.read',$1,$2::uuid,$3::uuid)",
            scope["scope_type"],
            scope.get("user_id"),
            scope.get("team_id"),
        )
        if not allowed:
            return None
        rows = await connection.fetch(
            """SELECT a.* FROM crm.effective_actual a
              WHERE a.workspace_id=common.current_workspace_id()
                AND security.customer_reference(a.customer_id) IS NOT NULL
                AND (a.occurred_on IS NULL OR a.occurred_on<=$4::date)
                AND (($1='team' AND a.owner_team_id=$3::uuid)
                  OR ($1='person' AND a.owner_user_ref_id=$2::uuid))""",
            scope["scope_type"],
            scope.get("user_id"),
            scope.get("team_id"),
            as_of,
        )
        return [dict(row) for row in rows]

    async def count_facts(self, connection, *, scope, metric_code):
        """Read the visible cohort before date eligibility; unknown dates survive."""
        if metric_code not in {"opportunity_count", "visit_count"}:
            raise ValueError("未知自动计数指标")
        permission = "opportunity.read" if metric_code == "opportunity_count" else "visit.read"
        complete = await connection.fetchval(
            "SELECT security.authorization_subject($1,$2,$3::uuid,$4::uuid)",
            permission,
            scope["scope_type"],
            scope.get("user_id"),
            scope.get("team_id"),
        )
        # RLS and object checks still apply when only a subset of the subject is
        # authorized. A partial cohort can never establish a complete zero.
        if metric_code == "opportunity_count":
            query = (
                """SELECT o.id,o.version_no,o.status,o.owner_user_ref_id AS user_id,
                o.owner_team_id AS team_id,"""
                + creation_date_projection()
                + """ AS creation_date,
                security.authorization_opportunity('work_report.read',o.id) AS creation_history_readable,
                EXISTS(SELECT 1 FROM crm.opportunity_state_event e
                  WHERE e.workspace_id=o.workspace_id AND e.opportunity_id=o.id
                    AND e.mutation_kind='insert' AND e.history_origin='native_creation'
                    AND e.complete_from_origin) AS native_creation
              FROM crm.opportunity o WHERE o.workspace_id=common.current_workspace_id()
                AND o.deleted_at IS NULL AND security.authorization_opportunity('opportunity.read',o.id)
                AND (($1='person' AND (o.owner_user_ref_id=$2::uuid OR o.owner_user_ref_id IS NULL))
                  OR ($1='team' AND (o.owner_team_id=$3::uuid OR o.owner_team_id IS NULL)))"""
            )
        else:
            query = """SELECT v.id,v.version_no,v.status,v.recorder_user_ref_id AS user_id,
                v.recorder_team_id AS team_id,v.interaction_at
              FROM activity.visit v WHERE v.workspace_id=common.current_workspace_id()
                AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
                AND security.authorization_visit('visit.read',v.id)
                AND (($1='person' AND (v.recorder_user_ref_id=$2::uuid OR v.recorder_user_ref_id IS NULL))
                  OR ($1='team' AND (v.recorder_team_id=$3::uuid OR v.recorder_team_id IS NULL)))"""
        rows = await connection.fetch(query, scope["scope_type"], scope.get("user_id"), scope.get("team_id"))
        return {"items": [dict(row) for row in rows], "scope_complete": bool(complete)}

    async def save(self, connection, kind, entity_id, body, version=None):
        if kind not in {"objective", "key_result"}:
            raise ValueError("未知目标类型")
        return json_value(
            await connection.fetchval(
                f"SELECT security.save_review_{kind}($1::uuid,$2::jsonb,$3)", entity_id, body, version
            )
        )

    async def progress(self, connection, entity_id, body):
        return json_value(
            await connection.fetchval(
                "SELECT security.confirm_review_key_result_progress($1::uuid,$2,$3::numeric,$4,$5)",
                entity_id,
                body.version_no,
                body.value,
                body.evidence,
                body.confirmed,
            )
        )

    async def mention(self, connection, kind, entity_id, body):
        return json_value(
            await connection.fetchval(
                "SELECT security.mention_review_okr($1,$2::uuid,$3,$4::uuid[])",
                kind,
                entity_id,
                body.version_no,
                body.recipient_user_ids,
            )
        )
