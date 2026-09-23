# ruff: noqa: S608 -- Scope fragment uses a fixed internal alias and parameterized values.
from __future__ import annotations

from typing import Any

import asyncpg

from sales_backend.domain.agent import ActorContext
from sales_backend.domain.reporting import quarter_window
from sales_backend.repositories.dashboard import DashboardRepository, owner_scope
from sales_backend.repositories.directory import DirectoryRepository
from sales_backend.repositories.visit_normalize import QUADRANT_NAMES


class WorkbenchRepository:
    async def load(self, connection: asyncpg.Connection, actor: ActorContext) -> dict[str, Any]:
        quarter_start, quarter_end = quarter_window()
        members = await DirectoryRepository().members(connection, actor)
        customers = await connection.fetch(
            """
            SELECT c.id::text, c.name, c.industry_code, c.customer_type_code,
                   c.source_code, c.lifecycle_status, c.primary_partner_name,
                   c.level_code AS level, c.created_at,
                   u.display_name AS owner, t.name AS team, t.region_code,
                   COALESCE(q.potential_score, 0) AS potential,
                   COALESCE(q.relationship_score, 0) AS relationship,
                   q.quadrant_code,
                   score.input_snapshot->'company_policy' AS quadrant_policy,
                   latest.interaction_at AS latest_visit_at,
                   risk.title AS risk
            FROM crm.customer c
            LEFT JOIN platform.user_ref u ON u.id = c.owner_user_ref_id
            LEFT JOIN platform.team t ON t.id = c.owner_team_id
            LEFT JOIN crm.v_customer_current_quadrant q ON q.id = c.id
            LEFT JOIN insight.quadrant_score score ON score.customer_id=c.id AND score.valid_to='infinity'
              AND score.subject_user_ref_id=common.current_user_ref_id()
            LEFT JOIN LATERAL (
              SELECT v.interaction_at FROM activity.visit v
              WHERE v.customer_id = c.id AND v.deleted_at IS NULL
              ORDER BY v.interaction_at DESC LIMIT 1
            ) latest ON true
            LEFT JOIN LATERAL (
              SELECT r.title FROM insight.risk r
              WHERE r.customer_id = c.id AND r.deleted_at IS NULL
                AND r.status IN ('new','pending','in_progress','escalated')
              ORDER BY CASE r.severity_code WHEN 'critical' THEN 1 WHEN 'high' THEN 2 ELSE 3 END,
                       r.opened_at DESC LIMIT 1
            ) risk ON true
            WHERE c.deleted_at IS NULL
            ORDER BY latest.interaction_at DESC NULLS LAST, c.name
            """
        )
        metrics = await connection.fetchrow(
            """
            SELECT
              (SELECT max(interaction_at) FROM activity.visit WHERE deleted_at IS NULL) AS source_date,
              (SELECT count(*) FROM crm.customer WHERE deleted_at IS NULL
                AND NOT EXISTS(SELECT 1 FROM crm.customer_sales_member cm
                  WHERE cm.customer_id=crm.customer.id)) AS pending_assignment_count
            """
        )
        performance = await connection.fetchrow(
            f"""  -- fixed scope fragment, all request values are bound
            WITH standardized AS (
              SELECT *,
                     CASE stage_code
                       WHEN 'identified' THEN 10
                       WHEN 'discovery' THEN 10
                       WHEN 'qualified' THEN 30
                       WHEN 'qualification' THEN 30
                       WHEN 'solution' THEN 50
                       WHEN 'proposal' THEN 70
                       WHEN 'quotation' THEN 70
                       WHEN 'negotiation' THEN 90
                       ELSE CASE
                         WHEN COALESCE(probability, 0) < 20 THEN 10
                         WHEN probability < 40 THEN 30
                         WHEN probability < 60 THEN 50
                         WHEN probability < 80 THEN 70
                         ELSE 90
                       END
                     END AS standard_probability
                FROM crm.opportunity o
               WHERE deleted_at IS NULL AND {owner_scope("o")}
            )
            SELECT
              COALESCE(sum(amount) FILTER (
                WHERE status = 'won'
                  AND closed_at >= $5::timestamptz
              ), 0) AS quarter_won_amount,
              COALESCE(max(amount) FILTER (
                WHERE status = 'won'
                  AND closed_at >= $5::timestamptz
              ), 0) AS quarter_largest_won,
              COALESCE(sum(amount * standard_probability / 100.0) FILTER (
                WHERE status = 'open'
                  AND expected_close_date >= $6::date
                  AND expected_close_date < $7::date
              ), 0) AS quarter_weighted_forecast,
              COALESCE(sum(amount) FILTER (WHERE status = 'open'), 0) AS open_amount,
              count(*) FILTER (WHERE status = 'open' AND standard_probability >= 30) AS qualified_open_count
            FROM standardized
            """,
            actor.role.value,
            actor.user_id,
            list(actor.team_ids),
            False,
            quarter_start,
            quarter_start.date(),
            quarter_end.date(),
        )
        tasks = await connection.fetch(
            """
            SELECT t.id::text, t.title, t.description, t.priority_code,t.customer_id::text,t.opportunity_id::text,t.target_position,
                   t.status, t.due_at, c.name AS customer_name,
                   COALESCE(assignee.display_name,'待领取') AS owner, team.name AS team
              FROM workflow.task t
              LEFT JOIN workflow.task_assignee ta
                ON ta.task_id = t.id AND ta.responsibility = 'owner'
              LEFT JOIN platform.user_ref assignee ON assignee.id = ta.assignee_user_ref_id
              -- A primary-team label is one row even when historical memberships overlap.
              LEFT JOIN LATERAL (
                SELECT tm.team_id FROM platform.team_membership tm
                WHERE tm.workspace_id = assignee.workspace_id AND tm.user_ref_id = assignee.id AND tm.is_primary
                  AND clock_timestamp() >= tm.valid_from AND clock_timestamp() < tm.valid_to
                ORDER BY tm.valid_from DESC, tm.id LIMIT 1
              ) primary_team ON true
              LEFT JOIN platform.team team ON team.id = primary_team.team_id
              LEFT JOIN crm.customer c ON c.id = t.customer_id
             WHERE t.deleted_at IS NULL
               AND t.status IN ('pending_confirm', 'pending_execution', 'in_progress', 'deferred', 'pending_review')
             ORDER BY t.due_at, t.created_at DESC
            """
        )
        risks = await connection.fetch(
            """
            SELECT r.id::text, r.title, r.description, r.severity_code,r.customer_id::text,r.opportunity_id::text,
                   r.status, r.due_at, c.name AS customer_name,
                   owner.display_name AS owner, team.name AS team
              FROM insight.risk r
              LEFT JOIN crm.customer c ON c.id = r.customer_id
              LEFT JOIN platform.user_ref owner ON owner.id = r.owner_user_ref_id
              -- Match V068's primary-team display rule without multiplying risk facts.
              LEFT JOIN LATERAL (
                SELECT tm.team_id FROM platform.team_membership tm
                WHERE tm.workspace_id = owner.workspace_id AND tm.user_ref_id = owner.id AND tm.is_primary
                  AND clock_timestamp() >= tm.valid_from AND clock_timestamp() < tm.valid_to
                ORDER BY tm.valid_from DESC, tm.id LIMIT 1
              ) primary_team ON true
              LEFT JOIN platform.team team ON team.id = primary_team.team_id
             WHERE r.deleted_at IS NULL
               AND r.status IN ('new', 'pending', 'in_progress', 'escalated')
             ORDER BY CASE r.severity_code
               WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END,
               r.opened_at DESC
            """
        )
        product_links = await connection.fetch(
            """
            SELECT cp.customer_id::text, cp.opportunity_id::text,
                   p.name AS product_name, cp.relationship_status
              FROM crm.customer_product cp
              JOIN crm.product p ON p.id = cp.product_id
             WHERE cp.deleted_at IS NULL
             ORDER BY cp.updated_at DESC
            """
        )
        facts = DashboardRepository()
        opportunities = await facts.opportunities(connection, actor)
        quarter_forecasts = await facts.forecasts(connection, actor)
        recent_visits = await facts.recent_visits(connection, actor)
        metrics = dict(metrics)
        metrics.update(
            opportunities=len(opportunities),
            forecast=sum(item["amount"] or 0 for item in opportunities),
            visits=len(recent_visits),
            risks=len(risks),
        )
        normalized_customers = []
        for row in customers:
            item = dict(row)
            # A newly assigned customer has factual 0/0 scores until the first
            # quadrant review. It still belongs on the battle map as low
            # potential / unfamiliar relationship instead of disappearing.
            item["quadrant"] = QUADRANT_NAMES.get(item.pop("quadrant_code", None), "见单打单")
            item["risk"] = item.get("risk") or "暂无重大风险"
            normalized_customers.append(item)
        focus = next((item for item in normalized_customers if item["risk"] != "暂无重大风险"), None)
        return {
            "members": members,
            "customers": normalized_customers,
            "summary": {
                **dict(metrics),
                **dict(performance),
                "contract_target": None,
                "collection_target": None,
                "quarter_collection_amount": None,
                "quarter_expected_collection": None,
            },
            "tasks": [dict(row) for row in tasks],
            "risks": [dict(row) for row in risks],
            "opportunities": opportunities,
            "quarter_forecasts": [dict(row) for row in quarter_forecasts],
            "recent_visits": recent_visits,
            "product_links": [dict(row) for row in product_links],
            "management_insight": {
                "customer_id": focus["id"],
                "title": f"{focus['name']} · {focus['risk']}",
                "text": "请进入客户档案查看来源拜访、风险证据与下一步行动。",
            }
            if focus
            else None,
        }
