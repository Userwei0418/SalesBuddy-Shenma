from __future__ import annotations

from typing import Any

import asyncpg


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
                FROM workflow.notification
                WHERE ($1::boolean = false OR status <> 'read')
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
            UPDATE workflow.notification
               SET status = 'read', read_at = COALESCE(read_at, clock_timestamp())
             WHERE id = $1::uuid AND status <> 'read'
            """,
            notification_id,
        )
        return result.endswith("1")
