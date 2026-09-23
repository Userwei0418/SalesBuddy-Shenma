from __future__ import annotations

from typing import Any

import asyncpg

from sales_backend.domain.agent import ActorContext


class DirectoryRepository:
    async def colleagues(self, connection, actor, *, ids=None):
        """Company-wide visit collaborators, excluding active FDE identities.

        Also used by the write validator; task assignment has a separate scope.
        """
        return await connection.fetch(
            """
            SELECT u.id::text, u.display_name AS name, u.account_code
              FROM platform.user_ref u
             WHERE u.workspace_id=$1::uuid AND u.status='active' AND u.deleted_at IS NULL
               AND ($2::uuid[] IS NULL OR u.id=ANY($2::uuid[]))
               AND NOT EXISTS (
                 SELECT 1 FROM platform.role_binding rb
                  WHERE rb.workspace_id=u.workspace_id AND rb.user_ref_id=u.id
                    AND rb.role_code IN ('fde','fde_lead')
                    AND clock_timestamp() >= rb.valid_from AND clock_timestamp() < rb.valid_to
               )
             ORDER BY u.display_name, u.id
            """, actor.workspace_id, ids)

    async def task_assignees(self, connection: asyncpg.Connection, actor: ActorContext, *,
                             opportunity_id=None, customer_id=None, target_role=None) -> list[dict[str, Any]]:
        rows = await connection.fetch(
            """
            SELECT DISTINCT ON (u.id) u.id::text, u.account_code, u.display_name AS name,
                   rb.role_code AS role, t.id::text AS team_id, COALESCE(t.name, '未分组') AS team
              FROM platform.user_ref u
              JOIN platform.role_binding rb ON rb.user_ref_id = u.id
               AND clock_timestamp() >= rb.valid_from AND clock_timestamp() < rb.valid_to
              LEFT JOIN platform.team_membership tm ON tm.user_ref_id = u.id AND tm.is_primary
               AND clock_timestamp() >= tm.valid_from AND clock_timestamp() < tm.valid_to
               AND (rb.role_code NOT IN ('fde','fde_lead') OR (tm.membership_role IN ('fde','fde_lead')
                 AND (rb.team_id IS NULL OR rb.team_id=tm.team_id)))
              LEFT JOIN platform.team t ON t.id = tm.team_id AND t.deleted_at IS NULL
             WHERE u.workspace_id = $1::uuid AND u.status = 'active' AND u.deleted_at IS NULL
               AND ($7::text IS NULL OR rb.role_code=$7)
               AND (rb.role_code NOT IN ('fde','fde_lead') OR security.fde_user_is_active(u.id,rb.role_code,t.id))
               AND (
                 ($2 = 'manager' AND rb.role_code IN ('sales','supervisor'))
                 OR ($2 = 'supervisor' AND rb.role_code = 'sales' AND tm.team_id = ANY($3::uuid[]))
                 OR ($2 = 'sales' AND rb.role_code = 'sales' AND tm.team_id = ANY($3::uuid[]))
                 OR ($2 IN ('operations','administrator')
                   AND rb.role_code IN ('sales','supervisor','manager','fde','fde_lead'))
                 OR ($2 IN ('fde','fde_lead') AND u.id=$4::uuid AND rb.role_code=$2)
                 OR ($2='fde_lead' AND rb.role_code IN ('fde','fde_lead') AND EXISTS(
                   SELECT 1 FROM platform.team_membership fm WHERE fm.user_ref_id=u.id
                   AND fm.team_id=ANY($3::uuid[])
                   AND clock_timestamp()>=fm.valid_from AND clock_timestamp()<fm.valid_to))
                 OR ($5::uuid IS NOT NULL AND (
                   (rb.role_code IN ('fde','fde_lead') AND EXISTS(SELECT 1 FROM crm.opportunity_participant p
                     WHERE p.opportunity_id=$5::uuid AND p.user_ref_id=u.id AND p.participant_role='fde'
                     AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to)
                     AND security.fde_user_is_active(u.id,rb.role_code,NULL))
                   OR ($2 IN ('fde','fde_lead') AND rb.role_code='sales' AND EXISTS(
                     SELECT 1 FROM crm.opportunity o WHERE o.id=$5::uuid
                       AND o.owner_user_ref_id=u.id AND o.deleted_at IS NULL))))
               )
               AND ($2 NOT IN ('fde','fde_lead') OR $5::uuid IS NULL OR EXISTS(
                 SELECT 1 FROM unnest($3::uuid[]) scope_team WHERE
                   security.fde_user_direct_opportunity_scope($4::uuid,$2,scope_team,$5::uuid)))
               AND ($5::uuid IS NULL OR EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=$5::uuid
                 AND o.deleted_at IS NULL AND ($6::uuid IS NULL OR o.customer_id=$6::uuid)))
             ORDER BY u.id, CASE rb.role_code WHEN 'supervisor' THEN 1 ELSE 2 END, u.display_name
            """,
            actor.workspace_id,
            actor.role.value,
            list(actor.team_ids),
            actor.user_id,
            opportunity_id,
            customer_id,
            target_role,
        )
        return [dict(row) for row in rows]

    async def members(self, connection: asyncpg.Connection, actor: ActorContext) -> list[dict[str, Any]]:
        if actor.role.value in {"fde", "fde_lead"}:
            from sales_backend.repositories.collaboration import scope_members

            return (await scope_members(connection, actor))[2]
        # Aggregate each fact independently. Joining all detail tables first
        # multiplies visits × opportunities × risks × tasks before DISTINCT.
        rows = await connection.fetch(
            """
            WITH members AS MATERIALIZED (
              SELECT DISTINCT u.id, u.account_code, u.display_name AS name,
                     rb.role_code AS role, t.id AS team_id, t.name AS team
                FROM platform.user_ref u
                JOIN platform.role_binding rb ON rb.user_ref_id = u.id
                 AND clock_timestamp() >= rb.valid_from AND clock_timestamp() < rb.valid_to
                LEFT JOIN platform.team_membership tm ON tm.user_ref_id = u.id
                 AND tm.is_primary AND clock_timestamp() >= tm.valid_from AND clock_timestamp() < tm.valid_to
                LEFT JOIN platform.team t ON t.id = tm.team_id AND t.deleted_at IS NULL
               WHERE u.workspace_id = $1::uuid AND u.status = 'active' AND u.deleted_at IS NULL
                 AND (
                   ($2 = 'manager' AND rb.role_code IN ('supervisor','sales'))
                   OR ($2 = 'supervisor' AND rb.role_code = 'sales' AND tm.team_id = ANY($4::uuid[]))
                   OR ($2 = 'sales' AND u.id = $3::uuid)
                 )
            )
            SELECT m.id::text, m.account_code, m.name, m.role, m.team_id::text, m.team,
                   v.visits, o.opportunities, r.risks, wt.completion
              FROM members m
              CROSS JOIN LATERAL (
                SELECT count(*) AS visits FROM activity.visit v
                 WHERE v.recorder_user_ref_id=m.id AND v.deleted_at IS NULL
              ) v
              CROSS JOIN LATERAL (
                SELECT count(*) AS opportunities FROM crm.opportunity o
                 WHERE o.owner_user_ref_id=m.id AND o.deleted_at IS NULL AND o.status='open'
              ) o
              CROSS JOIN LATERAL (
                SELECT count(*) AS risks FROM insight.risk r
                 WHERE r.owner_user_ref_id=m.id AND r.deleted_at IS NULL
                   AND r.status IN ('new','pending','in_progress','escalated')
              ) r
              CROSS JOIN LATERAL (
                SELECT COALESCE(round(100.0 * count(DISTINCT wt.id) FILTER (WHERE wt.status='completed')
                         / NULLIF(count(DISTINCT wt.id), 0)), 0) AS completion
                  FROM workflow.task_assignee wta
                  JOIN workflow.task wt ON wt.id=wta.task_id AND wt.deleted_at IS NULL
                 WHERE wta.assignee_user_ref_id=m.id AND wta.responsibility='owner'
              ) wt
             ORDER BY CASE m.role WHEN 'supervisor' THEN 1 ELSE 2 END, m.team, m.name
            """,
            actor.workspace_id,
            actor.role.value,
            actor.user_id,
            list(actor.team_ids),
        )
        return [dict(row) for row in rows]
