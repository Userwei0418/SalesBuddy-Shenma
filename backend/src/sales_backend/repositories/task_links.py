# ruff: noqa: S608 -- Only the fixed scope expression is interpolated; all values are bound.
"""Minimal, paged task link choices with the same scope as task creation."""

# This action keeps its own scopes. Merely receiving a task grants no access
# to its linked opportunity, and an FDE's broader panorama read is not a write grant.
_SCOPE = "security.authorization_opportunity('task.create_customer',o.id)"


class TaskLinkRepository:
    async def allowed(self, connection, actor, opportunity_id):
        return await connection.fetchval(
            f"SELECT EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=$1::uuid "
            f"AND o.deleted_at IS NULL AND ({_SCOPE}))",
            opportunity_id,
        )

    async def customers(self, connection, actor, *, query="", limit=20, offset=0):
        rows = await connection.fetch(
            f"""WITH customer_ids AS (
              SELECT DISTINCT o.customer_id FROM crm.opportunity o
              WHERE o.deleted_at IS NULL AND ({_SCOPE})
            ), choices AS (
              SELECT customer_id::text AS id,
                security.customer_reference(customer_id)->>'name' AS name
              FROM customer_ids
            ) SELECT id,name FROM choices WHERE name IS NOT NULL
              AND ($1::text='' OR name ILIKE '%' || $1 || '%')
              ORDER BY name,id LIMIT $2 OFFSET $3""",
            query, limit + 1, offset,
        )
        return self._page(rows, limit, offset)

    async def opportunities(self, connection, actor, *, customer_id, opportunity_id=None,
                            query="", limit=20, offset=0):
        rows = await connection.fetch(
            f"""SELECT o.id::text,o.name,o.customer_id::text FROM crm.opportunity o
              WHERE o.deleted_at IS NULL AND ({_SCOPE}) AND o.customer_id=$1::uuid
                AND ($2::text='' OR o.name ILIKE '%' || $2 || '%')
                AND ($5::uuid IS NULL OR o.id=$5::uuid)
              ORDER BY o.name,o.id LIMIT $3 OFFSET $4""",
            customer_id,
            query, limit + 1, offset, opportunity_id,
        )
        return self._page(rows, limit, offset)

    @staticmethod
    def _page(rows, limit, offset):
        more = len(rows) > limit
        return {"items": [dict(row) for row in rows[:limit]], "has_more": more,
                "next_offset": offset + limit if more else None}
