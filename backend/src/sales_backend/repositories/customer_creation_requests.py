from __future__ import annotations

from typing import Any, Literal

from sales_backend.db import json_value
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.repositories.authorization_checks import require_permission
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.team_directory import selectable_teams


def _row(row) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["payload"] = json_value(result.get("payload")) or {}
    return result


def _page(row, *, limit: int, offset: int) -> dict[str, Any]:
    items = [_row(item) for item in json_value(row["items"]) or []]
    total = int(row["total"])
    has_more = offset + len(items) < total
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "next_offset": offset + len(items) if has_more else None,
    }


class CustomerCreationRequestRepository:
    async def submit(self, connection, actor: ActorContext, data: dict[str, Any]) -> dict[str, Any]:
        await require_permission(connection, "customer.create_request")
        name = str(data.get("name") or "").strip()
        company_reference = str(data.get("company_reference") or "").strip()
        if not name:
            raise ValueError("请填写客户名称")
        if not company_reference:
            raise ValueError("请填写已核实的公司客户编号或审批单号")
        teams = await selectable_teams(connection, actor, 'creation_request')
        team_id = data.get('target_team_id')
        matches = [team for team in teams if team['id'] == str(team_id)] if team_id else [
            team for team in teams if team['name'] == data.get('target_team')]
        if len(matches) != 1:
            raise PermissionError("请选择当前授权范围内的有效业务团队")
        data = {**data, 'name': name, 'company_reference': company_reference,
                'target_team_id': matches[0]['id'], 'target_team': matches[0]['name']}
        available = await connection.fetchval("SELECT security.customer_name_is_available($1)", name)
        if not available:
            raise FileExistsError("客户已存在，请使用客户认领或核对已有客户")
        row = await connection.fetchrow(
            "SELECT security.submit_customer_creation_request($1::jsonb) AS request", data
        )
        return _row(json_value(row["request"] if row else None))

    async def withdraw(self, connection, actor: ActorContext, request_id: str) -> dict[str, Any]:
        await require_permission(connection, "customer.create_request")
        return _row(json_value(await connection.fetchval(
            "SELECT security.withdraw_customer_creation_request($1::uuid)", request_id
        )))

    async def mine(
        self, connection, actor: ActorContext, *, status: str | None = None,
        q: str | None = None, limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        await require_permission(connection, "customer.create_request")
        result = await connection.fetchrow(
            """WITH records AS MATERIALIZED (
               SELECT r.id::text,r.status,r.applicant_user_ref_id::text,a.display_name AS applicant_name,
                      reviewer.display_name AS reviewer_name,r.payload,r.requested_at,
                      r.reviewed_at,r.reviewer_user_ref_id::text,r.decision_reason,r.created_customer_id::text
                 FROM crm.customer_creation_request r
                 JOIN platform.user_ref a ON a.id=r.applicant_user_ref_id AND a.workspace_id=r.workspace_id
                 LEFT JOIN platform.user_ref reviewer ON reviewer.id=r.reviewer_user_ref_id
                  AND reviewer.workspace_id=r.workspace_id
                WHERE r.workspace_id=common.current_workspace_id()
                  AND r.applicant_user_ref_id=common.current_user_ref_id()
                  AND ($1::text IS NULL OR r.status=$1)
                  AND ($2::text IS NULL OR r.company_reference ILIKE '%'||$2||'%' OR r.payload->>'name' ILIKE '%'||$2||'%'))
               SELECT (SELECT count(*)::integer FROM records) AS total,
                      (SELECT COALESCE(jsonb_agg(p),'[]'::jsonb) FROM
                       (SELECT * FROM records ORDER BY requested_at DESC,id DESC LIMIT $3 OFFSET $4) p) AS items""",
            None if status == "all" else status,
            (q or "").strip() or None,
            limit,
            offset,
        )
        return _page(result, limit=limit, offset=offset)

    async def queue(
        self, connection, actor: ActorContext, *, status: str | None = "pending",
        q: str | None = None, limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        await require_permission(connection, "customer.create_review")
        result = await connection.fetchrow(
            """WITH records AS MATERIALIZED (
               SELECT r.id::text,r.status,r.applicant_user_ref_id::text,u.display_name AS applicant_name,
                      reviewer.display_name AS reviewer_name,r.payload,r.requested_at,r.reviewed_at,
                      r.reviewer_user_ref_id::text,r.decision_reason,r.created_customer_id::text
                 FROM crm.customer_creation_request r
                 JOIN platform.user_ref u ON u.id=r.applicant_user_ref_id AND u.workspace_id=r.workspace_id
                 LEFT JOIN platform.user_ref reviewer ON reviewer.id=r.reviewer_user_ref_id
                  AND reviewer.workspace_id=r.workspace_id
                WHERE r.workspace_id=common.current_workspace_id()
                  AND ($1::text IS NULL OR r.status=$1)
                  AND ($2::text IS NULL OR u.display_name ILIKE '%'||$2||'%'
                       OR r.company_reference ILIKE '%'||$2||'%'
                       OR r.normalized_name ILIKE '%'||lower(regexp_replace($2,'\\s+','','g'))||'%'))
               SELECT (SELECT count(*)::integer FROM records) AS total,
                      (SELECT COALESCE(jsonb_agg(p),'[]'::jsonb) FROM
                       (SELECT * FROM records ORDER BY requested_at DESC,id DESC LIMIT $3 OFFSET $4) p) AS items""",
            None if status == "all" else status,
            (q or "").strip() or None,
            limit,
            offset,
        )
        return _page(result, limit=limit, offset=offset)

    async def decide(
        self, connection, actor: ActorContext, request_id: str,
        decision: Literal["approved", "rejected"], reason: str = "",
    ) -> dict[str, Any]:
        await require_permission(connection, "customer.create_review")
        reason = reason.strip()
        if decision == "rejected" and not reason:
            raise ValueError("驳回客户新建申请必须填写原因")
        locked = await connection.fetchval(
            "SELECT security.customer_creation_request_lock($1::uuid)", request_id
        )
        row = json_value(locked)
        if not row:
            raise LookupError("客户新建申请不存在")
        if row.get("status") != "pending":
            raise VersionConflict("客户新建申请已处理，请刷新后再操作")
        created = None
        if decision == "approved":
            created = await CustomerMutationRepository().create(
                connection, actor, data=json_value(row.get("payload")) or {}
            )
        updated = await connection.fetchval(
            "SELECT security.complete_customer_creation_request($1::uuid,$2,$3,$4::uuid)",
            request_id,
            decision,
            reason,
            created["id"] if created else None,
        )
        result = _row(json_value(updated)) or {}
        if created is not None:
            result["customer"] = created
        return result
