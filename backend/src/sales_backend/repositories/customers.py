from __future__ import annotations

from typing import Any

import asyncpg

from sales_backend.repositories.attribute_overlay import (
    overlay_customer_attributes,
)
from sales_backend.repositories.customer_risk import current_clear_assessment


class CustomerRepository:
    async def reference(self, connection, customer_id):
        return await connection.fetchval("SELECT security.customer_reference($1::uuid)", str(customer_id))

    async def exists(self, connection, customer_id):
        return await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM crm.customer WHERE id=$1::uuid AND deleted_at IS NULL)", str(customer_id)
        )

    async def list(
        self,
        connection: asyncpg.Connection,
        *,
        query: str | None,
        level: str | None,
        unassigned: bool | None,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        rows = await connection.fetch(
            """
            WITH scope AS MATERIALIZED (
              SELECT * FROM security.customer_portfolio_scope()
            ), latest_visits AS MATERIALIZED (
              SELECT v.customer_id,max(v.interaction_at) AS interaction_at,
                count(*) FILTER (WHERE timezone('Asia/Shanghai',v.interaction_at) >=
                  date_trunc('week',timezone('Asia/Shanghai',clock_timestamp())))::integer AS weekly_follow_up_count
              FROM activity.visit v
              WHERE v.customer_id IS NOT NULL AND v.deleted_at IS NULL
                AND v.status IN ('confirmed','archived')
              GROUP BY v.customer_id
            ), visited AS MATERIALIZED (
              SELECT c.id,c.name,scope.claimant_id,latest.interaction_at,latest.weekly_follow_up_count
              FROM scope JOIN latest_visits latest ON latest.customer_id=scope.id
              JOIN LATERAL (
                SELECT c.id,c.name,c.level_code FROM crm.customer c
                WHERE c.id=scope.id AND c.deleted_at IS NULL LIMIT 1
              ) c ON true
              LEFT JOIN crm.customer_ownership ownership ON ownership.customer_id=c.id
              LEFT JOIN platform.user_ref u ON u.id=scope.claimant_id
              WHERE latest.interaction_at IS NOT NULL
                AND ($1::text IS NULL OR c.name ILIKE '%'||$1||'%' OR u.display_name ILIKE '%'||$1||'%')
                AND ($2::text IS NULL OR c.level_code=$2)
                AND ($3::boolean IS NULL OR ($3 AND ownership.state='unclaimed')
                     OR (NOT $3 AND ownership.state='claimed'))
              ORDER BY latest.interaction_at DESC NULLS LAST,c.name LIMIT $4
            ), without_date AS MATERIALIZED (
              SELECT c.id,c.name,scope.claimant_id,NULL::timestamptz AS interaction_at,
                COALESCE(latest.weekly_follow_up_count,0)::integer AS weekly_follow_up_count
              FROM scope
              JOIN LATERAL (
                SELECT c.id,c.name,c.level_code FROM crm.customer c
                WHERE c.id=scope.id AND c.deleted_at IS NULL LIMIT 1
              ) c ON true
              LEFT JOIN latest_visits latest ON latest.customer_id=c.id
              LEFT JOIN crm.customer_ownership ownership ON ownership.customer_id=c.id
              LEFT JOIN platform.user_ref u ON u.id=scope.claimant_id
              WHERE latest.interaction_at IS NULL
                AND ($1::text IS NULL OR c.name ILIKE '%'||$1||'%' OR u.display_name ILIKE '%'||$1||'%')
                AND ($2::text IS NULL OR c.level_code=$2)
                AND ($3::boolean IS NULL OR ($3 AND ownership.state='unclaimed')
                     OR (NOT $3 AND ownership.state='claimed'))
              ORDER BY c.name
              LIMIT (CASE WHEN $4::integer IS NULL THEN NULL
                     ELSE GREATEST($4-(SELECT count(*) FROM visited),0) END)
            ), page AS MATERIALIZED (
              SELECT * FROM visited UNION ALL SELECT * FROM without_date
            )
            SELECT c.id::text, c.name, security.authorization_customer('customer.update',c.id) AS can_edit, c.industry_code, c.customer_type_code, c.level_code,
                   c.lifecycle_status, c.source_code, c.data_kind, c.attributes,
                   c.import_meta, c.owner_user_ref_id::text,
                   u.display_name AS owner_name, CASE WHEN u.id IS NULL THEN '[]'::jsonb
                     ELSE jsonb_build_array(jsonb_build_object('id',u.id::text,'name',u.display_name))
                     END AS sales_members, c.owner_team_id::text,
                   t.name AS team_name, c.primary_partner_name,
                   c.next_action, c.operation_type, c.cooperation_years, c.main_business, c.customer_budget,
                   (SELECT s.input_snapshot->'company_policy' FROM insight.quadrant_score s
                     WHERE s.customer_id=c.id AND s.valid_to='infinity'
                     AND s.subject_user_ref_id=common.current_user_ref_id()) AS quadrant_policy,
                   q.potential_score, q.relationship_score, q.quadrant_code,
                   latest.interaction_at AS latest_visit_at,
                   COALESCE(latest.weekly_follow_up_count, 0) AS weekly_follow_up_count,
                   open_risk.title AS risk_title,
                   open_risk.severity_code AS risk_severity,
                   opportunity.name AS opportunity_name,
                   opportunity.amount AS opportunity_amount,
                   opportunity.stage_code AS opportunity_stage,
                   ARRAY(SELECT o.expected_close_date FROM crm.opportunity o WHERE o.customer_id=c.id
                     AND o.deleted_at IS NULL AND o.status='open') AS plan_close_dates
            FROM page latest
            JOIN LATERAL (
              SELECT c.* FROM crm.customer c WHERE c.id=latest.id AND c.deleted_at IS NULL LIMIT 1
            ) c ON true
            LEFT JOIN platform.user_ref u ON u.id=latest.claimant_id
            LEFT JOIN platform.team t ON t.id=c.owner_team_id
            LEFT JOIN LATERAL (
              SELECT q.potential_score,q.relationship_score,q.quadrant_code
              FROM crm.v_customer_current_quadrant q WHERE q.id=c.id LIMIT 1
            ) q ON true
            LEFT JOIN LATERAL (
              SELECT r.title, r.severity_code FROM insight.risk r
              WHERE r.customer_id = c.id AND r.deleted_at IS NULL
                AND r.status IN ('new','pending','in_progress','escalated')
              ORDER BY CASE r.severity_code WHEN 'critical' THEN 1 WHEN 'high' THEN 2 ELSE 3 END,
                       r.opened_at DESC LIMIT 1
            ) open_risk ON true
            LEFT JOIN LATERAL (
              SELECT o.name, o.stage_code,
                     (SELECT COALESCE(sum(o2.amount), 0)
                        FROM crm.opportunity o2
                       WHERE o2.customer_id = c.id AND o2.deleted_at IS NULL
                         AND o2.status = 'open') AS amount
                FROM crm.opportunity o
               WHERE o.customer_id = c.id AND o.deleted_at IS NULL AND o.status = 'open'
               ORDER BY o.updated_at DESC LIMIT 1
            ) opportunity ON true
            ORDER BY latest.interaction_at DESC NULLS LAST, c.name
            LIMIT $4
            """,
            query,
            level,
            unassigned,
            limit,
        )
        return [overlay_customer_attributes(dict(row)) for row in rows]

    async def base(self, connection: asyncpg.Connection, *, customer_id: str) -> dict[str, Any] | None:
        customer = await connection.fetchrow(
            """
            SELECT c.id::text, c.name, security.authorization_customer('customer.update',c.id) AS can_edit, c.industry_code, c.customer_type_code, c.level_code,
                   c.lifecycle_status, c.source_code, c.data_kind, c.primary_partner_name,
                   c.demand_summary, c.attributes, c.import_meta, c.version_no,
                   c.next_action, c.operation_type, c.cooperation_years, c.main_business, c.customer_budget,
                   u.display_name AS owner_name, CASE WHEN u.id IS NULL THEN '[]'::jsonb
                     ELSE jsonb_build_array(jsonb_build_object('id',u.id::text,'name',u.display_name))
                     END AS sales_members, t.name AS team_name,
                   (SELECT s.input_snapshot->'company_policy' FROM insight.quadrant_score s
                     WHERE s.customer_id=c.id AND s.valid_to='infinity'
                     AND s.subject_user_ref_id=common.current_user_ref_id()) AS quadrant_policy,
                   q.potential_score, q.relationship_score, q.quadrant_code
            FROM crm.customer c
            LEFT JOIN crm.customer_ownership ownership ON ownership.customer_id=c.id
            LEFT JOIN platform.user_ref u ON u.id = security.profile_customer_owner(c.id)
            LEFT JOIN platform.team t ON t.id = c.owner_team_id
            LEFT JOIN crm.v_customer_current_quadrant q ON q.id = c.id
            WHERE c.id = $1::uuid AND c.deleted_at IS NULL
            """,
            customer_id,
        )
        return overlay_customer_attributes(dict(customer)) if customer else None

    async def detail(self, connection: asyncpg.Connection, *, customer_id: str) -> dict[str, Any] | None:
        """Legacy full-history contract. New interactive consumers use the overview."""
        customer = await self.base(connection, customer_id=customer_id)
        if not customer:
            return None
        from sales_backend.repositories.customer_records import related_records
        from sales_backend.repositories.opportunities import OpportunityRepository

        related = await related_records(connection, customer_id)
        opportunities = await OpportunityRepository().for_customer(connection, customer_id)
        contacts = await connection.fetch(
            """
            SELECT id::text, name, title, department, contact_category_code,
                   relationship_role_code, is_primary
            FROM crm.contact
            WHERE customer_id = $1::uuid AND deleted_at IS NULL
            ORDER BY is_primary DESC, name
            """,
            customer_id,
        )
        result = overlay_customer_attributes(dict(customer))
        result.update(
            **related,
            opportunities=[dict(row) for row in opportunities],
            contacts=[dict(row) for row in contacts],
        )
        from sales_backend.domain.customer_profile import customer_profile

        result["profile"] = customer_profile(
            dict(customer), opportunities, related["visits"], contacts, related["risks"],
            risk_assessment_clear=await current_clear_assessment(connection, customer_id),
        )
        return result
