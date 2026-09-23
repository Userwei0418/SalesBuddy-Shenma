"""Opportunity persistence only; services/opportunities.py owns the business workflow."""

from sales_backend.db import json_value


async def customer_for_opportunity(connection, customer_id):
    return json_value(await connection.fetchval("SELECT security.customer_reference($1::uuid)", customer_id))


async def validate_opportunity_owner(connection, user_id):
    row = await connection.fetchrow(
        """SELECT u.id::text AS user_id,m.team_id::text FROM platform.user_ref u
    JOIN platform.role_binding r ON r.user_ref_id=u.id AND r.role_code='sales' AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to
    JOIN platform.team_membership m ON m.user_ref_id=u.id AND clock_timestamp()>=m.valid_from AND clock_timestamp()<m.valid_to
    JOIN platform.team t ON t.id=m.team_id AND t.status='active' AND t.deleted_at IS NULL
    WHERE u.id=$1::uuid AND u.status='active' AND u.deleted_at IS NULL ORDER BY m.is_primary DESC,m.created_at DESC LIMIT 1""",
        user_id,
    )
    if not row:
        raise ValueError("请选择本公司有效的销售负责人")
    return dict(row)


async def lock_opportunity(connection, opportunity_id, customer_id):
    return await connection.fetchrow(
        "SELECT * FROM crm.opportunity WHERE id=$1::uuid AND customer_id=$2::uuid AND deleted_at IS NULL FOR UPDATE",
        opportunity_id,
        customer_id,
    )


async def forecasts(connection, opportunity_id):
    rows = await connection.fetch(
        """SELECT year,quarter,recognized_amount,collection_amount
        FROM crm.opportunity_forecast WHERE opportunity_id=$1::uuid ORDER BY year,quarter""",
        opportunity_id,
    )
    return [dict(r) for r in rows]


async def write_opportunity(connection, actor, customer, customer_id, oid, after, stage, updating, *, owner=None):
    values = [
        after[k]
        for k in ("name", "probability", "status", "amount", "expected_close_date", "partner_name", "product_line")
    ]
    if updating:
        version = await connection.fetchval(
            """UPDATE crm.opportunity SET name=$2,probability=$3,status=$4,
            amount=$5,expected_close_date=$6,partner_name=$7,product_line=$8,stage_code=$9,
            closed_at=CASE WHEN $4='open' THEN NULL WHEN status<>$4 THEN clock_timestamp() ELSE closed_at END
             ,follow_up_plan=$10,sales_channel=$11,partner_id=$12::uuid WHERE id=$1::uuid RETURNING version_no""",
            oid,
            *values,
            stage,
            after.get("follow_up_plan"),
            after['sales_channel'], after['partner_id'],
        )
    else:
        version = await connection.fetchval(
            """INSERT INTO crm.opportunity
            (id,name,probability,status,amount,expected_close_date,partner_name,product_line,stage_code,
             workspace_id,customer_id,owner_user_ref_id,owner_team_id,created_by_user_ref_id,source_code,closed_at,follow_up_plan,sales_channel,partner_id)
            VALUES($1::uuid,$2,$3,$4,$5,$6,$7,$8,$9,$10::uuid,$11::uuid,$12::uuid,$13::uuid,$14::uuid,
             'manual',CASE WHEN $4='open' THEN NULL ELSE clock_timestamp() END,$15,$16,$17::uuid) RETURNING version_no""",
            oid,
            *values,
            stage,
            actor.workspace_id,
            customer_id,
            owner["user_id"] if owner else actor.user_id,
            owner["team_id"] if owner else actor.team_ids[0] if actor.team_ids else customer["owner_team_id"],
            actor.user_id,
            after.get("follow_up_plan"),
            after['sales_channel'], after['partner_id'],
        )
    return version


async def write_forecasts(connection, actor, oid, updates):
    for row in updates or []:
        await connection.execute(
            """INSERT INTO crm.opportunity_forecast
            (workspace_id,opportunity_id,year,quarter,recognized_amount,collection_amount,updated_by_user_ref_id)
            VALUES($1::uuid,$2::uuid,$3,$4,$5,$6,$7::uuid)
            ON CONFLICT(opportunity_id,year,quarter) DO UPDATE SET
            recognized_amount=EXCLUDED.recognized_amount,collection_amount=EXCLUDED.collection_amount,
            updated_by_user_ref_id=EXCLUDED.updated_by_user_ref_id,updated_at=clock_timestamp()""",
            actor.workspace_id,
            oid,
            row["year"],
            row["quarter"],
            row.get("recognized_amount"),
            row.get("collection_amount"),
            actor.user_id,
        )
