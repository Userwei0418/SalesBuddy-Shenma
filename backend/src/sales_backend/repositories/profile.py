from __future__ import annotations

from typing import Any

import asyncpg

from sales_backend.domain.agent import ActorContext
from sales_backend.repositories.marketing import efficiency_rankings


def _percent(numerator: Any, denominator: int) -> float:
    """占比统一保留一位小数；分母为 0 时返回 0，不返回 null，前端直接渲染。"""
    if not denominator:
        return 0
    return round(int(numerator or 0) / denominator * 100, 1)


def evaluation_metrics(values: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """把画像页的聚合计数换算成成熟度和效率两组指标。"""
    customer_count = int(values["customer_count"] or 0)
    opportunity_count = int(values["opportunity_count"] or 0)
    visit_count = int(values["visit_count"] or 0)
    return {
        "maturity": {
            "active_opportunity_amount": values["active_opportunity_amount"] or 0,
            "won_amount": values["won_amount"] or 0,
            "win_rate": _percent(values["won_count"], opportunity_count),
            "a_customer_share": _percent(values["a_customer_count"], customer_count),
        },
        "efficiency": {
            "week_visit_count": int(values["week_visit_count"] or 0),
            "quarter_visit_count": int(values["quarter_visit_count"] or 0),
            "year_visit_count": int(values["year_visit_count"] or 0),
            "historical_visit_count": visit_count,
            "quarter_customer_count": int(values["quarter_customer_count"] or 0),
            "followup_closure_rate": _percent(values["visits_with_next_action"], visit_count),
        },
    }


class ProfileRepository:
    async def is_competency_subject(self, connection, actor):
        return await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM platform.role_binding WHERE workspace_id=$1::uuid "
            "AND user_ref_id=$2::uuid AND role_code IN ('sales','supervisor') "
            "AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to)",
            actor.workspace_id, actor.user_id,
        )

    async def evaluation_summary(self, connection: asyncpg.Connection, actor: ActorContext) -> dict[str, Any]:
        """Return database-backed maturity/efficiency facts within the actor's RLS scope."""
        row = await connection.fetchrow(
            """
            WITH periods AS (
              SELECT timezone('Asia/Shanghai', clock_timestamp())::date AS today,
                     date_trunc('week', timezone('Asia/Shanghai', clock_timestamp()))::date AS week_start,
                     date_trunc('quarter', timezone('Asia/Shanghai', clock_timestamp()))::date AS quarter_start,
                     date_trunc('year', timezone('Asia/Shanghai', clock_timestamp()))::date AS year_start
            ),
            visible_customers AS (
              SELECT c.id, c.level_code
                FROM crm.customer c
               WHERE c.deleted_at IS NULL AND 'customer'=ANY(c.business_identities) AND
                 security.authorization_customer('profile.sales_read',c.id)
            ),
            visible_opportunities AS (
              SELECT o.id, o.customer_id, COALESCE(o.amount, 0) AS amount,
                     o.status, o.created_at, o.updated_at
                FROM crm.opportunity o
               WHERE o.deleted_at IS NULL AND
                 security.authorization_opportunity('profile.sales_read',o.id)
            ),
            visible_visits AS (
              SELECT v.id, v.customer_id, v.opportunity_id, v.interaction_at,
                     v.next_action, v.follow_up_record
                FROM activity.visit v
               WHERE v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
                 AND security.authorization_visit('profile.sales_read',v.id)
                 AND timezone('Asia/Shanghai',v.interaction_at)::date
                   <= timezone('Asia/Shanghai',clock_timestamp())::date
            )
            SELECT
              (SELECT COALESCE(sum(amount),0) FROM visible_opportunities
                WHERE status = 'open') AS active_opportunity_amount,
              (SELECT COALESCE(sum(amount),0) FROM visible_opportunities WHERE status = 'won') AS won_amount,
              (SELECT count(*) FROM visible_opportunities WHERE status = 'won') AS won_count,
              (SELECT count(*) FROM visible_opportunities WHERE status IN ('open','won')) AS opportunity_count,
              (SELECT count(*) FROM visible_customers) AS customer_count,
              (SELECT count(*) FROM visible_customers WHERE level_code IN ('A','Tier-1')) AS a_customer_count,
              (SELECT count(*) FROM visible_visits) AS visit_count,
              (SELECT count(*) FROM visible_visits v, periods p
                WHERE timezone('Asia/Shanghai',v.interaction_at)::date >= p.week_start) AS week_visit_count,
              (SELECT count(*) FROM visible_visits v, periods p
                WHERE timezone('Asia/Shanghai',v.interaction_at)::date >= p.quarter_start) AS quarter_visit_count,
              (SELECT count(*) FROM visible_visits v, periods p
                WHERE timezone('Asia/Shanghai',v.interaction_at)::date >= p.year_start) AS year_visit_count,
              (SELECT count(DISTINCT customer_id) FROM visible_visits v, periods p
                WHERE timezone('Asia/Shanghai',v.interaction_at)::date >= p.quarter_start
                  AND v.customer_id IN (SELECT id FROM visible_customers)) AS quarter_customer_count,
              (SELECT count(*) FROM visible_visits
                WHERE NULLIF(trim(next_action), '') IS NOT NULL) AS visits_with_next_action
            """,
        )
        values = dict(row)
        members: list[dict[str, Any]] = []
        teams: list[dict[str, Any]] = []
        if await connection.fetchval("SELECT EXISTS(SELECT 1 FROM security.authorization_current_grants() WHERE permission_code='profile.sales_read' AND effect='allow' AND scope_code IN ('teams','workspace'))"):
            members = [
                dict(item)
                for item in await connection.fetch(
                    """
                    SELECT u.account_code, u.display_name AS name,
                           COALESCE(v.visit_count, 0)::int AS visit_count,
                           COALESCE(v.customer_count, 0)::int AS customer_count,
                           COALESCE(o.opportunity_amount, 0) AS opportunity_amount
                      FROM platform.user_ref u
                      JOIN platform.role_binding rb ON rb.user_ref_id = u.id
                       AND rb.role_code = 'sales'
                       AND clock_timestamp() >= rb.valid_from AND clock_timestamp() < rb.valid_to
                      JOIN platform.team_membership tm ON tm.user_ref_id = u.id AND tm.is_primary
                       AND clock_timestamp() >= tm.valid_from AND clock_timestamp() < tm.valid_to
                      LEFT JOIN LATERAL (
                        SELECT count(*) AS visit_count, count(DISTINCT customer_id) AS customer_count
                          FROM activity.visit x
                         WHERE x.recorder_user_ref_id = u.id AND x.deleted_at IS NULL
                           AND x.status IN ('confirmed','archived')
                      ) v ON true
                      LEFT JOIN LATERAL (
                        SELECT COALESCE(sum(amount), 0) AS opportunity_amount
                          FROM crm.opportunity x
                         WHERE x.owner_user_ref_id = u.id AND x.deleted_at IS NULL AND x.status = 'open'
                      ) o ON true
                     WHERE u.workspace_id = $1::uuid AND u.status = 'active' AND u.deleted_at IS NULL
                       AND security.authorization_subject('profile.sales_read','person',u.id,NULL)
                     ORDER BY visit_count DESC, opportunity_amount DESC, u.display_name
                    """,
                    actor.workspace_id,
                )
            ]
        if await connection.fetchval("SELECT security.authorization_subject('profile.sales_read','department',NULL,NULL)"):
            teams = [
                dict(item)
                for item in await connection.fetch(
                    """
                    SELECT t.name,
                           count(DISTINCT u.id)::int AS member_count,
                           count(DISTINCT v.id)::int AS visit_count,
                           count(DISTINCT v.customer_id)::int AS customer_count,
                           COALESCE(o.opportunity_amount, 0) AS opportunity_amount
                      FROM platform.team t
                      JOIN platform.team_membership tm ON tm.team_id = t.id AND tm.is_primary
                      JOIN platform.user_ref u ON u.id = tm.user_ref_id
                      LEFT JOIN activity.visit v ON v.recorder_user_ref_id = u.id
                       AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
                      LEFT JOIN LATERAL (
                        SELECT COALESCE(sum(x.amount), 0) AS opportunity_amount
                          FROM crm.opportunity x
                         WHERE x.owner_team_id = t.id
                           AND x.deleted_at IS NULL AND x.status = 'open'
                      ) o ON true
                     WHERE t.workspace_id = $1::uuid AND t.deleted_at IS NULL
                       AND security.authorization_subject('profile.sales_read','team',NULL,t.id)
                     GROUP BY t.id, t.name, o.opportunity_amount
                     ORDER BY visit_count DESC, t.name
                    """,
                    actor.workspace_id,
                )
            ]
        metrics = evaluation_metrics(values)
        metrics["efficiency"].update(await efficiency_rankings(connection, actor))
        return {
            "data_source": "database",
            "scope": actor.data_scope.value,
            **metrics,
            "members": members,
            "teams": teams,
        }

    async def visible_sales_subject(
        self, connection: asyncpg.Connection, actor: ActorContext, *, account_code: str
    ) -> dict[str, Any] | None:
        row = await connection.fetchrow(
            """
            SELECT u.id::text, u.account_code, u.display_name AS name,
                   rb.role_code,
                   t.id::text AS team_id, t.name AS team
              FROM platform.user_ref u
              JOIN platform.role_binding rb
                ON rb.user_ref_id = u.id
               AND rb.role_code IN ('sales', 'supervisor')
               AND clock_timestamp() >= rb.valid_from
               AND clock_timestamp() < rb.valid_to
              JOIN platform.team_membership tm
                ON tm.user_ref_id = u.id AND tm.is_primary
               AND clock_timestamp() >= tm.valid_from
               AND clock_timestamp() < tm.valid_to
              JOIN platform.team t ON t.id = tm.team_id AND t.deleted_at IS NULL
             WHERE u.workspace_id = $1::uuid
               AND u.account_code = upper($2)
               AND u.status = 'active' AND u.deleted_at IS NULL
               AND security.authorization_subject('profile.sales_read','person',u.id,NULL)
             LIMIT 1
            """,
            actor.workspace_id,
            account_code,
        )
        return dict(row) if row else None

    async def ensure_daily_competency_review(self, connection, actor, *, force=False):
        """Compatibility entry: requests append/reuse generations, never reset history."""
        from sales_backend.repositories.competency_lifecycle import request_review
        result = await request_review(connection, actor)
        return {**result, "id": result["review_id"]}

    async def competency_growth(
        self,
        connection: asyncpg.Connection,
        actor: ActorContext,
        *,
        days: int,
        subject_user_id: str | None = None,
    ) -> dict[str, Any]:
        from sales_backend.repositories.competency_lifecycle import can_review
        from sales_backend.services.growth_projection import (
            CONTRACT_VERSION,
            current_review,
            member_coverage,
            public_review,
        )

        subject_id = subject_user_id or actor.user_id
        if not await connection.fetchval("SELECT security.authorization_subject('profile.sales_read','person',$1::uuid,NULL)", subject_id):
            raise PermissionError("该成员不在画像查看范围内")
        reviews = await connection.fetch(
            """
            SELECT id::text, review_date, status, overall_score, framework_version,
                   generation_no, trigger_type, created_at AS requested_at,
                   evaluation_state, input_fingerprint,
                   dimension_scores, summary, strengths, improvements,
                   input_snapshot, reviewed_at
             FROM insight.sales_competency_review
             WHERE workspace_id = $1::uuid
               AND subject_user_ref_id = $2::uuid
               AND review_date >= timezone('Asia/Shanghai', clock_timestamp())::date - $3::int
             ORDER BY review_date DESC, generation_no DESC, id DESC
            """,
            actor.workspace_id,
            subject_id,
            days,
        )
        items = [dict(row) for row in reviews]
        completed = [public_review(item) for item in items if item["status"] == "succeeded"]
        latest = completed[0] if completed else None
        basis = latest["score_basis"] if latest else {}
        return {
            "contract_version": CONTRACT_VERSION,
            "assessment_kind": "sales_competency",
            "data_source": "database",
            "projection": "person",
            "framework": basis.get("framework_snapshot"),
            "score_policy": basis.get("score_policy"),
            "latest": latest,
            "today_status": items[0]["status"] if items else "missing",
            "current_review": current_review(items[0]) if items else None,
            "can_review": subject_id == actor.user_id and await can_review(connection, actor),
            "history": list(reversed(completed)),
            "coverage": member_coverage([latest] if latest else [], 1),
        }
