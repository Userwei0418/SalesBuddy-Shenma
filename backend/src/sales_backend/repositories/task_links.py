# ruff: noqa: S608 -- Only the fixed scope expression is interpolated; all values are bound.
"""Minimal, paged task link choices with the same scope as task creation."""

# Receiving a task never satisfies this predicate. FDE creation requires direct
# participation, while sales and management retain their existing business scope.
_SCOPE = """CASE WHEN $2::text IN ('fde','fde_lead') THEN
    EXISTS(SELECT 1 FROM unnest($3::uuid[]) team WHERE
      security.fde_user_direct_opportunity_scope($1::uuid,$2,team,o.id))
    ELSE security.has_opportunity_access(o.id) END"""


class TaskLinkRepository:
    async def allowed(self, connection, actor, opportunity_id):
        return await connection.fetchval(
            f"SELECT EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=$4::uuid "
            f"AND o.deleted_at IS NULL AND ({_SCOPE}))",
            actor.user_id, actor.role.value, list(actor.team_ids), opportunity_id,
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
              AND ($4::text='' OR name ILIKE '%' || $4 || '%')
              ORDER BY name,id LIMIT $5 OFFSET $6""",
            actor.user_id, actor.role.value, list(actor.team_ids), query, limit + 1, offset,
        )
        return self._page(rows, limit, offset)

    async def opportunities(self, connection, actor, *, customer_id, opportunity_id=None,
                            query="", limit=20, offset=0):
        rows = await connection.fetch(
            f"""SELECT o.id::text,o.name,o.customer_id::text FROM crm.opportunity o
              WHERE o.deleted_at IS NULL AND ({_SCOPE}) AND o.customer_id=$4::uuid
                AND ($5::text='' OR o.name ILIKE '%' || $5 || '%')
                AND ($8::uuid IS NULL OR o.id=$8::uuid)
              ORDER BY o.name,o.id LIMIT $6 OFFSET $7""",
            actor.user_id, actor.role.value, list(actor.team_ids), customer_id,
            query, limit + 1, offset, opportunity_id,
        )
        return self._page(rows, limit, offset)

    @staticmethod
    def _page(rows, limit, offset):
        more = len(rows) > limit
        return {"items": [dict(row) for row in rows[:limit]], "has_more": more,
                "next_offset": offset + limit if more else None}
