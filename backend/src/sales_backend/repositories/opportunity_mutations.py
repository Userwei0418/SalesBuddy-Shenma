"""Opportunity persistence only; services/opportunities.py owns the business workflow."""

from sales_backend.db import json_value
from sales_backend.repositories.opportunity_fields import PERSISTED_FIELDS
from sales_backend.repositories.opportunity_history import native_creation


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
    # Visit review validation reaches this lock before save_opportunity. Keep
    # every caller in the same finance-before-tuple order as V212 and bulk edits.
    await connection.execute("SELECT security.finance_write_lock()")
    return await connection.fetchrow(
        "SELECT * FROM crm.opportunity WHERE id=$1::uuid AND customer_id=$2::uuid AND deleted_at IS NULL FOR UPDATE",
        opportunity_id,
        customer_id,
    )


async def forecasts(connection, opportunity_id):
    rows = await connection.fetch(
        """SELECT year,quarter,recognized_amount,collection_amount,collection_confidence
        FROM crm.opportunity_forecast WHERE opportunity_id=$1::uuid ORDER BY year,quarter""",
        opportunity_id,
    )
    return [dict(r) for r in rows]


async def write_opportunity(connection, actor, customer, customer_id, oid, after, stage, updating, *, owner=None):
    values = [
        after[k]
        for k in ("name", "probability", "status", "amount", "expected_close_date", "partner_name", "product_line")
    ]
    profile_keys = [key for key in PERSISTED_FIELDS if key in after]
    profile_values = [after[key] for key in profile_keys]
    def profile_slot(index, key):
        cast = "uuid[]" if key == "intent_product_line_ids" else "uuid" if key == "primary_fde_id" else "text[]" if key == "customer_source" else "boolean" if key == "is_key" else "text"
        return f"${index}::{cast}"
    if updating:
        # Quarter-only history is a fallback, not a second forecast. A newly
        # confirmed exact date supersedes it; unchanged/missing dates retain it.
        # Original import evidence stays in import_meta and the import ledger.
        profile_update = "".join(f",{key}={profile_slot(index, key)}" for index, key in enumerate(profile_keys, 15))
        version = await connection.fetchval(
            """UPDATE crm.opportunity SET name=$2,probability=$3,status=$4,
            amount=$5,expected_close_date=$6,partner_name=$7,product_line=$8,stage_code=$9,
            expected_close_year=CASE
                WHEN $6::date IS NOT NULL AND expected_close_date IS DISTINCT FROM $6::date THEN NULL
                ELSE expected_close_year END,
            expected_close_quarter=CASE
                WHEN $6::date IS NOT NULL AND expected_close_date IS DISTINCT FROM $6::date THEN NULL
                ELSE expected_close_quarter END,
            closed_at=CASE WHEN $4='open' THEN NULL WHEN status<>$4 THEN clock_timestamp() ELSE closed_at END
             ,follow_up_plan=$10,sales_channel=$11,partner_id=$12::uuid,
             trade_relation=$13,upstream_opportunity_id=$14::uuid""" + profile_update + " WHERE id=$1::uuid RETURNING version_no",
            oid,
            *values,
            stage,
            after.get("follow_up_plan"),
            after['sales_channel'], after['partner_id'],
            after.get('trade_relation'), after.get('upstream_opportunity_id'),
            *profile_values,
        )
    else:
        async with native_creation(connection, oid):
            version = await connection.fetchval(
                """INSERT INTO crm.opportunity
                (id,name,probability,status,amount,expected_close_date,partner_name,product_line,stage_code,
                 workspace_id,customer_id,owner_user_ref_id,owner_team_id,created_by_user_ref_id,source_code,closed_at,follow_up_plan,sales_channel,partner_id,
                 trade_relation,upstream_opportunity_id""" + "".join(f",{key}" for key in profile_keys) + """ )
                VALUES($1::uuid,$2,$3,$4,$5,$6,$7,$8,$9,$10::uuid,$11::uuid,$12::uuid,$13::uuid,$14::uuid,
                 'manual',CASE WHEN $4='open' THEN NULL ELSE clock_timestamp() END,$15,$16,$17::uuid,$18,$19::uuid"""
                 + "".join("," + profile_slot(index, key) for index, key in enumerate(profile_keys, 20)) + ") RETURNING version_no",
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
                after.get('trade_relation'), after.get('upstream_opportunity_id'),
                *profile_values,
            )
    return version


async def write_forecasts(connection, actor, oid, updates):
    for row in updates or []:
        await connection.execute(
            """INSERT INTO crm.opportunity_forecast
            (workspace_id,opportunity_id,year,quarter,recognized_amount,collection_amount,updated_by_user_ref_id,collection_confidence)
            VALUES($1::uuid,$2::uuid,$3,$4,$5,$6,$7::uuid,$8)
            ON CONFLICT(opportunity_id,year,quarter) DO UPDATE SET
            recognized_amount=EXCLUDED.recognized_amount,collection_amount=EXCLUDED.collection_amount,
            collection_confidence=CASE WHEN $9 THEN EXCLUDED.collection_confidence ELSE crm.opportunity_forecast.collection_confidence END,
            updated_by_user_ref_id=EXCLUDED.updated_by_user_ref_id,updated_at=clock_timestamp()""",
            actor.workspace_id,
            oid,
            row["year"],
            row["quarter"],
            row.get("recognized_amount"),
            row.get("collection_amount"),
            actor.user_id,
            row.get("collection_confidence"),
            "collection_confidence" in row,
        )
