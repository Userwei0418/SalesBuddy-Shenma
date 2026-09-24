"""Prepare immutable stage requests using the existing durable Agent run lifecycle."""

from datetime import date, timedelta

from sales_backend.contracts.visit_flow import canonical_fields, validate_quality_input
from sales_backend.db import json_value
from sales_backend.domain.agent import AgentMode
from sales_backend.domain.business_time import business_datetime
from sales_backend.repositories.assistant import AssistantRepository
from sales_backend.repositories.visits import validate_collaborators
from sales_backend.services.agent_access import require_agent_access
from sales_backend.services.capabilities import require_capability
from sales_backend.services.visit_access import require_visit_recording_scope, validate_visit_attendance, validate_visit_optional_actions


async def structure_source(connection, actor, source_run_id):
    return await connection.fetchrow(
        """SELECT m.text_content,r.business_context,r.created_at,
            (SELECT a.payload #>> '{fields,created_date}' FROM agent.artifact a
             WHERE a.run_id=r.id AND a.workspace_id=r.workspace_id
               AND a.artifact_type='visit_entry'
             ORDER BY a.created_at DESC LIMIT 1) AS structure_created_date
        FROM agent.run r JOIN agent.message m ON m.id=r.trigger_message_id
        WHERE r.id=$1::uuid AND r.workspace_id=$2::uuid AND r.identity_context->>'user_id'=$3
          AND r.intent_code='visit_entry' AND r.status='waiting_human'""",
        source_run_id, actor.workspace_id, actor.user_id,
    )


def structure_date_anchor(source):
    # The stored structure result was overwritten with server-owned fields at
    # validation. A pre-split legacy run can use its server creation timestamp.
    # Never accept the editable fields.created_date as the relative-time anchor.
    value = source.get("structure_created_date")
    if value:
        return date.fromisoformat(value).isoformat()
    return business_datetime(source["created_at"]).date().isoformat()


def relative_time_context(anchor, current_time):
    """Trusted calendar arithmetic only; the Agent still judges the user's text."""
    day = date.fromisoformat(anchor)
    monday = day - timedelta(days=day.weekday())
    current_day = business_datetime(current_time).date()
    return {
        "timezone": "Asia/Shanghai",
        "anchor_date": day.isoformat(),
        "current_date": current_day.isoformat(),
        "anchor_week_start": monday.isoformat(),
        "anchor_week_end": (monday + timedelta(days=6)).isoformat(),
        "next_week_start": (monday + timedelta(days=7)).isoformat(),
        "next_week_end": (monday + timedelta(days=13)).isoformat(),
        "next_week_expired": monday + timedelta(days=13) < current_day,
    }


async def prepare_visit_run(connection, actor, body, stage):
    await require_capability(connection, actor, "visit.create")
    await require_agent_access(connection, actor, "visit_entry", body.customer_id, opportunity_id=body.opportunity_id)
    await require_visit_recording_scope(connection, actor, body.customer_id, body.opportunity_id)
    await require_visit_recording_scope(connection, actor, body.customer_id, body.opportunity_id,
        permission="visit.quality_review" if stage == "quality" else "visit.structure")
    customer = json_value(await connection.fetchval("SELECT security.customer_reference($1::uuid)", body.customer_id))
    if not customer:
        raise PermissionError("客户不存在或不可用于录入")
    if body.opportunity_id:
        linked = await connection.fetchval(
            "SELECT id FROM crm.opportunity WHERE id=$1::uuid AND customer_id=$2::uuid AND deleted_at IS NULL",
            body.opportunity_id,
            body.customer_id,
        )
        if not linked:
            raise PermissionError("商机不存在、不可见或不属于当前客户")
    if body.source_import_id:
        owned = await connection.fetchval(
            "SELECT id FROM activity.visit_import WHERE id=$1::uuid "
            "AND created_by_user_ref_id=$2::uuid AND status='succeeded'",
            body.source_import_id,
            actor.user_id,
        )
        if not owned:
            raise ValueError("请使用本人已解析完成的材料")
    request = body.model_dump(mode="json")
    request["stage"] = stage
    if stage == "quality":
        source = await structure_source(connection, actor, body.source_run_id)
        if not source or source["business_context"].get("customer_id") != body.customer_id:
            raise ValueError("结构化原始记录不存在或客户已变化，请重新整理")
        original = source["business_context"].get("visit_request") or {}
        if original.get("stage") == "quality":
            raise ValueError("请使用结构化原始记录作为来源")
        if original.get("source_import_id") != body.source_import_id:
            raise ValueError("原始材料已变化，请重新整理")
        request["date_anchor"] = structure_date_anchor(source)
        request["fields"] = canonical_fields(request["fields"])
        # customer_type here means the record target, not CRM lifecycle stage.
        if request["fields"]["customer_type"] not in {"客户", "伙伴"}:
            raise ValueError("客户类型请选择客户或伙伴")
        if request["fields"]["customer_name"] != customer["name"]:
            raise ValueError("客户名称已变化，请重新核对")
        validate_quality_input(request["fields"])
        await validate_visit_optional_actions(connection, actor, body.customer_id, body.opportunity_id,
            mutation=request.get("opportunity_mutation"), collaborators=body.collaborator_ids,
            first_visit=request["fields"].get("is_first_visit", False))
        await validate_collaborators(connection, actor, body.collaborator_ids)
        request["fde_participant_ids"] = await validate_visit_attendance(
            connection, actor, body.fde_participant_ids, body.opportunity_id, request.get("opportunity_mutation")
        )
        text = source["text_content"]
    else:
        if not body.text.strip():
            raise ValueError("请输入拜访原文")
        await validate_visit_optional_actions(connection, actor, body.customer_id, body.opportunity_id,
            first_visit=request.get("is_first_visit", False))
        text = body.text
    repo = AssistantRepository()
    conversation = await repo.create_conversation(
        connection, actor, mode=AgentMode.VISIT_ENTRY, customer_id=body.customer_id, opportunity_id=body.opportunity_id
    )
    run_id = await repo.enqueue_message(
        connection,
        actor,
        conversation_id=conversation["id"],
        text=text,
        client_message_id=None,
        input_source="visit_flow",
        business_context={"visit_request": request},
    )
    return {"run_id": run_id}
