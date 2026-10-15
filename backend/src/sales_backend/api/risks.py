from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.models import RiskResolve
from sales_backend.contracts.risk_browse import RiskBrowsePage, RiskBrowseQuery
from sales_backend.contracts.risk_decision import RiskDecision
from sales_backend.contracts.types import UUIDString
from sales_backend.db import Database
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.repositories.risks import RiskForbidden, RiskNotFound, RiskRepository
from sales_backend.services.capabilities import require_capability

router = APIRouter(prefix="/api/v1/risks", tags=["Risks"])


@router.get("", response_model=RiskBrowsePage, description=(
    "风险只读列表；不传 scope 保留当前授权范围。self 限风险负责人本人，person 需 member_id，"
    "team 需 team_id；UUID 条件按风险 owner_user_ref_id/owner_team_id 求交集，不扩大 RLS 权限。"
    "团队为风险记录的归属团队，不按负责人当前主团队改写。total 是当前 status 的全部筛选结果；"
    "summary 跨 status，沿用同一 UUID 和 opportunity_only 条件，与当前页无关。"
    "open 包含 accepted，只有 resolved 关闭；opportunity_only 限有关联商机的风险。"
    "未授权或不存在的筛选目标返回空结果。未关闭风险不按工作台周期截断。"
))
async def list_risks(
    risk_status: str | None = Query(default=None, alias="status", pattern="^(open|resolved)$"),
    page_size: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=2_147_483_647),
    scope: Literal["all", "self", "person", "team"] = "all",
    member_id: UUIDString | None = None,
    team_id: UUIDString | None = None,
    opportunity_only: bool = False,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> RiskBrowsePage:
    try:
        filters = RiskBrowseQuery(scope=scope, member_id=member_id, team_id=team_id,
                                  opportunity_only=opportunity_only)
    except ValidationError as exc:
        raise HTTPException(422, "风险范围参数不完整或相互冲突") from exc
    async with database.transaction(identity.actor, readonly=True) as connection:
        result = await RiskRepository().page(
            connection, actor=identity.actor, filters=filters,
            status=risk_status, limit=page_size, offset=offset,
        )
    return RiskBrowsePage(**result)


@router.get("/{risk_id}")
async def risk_detail(
    risk_id: UUIDString,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        item = await RiskRepository().detail(connection, risk_id=risk_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "RISK_NOT_FOUND")
    return item


@router.post("/{risk_id}/decision", description=(
    "人工记录已采纳 adopt 或暂不采纳 defer；不改变风险状态，不生成任务。必须提供说明、"
    "当前 version_no 和 request_id。相同身份/风险/request_id 的相同请求幂等返回，"
    "复用不同内容或过期版本返回409；risk.decide 与负责人本人/有效leader管理范围同时校验。"
))
async def decide_risk(
    risk_id: UUIDString,
    body: RiskDecision,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    try:
        async with database.transaction(identity.actor) as connection:
            await require_capability(connection, identity.actor, "risk.decide")
            return await RiskRepository().decide(connection, actor=identity.actor, risk_id=risk_id, body=body)
    except RiskNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except VersionConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{risk_id}/resolve", description=(
    "解除风险；说明去空白后至少5字。显式 version_no 必须与锁内当前版本一致，否则409。"
    "旧客户端省略或 null 版本时，仅可关闭当前版本为1的未关闭风险；已变化的开放风险409，"
    "须升级客户端后重新打开。无版本请求对已关闭风险只读返回，不修改原说明、版本、事件或通知。"
    "所有分支仍校验 risk.resolve 与有效leader管理范围；此兼容不提供任意版本的陈旧页面保护。"
))
async def resolve_risk(
    risk_id: UUIDString,
    body: RiskResolve,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    try:
        async with database.transaction(identity.actor) as connection:
            await require_capability(connection, identity.actor, "risk.resolve")
            return await RiskRepository().resolve(
                connection,
                actor=identity.actor,
                risk_id=risk_id,
                resolution_note=body.resolution_note,
                expected_version=body.version_no,
            )
    except RiskNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except RiskForbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except VersionConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
