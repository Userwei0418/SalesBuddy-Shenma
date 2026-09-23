"""Prepare immutable stage requests using the existing durable Agent run lifecycle."""

from sales_backend.contracts.visit_flow import canonical_fields, validate_quality_input
from sales_backend.db import json_value
from sales_backend.domain.agent import AgentMode
from sales_backend.repositories.assistant import AssistantRepository
from sales_backend.repositories.visits import validate_collaborators
from sales_backend.services.agent_access import require_agent_access
from sales_backend.services.capabilities import require_capability
from sales_backend.services.visit_access import require_visit_recording_scope, validate_visit_attendance


async def prepare_visit_run(connection, actor, body, stage):
    await require_capability(connection, actor, "visit.create")
    await require_agent_access(connection, actor, "visit_entry", body.customer_id, opportunity_id=body.opportunity_id)
    await require_visit_recording_scope(connection, actor, body.customer_id, body.opportunity_id)
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
        source = await connection.fetchrow(
            """SELECT m.text_content,r.business_context FROM agent.run r
            JOIN agent.message m ON m.id=r.trigger_message_id
            WHERE r.id=$1::uuid AND r.workspace_id=$2::uuid AND r.identity_context->>'user_id'=$3
              AND r.intent_code='visit_entry' AND r.status='waiting_human'""",
            body.source_run_id,
            actor.workspace_id,
            actor.user_id,
        )
        if not source or source["business_context"].get("customer_id") != body.customer_id:
            raise ValueError("结构化原始记录不存在或客户已变化，请重新整理")
        original = source["business_context"].get("visit_request") or {}
        if original.get("stage") == "quality":
            raise ValueError("请使用结构化原始记录作为来源")
        if original.get("source_import_id") != body.source_import_id:
            raise ValueError("原始材料已变化，请重新整理")
        request["fields"] = canonical_fields(request["fields"])
        # customer_type here means the record target, not CRM lifecycle stage.
        if request["fields"]["customer_type"] not in {"客户", "伙伴"}:
            raise ValueError("客户类型请选择客户或伙伴")
        if request["fields"]["customer_name"] != customer["name"]:
            raise ValueError("客户名称已变化，请重新核对")
        validate_quality_input(request["fields"])
        if actor.role.value in {"fde", "fde_lead"} and (body.opportunity_mutation or body.collaborator_ids):
            raise PermissionError("FDE仅能归档本人参与商机的跟进")
        await validate_collaborators(connection, actor, body.collaborator_ids)
        request["fde_participant_ids"] = await validate_visit_attendance(
            connection, actor, body.fde_participant_ids, body.opportunity_id, request.get("opportunity_mutation")
        )
        text = source["text_content"]
    else:
        if not body.text.strip():
            raise ValueError("请输入拜访原文")
        if actor.role.value in {"fde", "fde_lead"}:
            request["is_first_visit"] = False
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
