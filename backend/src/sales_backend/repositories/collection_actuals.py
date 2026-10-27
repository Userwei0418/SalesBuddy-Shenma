"""Narrow opportunity-scoped projection; no grant of general financial access."""

from sales_backend.db import json_value


async def read_collection_actual(connection, opportunity_id, year, quarter):
    row = await connection.fetchrow(
        """SELECT o.id::text,
      security.authorization_opportunity('opportunity.update',o.id) AS can_edit,
      security.opportunity_collection_actual(o.id,$2,$3) AS actual
      FROM crm.opportunity o WHERE o.id=$1::uuid AND o.workspace_id=common.current_workspace_id()
       AND o.deleted_at IS NULL AND security.authorization_opportunity('opportunity.read',o.id)""",
        str(opportunity_id),
        year,
        quarter,
    )
    if not row:
        raise LookupError("商机不存在或不在当前权限范围内")
    return {
        "opportunity_id": row["id"],
        "period": {"year": year, "quarter": quarter},
        "can_edit": row["can_edit"],
        "actual": json_value(row["actual"]),
    }
