# ruff: noqa: S608 -- Only fixed server-owned SELECT fragments are concatenated.
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
        from sales_backend.repositories.task_targets import TaskTargetRepository

        return await TaskTargetRepository().recipients(connection, actor, target_role=target_role, business_only=True)

    async def members(self, connection: asyncpg.Connection, actor: ActorContext, *,
                      permission="directory.read", include_stats=True) -> list[dict[str, Any]]:
        from sales_backend.repositories.authorization_checks import require_permission
        await require_permission(connection, permission)
        # Base identities never depend on business fact tables. Legacy clients
        # may request the existing aggregates, each still protected by resource RLS.
        base_query = """
            WITH members AS MATERIALIZED (
              SELECT DISTINCT ON (u.id) u.id, u.account_code, u.display_name AS name,
                     rb.role_code AS role, t.id AS team_id, t.name AS team
                FROM platform.user_ref u
                JOIN platform.role_binding rb ON rb.user_ref_id=u.id AND rb.workspace_id=u.workspace_id
                 AND rb.role_code IN ('sales','supervisor','manager','fde','fde_lead')
                 AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
                LEFT JOIN platform.team_membership tm ON tm.user_ref_id=u.id AND tm.workspace_id=u.workspace_id
                 AND tm.is_primary AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
                LEFT JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=u.workspace_id
                 AND t.deleted_at IS NULL AND t.status='active'
                 AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
               WHERE u.workspace_id=$1::uuid AND u.status='active' AND u.deleted_at IS NULL
                 AND security.authorization_subject($2,'person',u.id,NULL)
               ORDER BY u.id, CASE rb.role_code WHEN 'manager' THEN 0 WHEN 'supervisor' THEN 1
                 WHEN 'sales' THEN 2 WHEN 'fde_lead' THEN 3 ELSE 4 END, t.id
            )
            SELECT m.id::text,m.account_code,m.name,m.role,m.team_id::text,m.team
        """
        if not include_stats:
            query = base_query + " FROM members m ORDER BY m.team,m.name,m.id"
        else:
            query = base_query + """,
              v.visits,o.opportunities,r.risks,wt.completion
              FROM members m
              CROSS JOIN LATERAL (SELECT count(*) AS visits FROM activity.visit v
                WHERE v.recorder_user_ref_id=m.id AND v.deleted_at IS NULL) v
              CROSS JOIN LATERAL (SELECT count(*) AS opportunities FROM crm.opportunity o
                WHERE o.owner_user_ref_id=m.id AND o.deleted_at IS NULL AND o.status='open') o
              CROSS JOIN LATERAL (SELECT count(*) AS risks FROM insight.risk r
                WHERE r.owner_user_ref_id=m.id AND r.deleted_at IS NULL
                AND r.status <> 'resolved') r
              CROSS JOIN LATERAL (
                SELECT COALESCE(round(100.0 * count(DISTINCT wt.id) FILTER(WHERE wt.status='completed')
                  / NULLIF(count(DISTINCT wt.id),0)),0) AS completion
                FROM workflow.task_assignee wta JOIN workflow.task wt ON wt.id=wta.task_id AND wt.deleted_at IS NULL
                WHERE wta.assignee_user_ref_id=m.id AND wta.responsibility='owner') wt
              ORDER BY m.team,m.name,m.id
            """
        rows = await connection.fetch(query, actor.workspace_id, permission)
        return [dict(row) for row in rows]
