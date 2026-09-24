from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.models import (
    AgentRunResponse,
    AssistantHomeResponse,
    ConversationCreate,
    ConversationResponse,
    MessageCreate,
    MessageResponse,
    RunAccepted,
)
from sales_backend.contracts.types import UUIDString
from sales_backend.db import Database
from sales_backend.domain.agent import RoleCode
from sales_backend.domain.capabilities import role_capabilities
from sales_backend.repositories.assistant import AssistantRepository, MessageReplayConflict
from sales_backend.repositories.company_rules import CompanyRulesRepository
from sales_backend.services.agent_access import require_agent_access
from sales_backend.services.capabilities import effective_capabilities
from sales_backend.services.idempotency import execute_mutation


def home_quick_actions(role: RoleCode, capabilities: dict[str, bool] | None = None) -> list[dict[str, str]]:
    """销售三入口、FDE两入口；动态录入开关由当前请求的有效能力控制。"""
    caps = capabilities if capabilities is not None else role_capabilities(role.value)
    actions = (
        ("customer.claim", "客户认领", "customer_claim"),
        ("visit.create", "记录客户拜访", "visit_entry"),
        ("task.create", "创建任务", "management_task"),
    )
    return [{"label": label, "code": code} for capability, label, code in actions if caps.get(capability, False)]

router = APIRouter(prefix="/api/v1", tags=["Assistant"])


@router.get("/assistant/home", response_model=AssistantHomeResponse)
async def assistant_home(
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> AssistantHomeResponse:
    async with database.transaction(identity.actor, readonly=True) as connection:
        data = await AssistantRepository().home(connection, identity.actor)
        archived_visits = await AssistantRepository().archived_visits(connection, identity.actor)
        display_policy = await CompanyRulesRepository().active(connection, "home_display")
        caps = await effective_capabilities(connection, identity.actor)
    role = identity.actor.role.value
    quick_actions = home_quick_actions(identity.actor.role, caps)
    return AssistantHomeResponse(
        greeting=f"你好，{identity.profile.display_name}",
        actor={
            "display_name": identity.profile.display_name,
            "role": role,
            "scope": identity.actor.data_scope.value,
        },
        overview=[
            {"value": data["today_visits"], "label": "今日拜访"},
            {"value": data["my_tasks"], "label": "待办事项"},
            {"value": data["open_risks"], "label": "风险提醒", "tone": "risk"},
        ],
        focus_cards=[
                {
                    "type": "task_execution_board",
                    "title": "任务执行看板",
                    "subtitle": "点击已完成或未完成，下钻查看对应任务",
                    "metrics": [
                        {"label": "已完成", "value": data["completed_tasks"], "action": "open_completed_tasks"},
                        {"label": "未完成", "value": data["my_tasks"], "action": "open_unfinished_tasks"},
                    ],
                    "rows": [],
                    "action": {"label": "查看任务总览", "code": "open_tasks"},
                }
            ],
        quick_actions=quick_actions,
        archived_visits=archived_visits,
        display_policy=display_policy,
        team_summary=data.get("team_summary"),
        data_as_of=datetime.now(UTC),
    )


@router.post("/conversations", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    body: ConversationCreate,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
) -> ConversationResponse:
    async with database.transaction(identity.actor) as connection:
        try:
            await require_agent_access(connection, identity.actor, body.mode, body.customer_id,
                                       opportunity_id=body.opportunity_id)
        except PermissionError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        async def create():
            row = await AssistantRepository().create_conversation(
                connection, identity.actor, mode=body.mode, customer_id=body.customer_id,
                opportunity_id=str(body.opportunity_id) if body.opportunity_id else None,
            )
            context = row["context"] or {}
            return ConversationResponse(
                id=row["id"], mode=context.get("mode", body.mode.value),
                customer_id=context.get("customer_id"), opportunity_id=context.get("opportunity_id"),
                status=row["status"], created_at=row["created_at"],
            ).model_dump(mode="json")

        result = await execute_mutation(
            connection, identity.actor, idempotency_key, "conversations.create", body.model_dump(), create,
        )
    return ConversationResponse.model_validate(result)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=RunAccepted,
    status_code=202,
    responses={409: {"description": "同一 client_message_id 对应的消息内容发生变化"}},
)
async def send_message(
    conversation_id: UUIDString,
    body: MessageCreate,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> RunAccepted:
    try:
        async with database.transaction(identity.actor) as connection:
            context = await AssistantRepository().conversation_context(connection, conversation_id)
            await require_agent_access(connection, identity.actor, context.get("mode", "chatbi"), context.get("customer_id"),
                                       opportunity_id=context.get("opportunity_id"))
            run_id = await AssistantRepository().enqueue_message(
                connection,
                identity.actor,
                conversation_id=conversation_id,
                text=body.text,
                client_message_id=body.client_message_id,
                input_source=body.input_source,
            )
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except MessageReplayConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "MESSAGE_REPLAY_CONFLICT") from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "CONVERSATION_NOT_FOUND") from exc
    return RunAccepted(run_id=run_id)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    conversation_id: UUIDString,
    limit: int = Query(50, ge=1, le=100),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> list[MessageResponse]:
    async with database.transaction(identity.actor, readonly=True) as connection:
        items = await AssistantRepository().list_messages(connection, conversation_id=conversation_id, limit=limit)
    return [MessageResponse(**item) for item in items]


@router.get("/agent/runs/{run_id}", response_model=AgentRunResponse)
async def get_run(
    run_id: UUIDString,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> AgentRunResponse:
    async with database.transaction(identity.actor, readonly=True) as connection:
        item = await AssistantRepository().get_run(connection, run_id=run_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "RUN_NOT_FOUND")
    return AgentRunResponse(**item)
