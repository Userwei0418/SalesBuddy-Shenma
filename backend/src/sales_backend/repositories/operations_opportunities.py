from uuid import uuid4

from sales_backend.repositories.opportunity_mutations import forecasts
from sales_backend.repositories.opportunity_product_lines import attach_product_lines


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
        records = await connection.fetch(
            """WITH filtered AS MATERIALIZED (
            SELECT o.id,o.stage_code,o.probability,o.status,o.amount,o.updated_at
            FROM crm.opportunity o LEFT JOIN crm.customer c ON c.id=o.customer_id AND c.deleted_at IS NULL
            WHERE o.deleted_at IS NULL AND ($1::text IS NULL OR o.name ILIKE '%'||$1||'%' OR COALESCE(c.name,security.customer_reference(o.customer_id)->>'name') ILIKE '%'||$1||'%')
            AND ($2::uuid IS NULL OR o.customer_id=$2) AND ($3::uuid IS NULL OR o.owner_user_ref_id=$3)
            AND ($4::text IS NULL OR o.status=$4) AND ($5::integer IS NULL OR o.probability=$5)
            AND ($6::date IS NULL OR o.expected_close_date>=$6) AND ($7::date IS NULL OR o.expected_close_date<=$7)
            ), page AS MATERIALIZED (
              SELECT id FROM filtered ORDER BY updated_at DESC,id LIMIT $8 OFFSET $9
            )
            SELECT 0 AS _row_kind,0 AS _stage_count,
              o.id::text,o.customer_id::text,COALESCE(c.name,security.customer_reference(o.customer_id)->>'name') AS customer_name,o.name,o.stage_code,o.probability,o.status,
              o.amount,o.currency,o.expected_close_date,o.owner_user_ref_id::text,u.display_name AS owner_name,
              o.partner_name,o.partner_id::text,o.sales_channel,o.product_line,o.version_no,o.created_at,o.updated_at,
              security.authorization_opportunity('opportunity.update',o.id) AS can_edit,
              security.authorization_opportunity('opportunity.quote_create',o.id) AS can_quote
            FROM page p JOIN crm.opportunity o ON o.id=p.id LEFT JOIN crm.customer c ON c.id=o.customer_id AND c.deleted_at IS NULL
            LEFT JOIN platform.user_ref u ON u.id=o.owner_user_ref_id
            UNION ALL
            SELECT 1,count(*)::integer,
              NULL::text,NULL::text,NULL::text,NULL::text,stage_code,probability,status,
              sum(amount),NULL::character(3),NULL::date,NULL::text,NULL::text,
              NULL::text,NULL::text,NULL::text,NULL::text,NULL::integer,NULL::timestamptz,NULL::timestamptz,NULL::boolean,NULL::boolean
            FROM filtered GROUP BY stage_code,status,probability
            ORDER BY _row_kind,updated_at DESC,id,probability""",
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
        # Typed summary rows avoid building full-set JSON or changing Decimal/
        # date values. They survive an empty page and share its statement snapshot.
        stages = [
            {"stage_code": r["stage_code"], "status": r["status"], "probability": r["probability"],
             "count": r["_stage_count"], "amount": r["amount"]}
            for r in records if r["_row_kind"] == 1
        ]
        rows = [{key: value for key, value in dict(r).items() if key not in {"_row_kind", "_stage_count"}}
                for r in records if r["_row_kind"] == 0]
        await attach_product_lines(connection, rows)
        total = sum(r["count"] for r in stages)
        total_amount = sum(r["amount"] or 0 for r in stages)
        # SQL SUM remains null when every matching amount is unknown.
        row_amount = total_amount if any(r["amount"] is not None for r in stages) else None
        return {
            "items": [{**dict(r), "total_count": total, "total_amount": row_amount} for r in rows],
            "total": total,
            "total_amount": total_amount,
            "stages": [dict(r) for r in stages],
        }

    async def detail(self, connection, opportunity_id):
        row = await connection.fetchrow(
            """SELECT o.*,o.id::text,o.customer_id::text,o.owner_user_ref_id::text,
            COALESCE(c.name,security.customer_reference(o.customer_id)->>'name') AS customer_name,u.display_name AS owner_name,
            security.authorization_opportunity('opportunity.update',o.id) AS can_edit,
            security.authorization_opportunity('opportunity.quote_create',o.id) AS can_quote FROM crm.opportunity o LEFT JOIN crm.customer c ON c.id=o.customer_id AND c.deleted_at IS NULL
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
        item = (await attach_product_lines(connection, [dict(row)]))[0]
        return {
            **item,
            "quarterly_forecasts": await forecasts(connection, opportunity_id),
            "visits": [dict(r) for r in visits],
            "changes": [dict(r) for r in changes],
            "quote_references": [dict(r) for r in quotes],
        }

    async def attach_quote(self, connection, actor, opportunity_id, data):
        from sales_backend.repositories.authorization_checks import require_permission

        await require_permission(connection, "opportunity.quote_create", opportunity_id=opportunity_id)
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
