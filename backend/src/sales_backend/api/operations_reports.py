from sales_backend.api.exports import csv_response
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from sales_backend.api.dependencies import RequestIdentity, get_database
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.contracts.operations import Role
from sales_backend.db import Database
from sales_backend.domain.reporting import reporting_window
from sales_backend.repositories.operations_ai import OperationsAIRepository
from sales_backend.repositories.operations_logs import OperationsLogRepository
from sales_backend.services.operations import management_write

router = APIRouter(prefix="/api/v1/console", tags=["Operations reports and logs"])
ai = OperationsAIRepository()
logs = OperationsLogRepository()


def window(
    period: Literal["day", "week", "month", "custom"] = "month", start: date | None = None, end: date | None = None
):
    try:
        return reporting_window(period, start, end)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class UsageRule(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role_code: Role | None = None
    period: Literal["day", "week", "month"]
    calls_limit: int | None = Field(default=None, ge=1, le=1000000000)
    tokens_limit: int | None = Field(default=None, ge=1, le=1000000000000)
    enabled: bool = True
    version_no: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_threshold(self):
        if self.calls_limit is None and self.tokens_limit is None:
            raise ValueError("至少设置一个用量阈值")
        return self


@router.get("/ai/overview")
async def ai_overview(
    bounds=Depends(window),
    role: Role | None = None,
    user: UUID | None = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        result = await ai.report(connection, start=bounds[0], end=bounds[1], role=role, user=user)
    return {
        **result,
        "start": bounds[0],
        "end_exclusive": bounds[1],
        "usage_note": (
            "统计请求尝试，重试单独计数；明确标记的发送前测试拦截不计入实际请求。"
            "未返回 Token 时保留未知。请求完成不等于业务契约通过，业务结果见 Agent 运行审计。"
        ),
    }


@router.get("/ai/calls")
@router.get("/ai/calls/export")
async def ai_calls(
    request: Request,
    bounds=Depends(window),
    role: Role | None = None,
    user: UUID | None = None,
    status: Literal["running", "succeeded", "failed", "cancelled"] | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    exporting = request.url.path.endswith("/export")
    async with database.transaction(identity.actor, readonly=True) as connection:
        result = await ai.calls(
            connection,
            start=bounds[0],
            end=bounds[1],
            role=role,
            user=user,
            status=status,
            limit=10001 if exporting else limit,
            offset=0 if exporting else offset,
        )
    if exporting:
        return csv_response(
            result["items"],
            [
                ("started_at", "调用时间"),
                ("actor_name", "操作用户"),
                ("actor_role_code", "调用时角色"),
                ("operation_code", "业务操作"),
                ("model_id", "模型"),
                ("endpoint_code", "接口"),
                ("input_tokens", "输入Token"),
                ("output_tokens", "输出Token"),
                ("audio_seconds", "音频秒数"),
                ("latency_ms", "耗时毫秒"),
                ("status", "结果"),
                ("record_kind", "计量口径"),
                ("request_summary", "请求摘要"),
                ("request_id", "请求标识"),
            ],
            "ai-calls",
        )
    return result


@router.get("/ai/rules")
async def rules(
    identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database)
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return {"items": await ai.rules(connection), "alerts": await ai.alerts(connection)}


@router.post("/ai/rules", status_code=201)
async def create_rule(
    body: UsageRule,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        "ai-rule.create",
        body.model_dump(),
        lambda c: ai.save_rule(c, identity.actor, None, body.model_dump()),
    )


@router.put("/ai/rules/{rule_id}")
async def update_rule(
    rule_id: UUID,
    body: UsageRule,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"ai-rule.update:{rule_id}",
        body.model_dump(),
        lambda c: ai.save_rule(c, identity.actor, rule_id, body.model_dump()),
    )


@router.get("/audit")
@router.get("/audit/export")
async def audit(
    request: Request,
    bounds=Depends(window),
    actor: UUID | None = None,
    role: Role | None = None,
    module: str | None = Query(None, max_length=100),
    action: str | None = Query(None, max_length=100),
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    exporting = request.url.path.endswith("/export")
    async with database.transaction(identity.actor, readonly=True) as connection:
        result = await logs.audit(
            connection,
            start=bounds[0],
            end=bounds[1],
            actor=actor,
            role=role,
            module=module,
            action=action,
            q=q,
            limit=10001 if exporting else limit,
            offset=0 if exporting else offset,
        )
    if exporting:
        return csv_response(
            result["items"],
            [
                ("occurred_at", "操作时间"),
                ("actor_name", "操作人"),
                ("actor_role_code", "当时角色"),
                ("module_code", "模块"),
                ("action_code", "操作"),
                ("object_type", "对象类型"),
                ("object_label", "对象摘要"),
                ("object_id", "对象标识"),
                ("client_ip", "IP地址"),
                ("changed_fields", "变更字段"),
                ("before_snapshot", "变更前"),
                ("after_snapshot", "变更后"),
                ("result_code", "结果"),
                ("request_id", "请求标识"),
            ],
            "business-audit",
        )
    return result


@router.get("/system-events")
@router.get("/system-events/export")
async def system_events(
    request: Request,
    bounds=Depends(window),
    level: Literal["INFO", "WARN", "ERROR"] | None = None,
    module: str | None = Query(None, max_length=100),
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    exporting = request.url.path.endswith("/export")
    async with database.transaction(identity.actor, readonly=True) as connection:
        result = await logs.system_events(
            connection,
            start=bounds[0],
            end=bounds[1],
            level=level,
            module=module,
            q=q,
            limit=10001 if exporting else limit,
            offset=0 if exporting else offset,
        )
    if exporting:
        return csv_response(
            result["items"],
            [
                ("occurred_at", "时间"),
                ("level", "级别"),
                ("service_module", "服务模块"),
                ("event_type", "事件类型"),
                ("detail", "详情"),
                ("error_stack", "错误堆栈"),
                ("request_id", "请求标识"),
            ],
            "system-events",
        )
    return result


@router.get("/ai/roles/export")
async def export_role_usage(
    bounds=Depends(window),
    role: Role | None = None,
    user: UUID | None = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        result = await ai.report(connection, start=bounds[0], end=bounds[1], role=role, user=user)
    return csv_response(
        result["roles"],
        [
            ("role", "调用时角色"),
            ("calls", "实际请求次数"),
            ("users", "操作人数"),
            ("input_tokens", "已知输入Token"),
            ("output_tokens", "已知输出Token"),
            ("unknown_token_calls", "Token未完整上报次数"),
        ],
        "ai-role-usage",
    )
