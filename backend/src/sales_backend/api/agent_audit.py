from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from sales_backend.api.dependencies import RequestIdentity, get_database
from sales_backend.api.exports import csv_response
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.api.operations_reports import window
from sales_backend.db import Database
from sales_backend.repositories.agent_audit import AgentAuditRepository

router = APIRouter(prefix="/api/v1/console", tags=["Agent business audit"])


@router.get("/ai/runs")
@router.get("/ai/runs/export")
async def runs(
    request: Request,
    bounds=Depends(window),
    capability: str | None = Query(None, max_length=80),
    provider: Literal["agent_platform", "senseaudio", "rules"] | None = None,
    outcome: Literal["running", "accepted", "failed", "cancelled", "reconciliation_required", "unknown"] | None = None,
    actor: UUID | None = None,
    test_only: bool | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    exporting = request.url.path.endswith("/export")
    async with database.transaction(identity.actor, readonly=True) as connection:
        result = await AgentAuditRepository().cases(
            connection,
            start=bounds[0],
            end=bounds[1],
            capability=capability,
            provider=provider,
            outcome=outcome,
            actor=actor,
            test_only=test_only,
            limit=10001 if exporting else limit,
            offset=0 if exporting else offset,
        )
    if exporting:
        return csv_response(
            [
                {
                    **r,
                    "provider": r.get("trace", {}).get("provider"),
                    "fallback_reason": r.get("trace", {}).get("fallback_reason"),
                }
                for r in result["items"]
            ],
            [
                ("case_id", "业务关联编号"),
                ("started_at", "发起时间"),
                ("capability", "业务能力"),
                ("actor_name", "发起人"),
                ("provider", "最终采用来源"),
                ("fallback_reason", "兜底原因"),
                ("inference_status", "推理状态"),
                ("business_status", "业务状态"),
                ("job_status", "后台任务状态"),
                ("test_injected", "受控故障测试"),
            ],
            "agent-runs",
        )
    return {
        **result,
        "note": (
            "推理通过不等于业务完成；历史缺失证据标为未知。受控测试仅按服务端故障标记识别，未标记不代表正式业务。"
        ),
    }
