"""Persist human-confirmed field differences and recipient-scoped cards atomically."""

import json
from uuid import uuid4


async def record_change(
    connection,
    actor,
    customer,
    *,
    opportunity_id=None,
    version=1,
    changes,
    tone="blue",
    title="客户信息已更新",
    name="",
    assess_change=False,
):
    event_id = str(uuid4())
    changes = json.loads(json.dumps(changes, ensure_ascii=False, default=str))
    await connection.execute(
        """INSERT INTO crm.business_change
        (id,workspace_id,customer_id,opportunity_id,actor_user_ref_id,version_no,changes)
        VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$6,$7::jsonb)""",
        event_id,
        actor.workspace_id,
        str(customer["id"]),
        opportunity_id,
        actor.user_id,
        version,
        changes,
    )
    actor_name = await connection.fetchval(
        "SELECT display_name FROM platform.user_ref WHERE id=$1::uuid", actor.user_id
    )
    payload = {
        "customer_id": str(customer["id"]),
        "opportunity_id": opportunity_id,
        "customer_name": customer["name"],
        "name": name,
        "changes": changes,
        "tone": tone,
        "actor_name": actor_name,
        "event_id": event_id,
    }
    if assess_change:
        payload["change_review"] = {"status": "pending"}
    if opportunity_id:
        owners = await connection.fetch(
            "SELECT owner_user_ref_id::text AS user_id FROM crm.opportunity WHERE id=$1::uuid", opportunity_id
        )
    else:
        owners = await connection.fetch(
            "SELECT user_ref_id::text AS user_id FROM crm.customer_sales_member WHERE customer_id=$1::uuid",
            str(customer["id"]),
        )
    for recipient in {actor.user_id, *(r["user_id"] for r in owners if r["user_id"])}:
        await connection.execute(
            """INSERT INTO workflow.notification
            (workspace_id,recipient_user_ref_id,channel_code,template_code,title,body,object_type,
             object_id,status,dedupe_key,payload)
            VALUES($1::uuid,$2::uuid,'in_app','business_changed',$3,$4,$5,$6::uuid,'pending',$7,$8::jsonb)
            ON CONFLICT DO NOTHING""",
            actor.workspace_id,
            recipient,
            title,
            f"{customer['name']} · {name or '客户资料'}",
            "opportunity" if opportunity_id else "customer",
            opportunity_id or str(customer["id"]),
            f"business_changed:{event_id}:{recipient}",
            payload,
        )
    if opportunity_id:
        participants = await connection.fetch(
            "SELECT user_ref_id::text FROM crm.opportunity_participant WHERE opportunity_id=$1::uuid "
            "AND participant_role='fde' AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to "
            "AND security.fde_user_is_active(user_ref_id)", opportunity_id,
        )
        for person in participants:
            await connection.fetchval(
                "SELECT workflow.enqueue_fde_collaboration_notification("
                "$1::uuid,$2::uuid,'business_changed',$3::uuid,$4::jsonb)",
                opportunity_id, person["user_ref_id"], event_id, {**payload, "title": title},
            )
    return event_id
