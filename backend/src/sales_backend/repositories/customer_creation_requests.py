from __future__ import annotations

from typing import Any, Literal

import asyncpg

from sales_backend.contracts.customer_request_history import CustomerRequestHistoryQuery
from sales_backend.db import json_value
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.concurrency import VersionConflict, require_version
from sales_backend.repositories.authorization_checks import require_permission
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.customer_identity import related_partner
from sales_backend.repositories.team_directory import selectable_teams


def _row(row) -> dict[str, Any]:
    if row is None:
        return None
    result = dict(row)
    result["payload"] = json_value(result.get("payload")) or {}
    result["reviewed_payload"] = json_value(result.get("reviewed_payload"))
    return result


def _page(row, *, limit: int, offset: int) -> dict[str, Any]:
    items = [_row(item) for item in json_value(row["items"]) or []]
    total = int(row["total"])
    more = offset + len(items) < total
    return {"items": items, "total": total, "offset": offset, "limit": limit,
            "has_more": more, "next_offset": offset + len(items) if more else None,
            "summary": json_value(row["summary"])}


class CustomerCreationRequestRepository:
    async def submit(self, connection, actor: ActorContext, data: dict[str, Any]) -> dict[str, Any]:
        await require_permission(connection, "customer.create_request")
        data = await related_partner(connection, data)
        if data.get("partner_profile_id"):
            profile = await connection.fetchrow(
                "SELECT id,status,security.partner_enterprise_reference(id) AS reference FROM crm.partner "
                "WHERE id=$1::uuid AND workspace_id=common.current_workspace_id()", str(data["partner_profile_id"]))
            if not profile or profile["status"] != "active":
                raise ValueError("请选择本公司有效自身伙伴档案")
            if (json_value(profile["reference"]) or {}).get("linked_customer_id"):
                raise FileExistsError("伙伴档案已关联企业，请使用已有档案")
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("请填写客户名称")
        # A self grant belongs to this application, not to a future customer.
        # Both the selector and submission validate current same-company teams;
        # approval separately requires the reviewer's scoped customer.create.
        teams = await selectable_teams(connection, actor, 'assignment', permission='customer.create_request')
        target_id = data.get('target_team_id')
        matches = [team for team in teams if team['id'] == str(target_id)] if target_id else [
            team for team in teams if team['name'] == data.get('target_team')]
        if len(matches) != 1:
            raise PermissionError("请选择当前可申请的有效业务团队")
        available = await connection.fetchval(
            "SELECT security.customer_name_is_available($1)", name,
        )
        if not available:
            raise FileExistsError("客户已存在，请使用客户认领或核对已有客户")
        row = await connection.fetchrow(
            "SELECT security.submit_customer_creation_request($1::jsonb) AS request", data,
        )
        row = json_value(row["request"] if row else None)
        return _row(row)

    async def mine(self, connection, actor: ActorContext, *, status: str | None = None,
                   q: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        await require_permission(connection, "customer.create_request")
        return await self._list(connection, actor, applicant=actor.user_id, status=status,
                                q=q, limit=limit, offset=offset)

    async def queue(self, connection, actor: ActorContext, *, status: str | None = "pending",
                    q: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        await require_permission(connection, "customer.create_review")
        return await self._list(connection, actor, applicant=None, status=status,
                                q=q, limit=limit, offset=offset)

    async def _list(self, connection, actor, *, applicant, status, q, limit, offset):
        result = await connection.fetchrow(
            r"""WITH records AS MATERIALIZED (
               SELECT r.id::text,r.status,r.applicant_user_ref_id::text,u.display_name AS applicant_name,
                      reviewer.display_name AS reviewer_name,r.payload,r.reviewed_payload,r.version_no,
                      r.requested_at,r.reviewed_at,r.reviewer_user_ref_id::text,
                      r.decision_reason,r.created_customer_id::text
                 FROM crm.customer_creation_request r
                 JOIN platform.user_ref u ON u.id=r.applicant_user_ref_id AND u.workspace_id=r.workspace_id
                 LEFT JOIN platform.user_ref reviewer ON reviewer.id=r.reviewer_user_ref_id
                  AND reviewer.workspace_id=r.workspace_id
                WHERE r.workspace_id=$1::uuid AND ($2::uuid IS NULL OR r.applicant_user_ref_id=$2)
                  AND ($4::text IS NULL OR u.display_name ILIKE '%'||$4||'%'
                    OR r.company_reference ILIKE '%'||$4||'%'
                    OR r.payload->>'name' ILIKE '%'||$4||'%'
                    OR r.reviewed_payload->>'name' ILIKE '%'||$4||'%')),
               filtered AS MATERIALIZED (SELECT * FROM records WHERE $3::text IS NULL OR status=$3),
               summary AS (SELECT count(*)::integer total,
                  count(*) FILTER(WHERE status='pending')::integer pending,
                  count(*) FILTER(WHERE status='approved')::integer approved,
                  count(*) FILTER(WHERE status='rejected')::integer rejected,
                  count(*) FILTER(WHERE status='cancelled')::integer cancelled FROM records)
               SELECT (SELECT count(*)::integer FROM filtered) AS total,
                      (SELECT to_jsonb(summary) FROM summary) AS summary,
                      (SELECT COALESCE(jsonb_agg(p),'[]'::jsonb) FROM
                       (SELECT * FROM filtered ORDER BY requested_at DESC,id DESC LIMIT $5 OFFSET $6) p) AS items""",
            actor.workspace_id, applicant, None if status == "all" else status,
            (q or "").strip() or None, limit, offset,
        )
        return _page(result, limit=limit, offset=offset)

    async def history(self, connection, actor: ActorContext, *, query: CustomerRequestHistoryQuery) -> dict[str, Any]:
        await require_permission(connection, "customer.read")
        try:
            result = await connection.fetchval(
                "SELECT security.customer_request_history($1,$2::uuid,$3,$4,$5,$6,$7,$8::uuid)",
                query.scope, str(query.team_id) if query.team_id else None,
                query.kind, query.status, query.q, query.limit, query.offset,
                str(query.member_id) if query.member_id else None,
            )
        except asyncpg.InsufficientPrivilegeError as exc:
            raise PermissionError("所选客户历史范围不在授权范围内") from exc
        return json_value(result)

    async def decide(self, connection, actor: ActorContext, request_id: str,
                     decision: Literal["approved", "rejected"], reason: str = "", *,
                     approved_payload: dict[str, Any] | None = None,
                     expected_version: int | None = None) -> dict[str, Any]:
        await require_permission(connection, "customer.create_review")
        reason = reason.strip()
        if decision == "rejected" and not reason:
            raise ValueError("驳回客户新建申请必须填写原因")
        locked = await connection.fetchval(
            "SELECT security.customer_creation_request_lock($1::uuid)", request_id,
        )
        row = json_value(locked)
        if not row:
            raise LookupError("客户新建申请不存在")
        if row.get("status") != "pending":
            raise VersionConflict("客户新建申请已处理，请刷新后再操作")
        require_version(row["version_no"], expected_version)
        if decision != "approved" and approved_payload is not None:
            raise ValueError("只有批准时可以修正建档内容")
        created = None
        if decision == "approved":
            # The customer write is deliberately inside this transaction. A
            # failed create rolls back the decision and leaves the request pending.
            from sales_backend.contracts.customer_creation import CustomerCreationRequestBody

            original = json_value(row.get("payload")) or {}
            candidate = dict(original if approved_payload is None else approved_payload)
            if approved_payload is not None:
                for field in ("business_identities", "partner_profile_id", "partner_id"):
                    if field not in approved_payload and field in original:
                        candidate[field] = original[field]
                # Older approval forms always submit a customer-type select.
                # They cannot change an identity they do not understand.
                if "business_identities" not in approved_payload and candidate.get("business_identities") == ["partner"]:
                    candidate["customer_type"] = None
            reviewed = CustomerCreationRequestBody.model_validate(candidate).model_dump(mode="json")
            created = await CustomerMutationRepository().create(connection, actor, data=reviewed)
            await connection.execute(
                "UPDATE crm.customer_creation_request SET reviewed_payload=$2::jsonb "
                "WHERE id=$1::uuid AND workspace_id=common.current_workspace_id() AND status='pending'",
                request_id, reviewed,
            )
        updated = await connection.fetchval(
            "SELECT security.complete_customer_creation_request($1::uuid,$2,$3,$4::uuid)",
            request_id, decision, reason, created["id"] if created else None,
        )
        result = _row(json_value(updated))
        if created is not None:
            result["customer"] = created
        return result
