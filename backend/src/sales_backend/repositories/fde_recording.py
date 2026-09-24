"""Recording choices use direct membership, independently of customer-wide reading."""


async def recording_opportunity(connection, opportunity_id, customer_id):
    return await connection.fetchrow(
        "SELECT o.id::text,o.customer_id::text,o.name FROM crm.opportunity o "
        "WHERE o.id=$1::uuid AND o.customer_id=$2::uuid "
        "AND security.fde_can_record_opportunity(o.id)",
        opportunity_id,
        customer_id,
    )


async def recording_visit(connection, visit_id):
    return await connection.fetchrow(
        "SELECT customer_id::text,opportunity_id::text,created_by_user_ref_id::text,"
        "recorder_user_ref_id::text,confirmed_by_user_ref_id::text,status "
        "FROM activity.visit WHERE id=$1::uuid AND deleted_at IS NULL", visit_id,
    )


async def recording_opportunities(connection, *, customer_id=None, opportunity_id=None, query=None, limit=50, offset=0):
    predicate = """FROM crm.opportunity o JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
        WHERE security.fde_can_record_opportunity(o.id)
          AND ($1::uuid IS NULL OR o.customer_id=$1::uuid)
          AND ($2::uuid IS NULL OR o.id=$2::uuid)
          AND ($3::text IS NULL OR o.name ILIKE $3 OR c.name ILIKE $3)"""
    query = f"%{query.strip()}%" if query and query.strip() else None
    total = await connection.fetchval("SELECT count(*) " + predicate, customer_id, opportunity_id, query)
    rows = await connection.fetch(
        "SELECT o.id::text,o.customer_id::text,o.name,c.name AS customer_name,"
        "o.status,o.stage_code,o.probability,o.sales_channel,o.partner_id::text,o.partner_name,"
        "true AS can_record_visit "
        + predicate + " ORDER BY c.name,o.name,o.id LIMIT $4 OFFSET $5",
        customer_id, opportunity_id, query, limit, offset,
    )
    more = offset + limit < total
    return {"items": [dict(row) for row in rows], "total": total, "has_more": more,
            "next_offset": offset + limit if more else None}
