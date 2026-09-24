"""Operations read models; claim writes use the database's atomic state machine."""

from sales_backend.db import json_value
from sales_backend.repositories.customer_risk import enqueue_customer_risk_review
from sales_backend.repositories.identity import IdentityRepository


async def _current_operator(connection, actor):
    """Resolve the authenticated DB context; an optional argument cannot impersonate it."""
    scope = await connection.fetchrow(
        "SELECT common.current_workspace_id()::text AS workspace_id,"
        "common.current_user_ref_id()::text AS user_id,common.current_role_code() AS role"
    )
    if not scope or not scope["workspace_id"] or not scope["user_id"] or not scope["role"]:
        raise PermissionError("需要当前有效登录身份")
    if actor is not None and (actor.workspace_id, actor.user_id, actor.role.value) != (
        scope["workspace_id"], scope["user_id"], scope["role"],
    ):
        raise PermissionError("审批操作者与当前认证身份不一致")
    current = await IdentityRepository().find_actor_by_id(
        connection, workspace_id=scope["workspace_id"], user_id=scope["user_id"], role=scope["role"],
    )
    if current is None:
        raise PermissionError("当前审批账号或权限已失效")
    return current.context


class OperationsCustomerRepository:
    async def profile(self, connection, customer_id):
        row = await connection.fetchrow(
            """SELECT c.id::text,c.name,c.customer_code,c.external_customer_id,c.company_reference,
              c.industry_code,c.customer_type_code,c.level_code,c.source_code,c.primary_partner_name,
              c.operation_type,c.cooperation_years,c.main_business,c.customer_budget,c.demand_summary,c.next_action,
              c.lifecycle_status,c.data_source,c.data_kind,c.created_at,c.updated_at,c.company_verified_at,
              c.version_no,t.name AS team_name,u.display_name AS creator_name,
              p.name AS contact_name,p.title AS contact_title,p.relationship_role_code AS contact_role,
              p.phone AS contact_phone,p.email AS contact_email
              FROM crm.customer c LEFT JOIN platform.team t ON t.id=c.owner_team_id
              LEFT JOIN platform.user_ref u ON u.id=c.created_by_user_ref_id
              LEFT JOIN LATERAL (SELECT name,title,relationship_role_code,phone,email FROM crm.contact
                WHERE customer_id=c.id AND deleted_at IS NULL ORDER BY is_primary DESC,created_at,id LIMIT 1) p ON true
              WHERE c.id=$1::uuid AND c.deleted_at IS NULL""",
            customer_id,
        )
        return dict(row) if row else None

    async def customer_history(self, connection, customer_id, *, kind, limit=20, offset=0):
        if kind == "claims":
            projection = """SELECT r.id::text,r.customer_id::text,r.status,r.requested_at,r.reviewed_at,
              r.decision_reason,a.display_name AS applicant_name,a.account_code,reviewer.display_name AS reviewer_name
              FROM crm.customer_claim_request r JOIN platform.user_ref a ON a.id=r.applicant_user_ref_id
              LEFT JOIN platform.user_ref reviewer ON reviewer.id=r.reviewer_user_ref_id
              WHERE r.customer_id=$1::uuid"""
            order = "requested_at DESC,id"
        elif kind == "ownership":
            projection = """SELECT e.id::text,e.event_type,e.reason,e.occurred_at,a.display_name AS operator,
              old.display_name AS previous_owner,new.display_name AS owner FROM crm.customer_ownership_event e
              JOIN platform.user_ref a ON a.id=e.actor_user_ref_id
              LEFT JOIN platform.user_ref old ON old.id=e.previous_owner_user_ref_id
              LEFT JOIN platform.user_ref new ON new.id=e.owner_user_ref_id WHERE e.customer_id=$1::uuid"""
            order = "occurred_at DESC,id"
        else:
            raise ValueError("Unknown customer history")
        result = await connection.fetchrow(
            "WITH records AS MATERIALIZED (" + projection + ") SELECT "  # noqa: S608
            "(SELECT count(*)::integer FROM records) AS total,"
            "(SELECT COALESCE(jsonb_agg(p),'[]'::jsonb) FROM (SELECT * FROM records ORDER BY "
            + order + " LIMIT $2 OFFSET $3) p) AS items",  # noqa: S608 -- fixed internal projections/order
            customer_id, limit, offset,
        )
        more = offset + len(result["items"]) < result["total"]
        return {**dict(result), "has_more": more, "next_offset": offset + limit if more else None}

    async def list(self, connection, *, q=None, industry=None, level=None, state=None, owner=None, limit=50, offset=0):
        rows = await connection.fetch(
            """WITH page AS MATERIALIZED (
            SELECT c.id,c.name,c.industry_code,c.customer_type_code,c.source_code,c.level_code,
            c.lifecycle_status,c.owner_team_id,c.primary_partner_name,
            c.created_at,c.version_no,c.company_reference,c.company_verified_at,
            o.state AS ownership_state,o.version_no AS ownership_version,o.owner_user_ref_id,
            count(*) OVER()::integer AS total_count
            FROM crm.customer c JOIN crm.customer_ownership o ON o.customer_id=c.id
            WHERE c.deleted_at IS NULL AND ($1::text IS NULL OR c.name ILIKE '%'||$1||'%' OR c.company_reference
            ILIKE '%'||$1||'%')
            AND ($2::text IS NULL OR c.industry_code=$2) AND ($3::text IS NULL OR c.level_code=$3)
            AND ($4::text IS NULL OR o.state=$4) AND ($5::uuid IS NULL OR o.owner_user_ref_id=$5)
            ORDER BY c.created_at DESC,c.id LIMIT $6 OFFSET $7)
            SELECT c.id::text,c.name,c.industry_code,c.customer_type_code,c.source_code,c.level_code,
            c.lifecycle_status,c.owner_team_id::text,t.name AS team_name,c.primary_partner_name,
            c.created_at,c.version_no,c.company_reference,c.company_verified_at,
            c.ownership_state,c.ownership_version,c.owner_user_ref_id::text,u.display_name AS owner_name,
            p.name AS contact_name,p.title AS contact_title,p.relationship_role_code AS contact_role,
            p.phone AS contact_phone,p.email AS contact_email,c.total_count
            FROM page c LEFT JOIN platform.user_ref u ON u.id=c.owner_user_ref_id
            LEFT JOIN platform.team t ON t.id=c.owner_team_id
            LEFT JOIN LATERAL(SELECT * FROM crm.contact WHERE customer_id=c.id AND deleted_at IS NULL
            ORDER BY is_primary DESC,created_at LIMIT 1) p ON true
            ORDER BY c.created_at DESC,c.id""",
            q,
            industry,
            level,
            state,
            owner,
            limit,
            offset,
        )
        return {
            "items": [dict(row) for row in rows],
            "total": rows[0]["total_count"] if rows else 0,
            "offset": offset,
            "limit": limit,
        }

    async def ownership(self, connection, customer_id):
        row = await connection.fetchrow(
            """SELECT o.customer_id::text,o.owner_user_ref_id::text,o.state,o.version_no,u.display_name AS owner_name,
            (SELECT jsonb_agg(jsonb_build_object('id',m.user_ref_id::text,'name',mu.display_name) ORDER BY
            mu.display_name)
             FROM crm.customer_sales_member m JOIN platform.user_ref mu ON mu.id=m.user_ref_id WHERE
            m.customer_id=o.customer_id) AS historical_members
            FROM crm.customer_ownership o LEFT JOIN platform.user_ref u ON u.id=o.owner_user_ref_id WHERE
            o.customer_id=$1::uuid""",
            customer_id,
        )
        return dict(row) if row else None

    async def claims(self, connection, *, status=None, customer_id=None, q=None, limit=50, offset=0):
        rows = await connection.fetch(
            """SELECT r.id::text,r.customer_id::text,c.name AS customer_name,r.applicant_user_ref_id::text,
            a.display_name AS applicant_name,a.account_code,r.status,r.requested_at,r.reviewed_at,r.decision_reason,
            r.source,reviewer.display_name AS reviewer_name,count(*) OVER()::integer AS total_count
            FROM crm.customer_claim_request r JOIN crm.customer c ON c.id=r.customer_id
            JOIN platform.user_ref a ON a.id=r.applicant_user_ref_id
            LEFT JOIN platform.user_ref reviewer ON reviewer.id=r.reviewer_user_ref_id
            WHERE ($1::text IS NULL OR r.status=$1) AND ($2::uuid IS NULL OR r.customer_id=$2)
            AND ($3::text IS NULL OR c.name ILIKE '%'||$3||'%' OR a.display_name ILIKE '%'||$3||'%')
            ORDER BY r.requested_at DESC,r.id LIMIT $4 OFFSET $5""",
            status,
            customer_id,
            q,
            limit,
            offset,
        )
        return {"items": [dict(row) for row in rows], "total": rows[0]["total_count"] if rows else 0}

    async def events(self, connection, customer_id):
        return [
            dict(r)
            for r in await connection.fetch(
                """SELECT e.id::text,e.event_type,e.reason,e.occurred_at,a.display_name AS operator,
            old.display_name AS previous_owner,new.display_name AS owner FROM crm.customer_ownership_event e
            JOIN platform.user_ref a ON a.id=e.actor_user_ref_id LEFT JOIN platform.user_ref old ON
            old.id=e.previous_owner_user_ref_id
            LEFT JOIN platform.user_ref new ON new.id=e.owner_user_ref_id WHERE e.customer_id=$1::uuid ORDER BY
            e.occurred_at DESC LIMIT 200""",
                customer_id,
            )
        ]

    async def review(self, connection, request_id, decision, reason, *, actor=None):
        operator = await _current_operator(connection, actor)
        result = json_value(
            await connection.fetchval(
                "SELECT security.review_customer_claim($1::uuid,$2,$3)", request_id, decision, reason
            )
        )
        if result["status"] == "approved":
            await enqueue_customer_risk_review(
                connection, operator, customer_id=result["customer_id"],
                trigger_type="customer.claimed", trigger_id=result["request_id"],
            )
        return result

    async def release(self, connection, customer_id, version, reason):
        return json_value(
            await connection.fetchval("SELECT security.release_customer($1::uuid,$2,$3)", customer_id, version, reason)
        )

    async def resolve_legacy(self, connection, customer_id, version, owner, reason, *, actor=None):
        operator = await _current_operator(connection, actor)
        result = json_value(
            await connection.fetchval(
                "SELECT security.resolve_legacy_customer_owner($1::uuid,$2,$3::uuid,$4)",
                customer_id,
                version,
                owner,
                reason,
            )
        )
        if result["status"] == "approved":
            await enqueue_customer_risk_review(
                connection, operator, customer_id=result["customer_id"],
                trigger_type="owner.resolved", trigger_id=result["request_id"],
            )
        return result

    async def summary(self, connection):
        return dict(
            await connection.fetchrow(
                """SELECT count(*)::integer AS customers,
            count(*) FILTER(WHERE o.state='unclaimed')::integer AS unclaimed,
            count(*) FILTER(WHERE o.state='legacy_review')::integer AS legacy_review,
            (SELECT count(*)::integer FROM crm.customer_claim_request WHERE status='pending') AS pending_claims
            FROM crm.customer c JOIN crm.customer_ownership o ON o.customer_id=c.id WHERE c.deleted_at IS NULL"""
            )
        )
