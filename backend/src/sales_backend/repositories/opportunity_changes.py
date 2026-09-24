"""Bounded event-time facts and recipient-safe change card persistence."""

import json

from sales_backend.domain.opportunity_change import CONTRACT


async def enqueue_change_review(connection, actor, *, event_id, opportunity_id, before, after,
                                old_forecasts, new_forecasts, changes, fallback, visit_context=None):
    records = {}
    queries = {
        "visits": "SELECT id::text,interaction_at,follow_up_record,next_action FROM activity.visit "
                  "WHERE opportunity_id=$1::uuid AND status IN ('confirmed','archived') AND deleted_at IS NULL "
                  "ORDER BY interaction_at DESC,id DESC LIMIT 21",
        "tasks": "SELECT id::text,title,description,status,due_at,completed_at FROM workflow.task "
                 "WHERE opportunity_id=$1::uuid AND deleted_at IS NULL ORDER BY updated_at DESC,id DESC LIMIT 21",
        "risks": "SELECT id::text,title,description,severity_code,status FROM insight.risk "
                 "WHERE opportunity_id=$1::uuid AND deleted_at IS NULL ORDER BY updated_at DESC,id DESC LIMIT 21",
    }
    coverage = {}
    refs = ["change"]
    for kind, query in queries.items():
        rows = await connection.fetch(query, opportunity_id)
        records[kind] = [dict(row) for row in rows[:20]]
        coverage[kind] = {"included": len(records[kind]), "has_more": len(rows) > 20}
        refs.extend(f"{kind}:{row['id']}" for row in records[kind])
    if visit_context:
        records["current_visit"] = {k: visit_context.get(k) for k in (
            "follow_up_record", "next_action", "interaction_at", "contact_name", "visit_goal"
        )}
        refs.append("current_visit")
    actuals = await connection.fetchrow(
        "SELECT sum(amount) FILTER(WHERE kind='recognized') AS recognized_amount,"
        "sum(amount) FILTER(WHERE kind='collection') AS collection_amount FROM crm.customer_actual "
        "WHERE opportunity_id=$1::uuid AND voided_at IS NULL", opportunity_id,
    )
    refs.append("actuals")
    # Whitelist business fields; never persist arbitrary request/auth/internal fields.
    keys = ("name", "status", "probability", "amount", "expected_close_date", "partner_name",
            "product_line", "sales_channel", "follow_up_plan")
    facts = {
        "contract_version": CONTRACT,
        "data_as_of": await connection.fetchval("SELECT clock_timestamp()"),
        "business_timezone": "Asia/Shanghai",
        "amount_unit": "CNY_yuan",
        "change": {"event_id": event_id, "opportunity_id": opportunity_id,
                   "version": before["version_no"] + 1,
                   "before": {k: before.get(k) for k in keys}, "after": {k: after.get(k) for k in keys},
                   "old_forecasts": old_forecasts, "new_forecasts": new_forecasts,
                   "changes": changes, "fallback": fallback},
        "records": records, "actuals": dict(actuals), "coverage": coverage, "evidence_refs": refs,
    }
    await connection.execute(
        "INSERT INTO ops.job(workspace_id,job_type,aggregate_type,aggregate_id,payload,priority,correlation_id) "
        "VALUES($1::uuid,'opportunity.change.review','business_change',$2::uuid,$3::jsonb,75,$2::uuid)",
        actor.workspace_id, event_id,
        json.loads(json.dumps({**actor.model_dump(mode="json"), "facts": facts}, ensure_ascii=False, default=str)),
    )


async def review_is_pending(connection, event_id):
    return await connection.fetchval(
        "SELECT EXISTS(SELECT 1 FROM crm.business_change b JOIN workflow.notification n "
        "ON n.payload->>'event_id'=b.id::text WHERE b.id=$1::uuid "
        "AND b.actor_user_ref_id=common.current_user_ref_id() AND b.opportunity_id IS NOT NULL "
        "AND security.authorization_opportunity('opportunity.update',b.opportunity_id) "
        "AND n.payload->'change_review'->>'status'='pending')", event_id,
    )


async def complete_change_review(connection, event_id, assessment):
    return await connection.fetchval(
        "SELECT workflow.complete_opportunity_change_review($1::uuid,$2::jsonb)", event_id, assessment,
    )
