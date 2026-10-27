# ruff: noqa: S608 -- Visibility SQL is a fixed server-owned predicate; values remain bound.
from __future__ import annotations

from typing import Any

import asyncpg

# Evaluate before LIMIT and again on mark-read. A stored notification is not a
# durable grant to its task. Pause reminders also belong to one live round.
NOTIFICATION_VISIBLE_SQL = """n.status <> 'cancelled' AND (
    n.object_type IS DISTINCT FROM 'task' OR (
      n.object_id IS NOT NULL AND security.authorization_task('task.read',n.object_id)
      AND (n.template_code <> 'task_pause_recheck' OR EXISTS (
        SELECT 1 FROM workflow.task t JOIN workflow.task_execution x ON x.task_id=t.id
        WHERE t.id=n.object_id AND t.workspace_id=n.workspace_id AND t.deleted_at IS NULL
          AND t.execution_mode IN ('execution_v1','execution_v2') AND t.status NOT IN ('completed','cancelled')
          AND x.state->'pause'->>'id'=n.payload->>'pause_id'
          AND x.state->'pause'->>'recheck_version'=n.payload->>'recheck_version'
          AND (x.state->'pause'->>'recheck_at')::timestamptz<=statement_timestamp()
          AND ((x.state->>'current_reviewer'=common.current_user_ref_id()::text
                AND security.authorization_task('task.review',t.id))
            OR (x.state->'pause'->>'actor_user_id'=common.current_user_ref_id()::text
                AND security.authorization_task('task.coordinate',t.id))
            OR (security.authorization_task('task.complete',t.id) AND EXISTS(
              SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=t.id
                AND a.responsibility='owner' AND a.assignee_user_ref_id=common.current_user_ref_id()
                AND security.task_recipient_eligible(t.id,a.assignee_user_ref_id,a.assignee_role,a.assignee_team_id))))
      ))
    ))"""


class NotificationRepository:
    async def list(
        self,
        connection: asyncpg.Connection,
        *,
        unread_only: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows = await connection.fetch(
            """
            WITH page AS MATERIALIZED (
                SELECT id, channel_code, template_code, title, body,
                       object_type, object_id, status, payload,
                       scheduled_at, read_at, created_at
                FROM workflow.notification n
                WHERE """
            + NOTIFICATION_VISIBLE_SQL
            + """
                  AND ($1::boolean = false OR status <> 'read')
                ORDER BY created_at DESC, id DESC
                LIMIT $2
            )
            SELECT id::text, channel_code, template_code, title, body,
                   object_type, object_id::text, status,
                   CASE WHEN template_code = 'customer_claim' AND object_type = 'customer'
                        THEN COALESCE(payload, '{}'::jsonb) || jsonb_build_object(
                            'customer_name', security.customer_reference(object_id)->>'name')
                        ELSE payload END AS payload,
                   scheduled_at, read_at, created_at
            FROM page
            ORDER BY created_at DESC, id DESC
            """,
            unread_only,
            limit,
        )
        return [dict(row) for row in rows]

    async def mark_read(self, connection: asyncpg.Connection, *, notification_id: str) -> bool:
        result = await connection.execute(
            """
            UPDATE workflow.notification n
               SET status = 'read', read_at = COALESCE(read_at, clock_timestamp())
             WHERE id = $1::uuid AND status <> 'read' AND """
            + NOTIFICATION_VISIBLE_SQL
            + """
            """,
            notification_id,
        )
        return result.endswith("1")
