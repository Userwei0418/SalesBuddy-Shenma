from uuid import uuid4

from sales_backend.repositories.opportunity_mutations import forecasts


class OperationsOpportunityRepository:
    async def list(
        self,
        connection,
        *,
        q=None,
        customer=None,
        owner=None,
        status=None,
        probability=None,
        close_from=None,
        close_to=None,
        limit=50,
        offset=0,
    ):
        rows = await connection.fetch(
            """SELECT o.id::text,o.customer_id::text,c.name AS customer_name,o.name,o.stage_code,o.probability,o.status,
            o.amount,o.currency,o.expected_close_date,o.owner_user_ref_id::text,u.display_name AS owner_name,
            o.partner_name,o.partner_id::text,o.sales_channel,o.product_line,o.version_no,o.created_at,o.updated_at,count(*) OVER()::integer AS total_count,
            sum(o.amount) OVER() AS total_amount FROM crm.opportunity o JOIN crm.customer c ON c.id=o.customer_id
            LEFT JOIN platform.user_ref u ON u.id=o.owner_user_ref_id
            WHERE o.deleted_at IS NULL AND ($1::text IS NULL OR o.name ILIKE '%'||$1||'%' OR c.name ILIKE '%'||$1||'%')
            AND ($2::uuid IS NULL OR o.customer_id=$2) AND ($3::uuid IS NULL OR o.owner_user_ref_id=$3)
            AND ($4::text IS NULL OR o.status=$4) AND ($5::integer IS NULL OR o.probability=$5)
            AND ($6::date IS NULL OR o.expected_close_date>=$6) AND ($7::date IS NULL OR o.expected_close_date<=$7)
            ORDER BY o.updated_at DESC,o.id LIMIT $8 OFFSET $9""",
            q,
            customer,
            owner,
            status,
            probability,
            close_from,
            close_to,
            limit,
            offset,
        )
        stages = await connection.fetch(
            """SELECT o.stage_code,o.status,o.probability,count(*)::integer AS count,sum(o.amount) AS amount
            FROM crm.opportunity o JOIN crm.customer c ON c.id=o.customer_id WHERE o.deleted_at IS NULL
            AND ($1::text IS NULL OR o.name ILIKE '%'||$1||'%' OR c.name ILIKE '%'||$1||'%')
            AND ($2::uuid IS NULL OR o.customer_id=$2) AND ($3::uuid IS NULL OR o.owner_user_ref_id=$3)
            AND ($4::text IS NULL OR o.status=$4) AND ($5::integer IS NULL OR o.probability=$5)
            AND ($6::date IS NULL OR o.expected_close_date>=$6) AND ($7::date IS NULL OR o.expected_close_date<=$7)
            GROUP BY o.stage_code,o.status,o.probability ORDER BY o.probability""",
            q,
            customer,
            owner,
            status,
            probability,
            close_from,
            close_to,
        )
        return {
            "items": [dict(r) for r in rows],
            "total": sum(r["count"] for r in stages),
            "total_amount": sum(r["amount"] or 0 for r in stages),
            "stages": [dict(r) for r in stages],
        }

    async def detail(self, connection, opportunity_id):
        row = await connection.fetchrow(
            """SELECT o.*,o.id::text,o.customer_id::text,o.owner_user_ref_id::text,
            c.name AS customer_name,u.display_name AS owner_name FROM crm.opportunity o JOIN crm.customer c ON c.id=o.customer_id
            LEFT JOIN platform.user_ref u ON u.id=o.owner_user_ref_id WHERE o.id=$1::uuid AND o.deleted_at IS NULL""",
            opportunity_id,
        )
        if not row:
            raise LookupError("商机不存在")
        visits = await connection.fetch(
            """SELECT v.id::text,v.interaction_at,v.follow_up_record,v.next_action,v.created_at,u.display_name AS recorder
            FROM activity.visit v LEFT JOIN platform.user_ref u ON u.id=v.recorder_user_ref_id WHERE v.opportunity_id=$1::uuid AND v.deleted_at IS NULL ORDER BY v.interaction_at DESC LIMIT 100""",
            opportunity_id,
        )
        changes = await connection.fetch(
            """SELECT b.id::text,b.changes,b.created_at,u.display_name AS operator FROM crm.business_change b
            LEFT JOIN platform.user_ref u ON u.id=b.actor_user_ref_id WHERE b.opportunity_id=$1::uuid ORDER BY b.created_at DESC LIMIT 100""",
            opportunity_id,
        )
        quotes = await connection.fetch(
            "SELECT id::text,reference_no,title,url,amount,created_at FROM crm.opportunity_quote_reference WHERE opportunity_id=$1::uuid ORDER BY created_at DESC",
            opportunity_id,
        )
        return {
            **dict(row),
            "quarterly_forecasts": await forecasts(connection, opportunity_id),
            "visits": [dict(r) for r in visits],
            "changes": [dict(r) for r in changes],
            "quote_references": [dict(r) for r in quotes],
        }

    async def attach_quote(self, connection, actor, opportunity_id, data):
        if not await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM crm.opportunity WHERE id=$1::uuid AND deleted_at IS NULL)", opportunity_id
        ):
            raise LookupError("商机不存在")
        ref_id = str(uuid4())
        await connection.execute(
            "INSERT INTO crm.opportunity_quote_reference(id,workspace_id,opportunity_id,reference_no,title,url,amount,created_by_user_ref_id) VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5,$6,$7,$8::uuid)",
            ref_id,
            actor.workspace_id,
            opportunity_id,
            data["reference_no"].strip(),
            data["title"].strip(),
            str(data["url"]) if data["url"] else None,
            data["amount"],
            actor.user_id,
        )
        return {"id": ref_id}
