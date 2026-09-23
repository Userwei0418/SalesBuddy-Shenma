from __future__ import annotations

from typing import Any

import asyncpg

from sales_backend.domain.agent import ActorContext
from sales_backend.repositories.marketing import efficiency_rankings
from sales_backend.repositories.profile_growth import framework as competency_framework


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
               WHERE c.deleted_at IS NULL AND
                 ($1='manager' OR ($1='sales' AND EXISTS(SELECT 1 FROM crm.customer_sales_member cm
                   WHERE cm.customer_id=c.id AND cm.user_ref_id=$2::uuid))
                   OR ($1='supervisor' AND security.has_customer_access(c.id)))
            ),
            visible_opportunities AS (
              SELECT o.id, o.customer_id, COALESCE(o.amount, 0) AS amount,
                     o.status, o.created_at, o.updated_at
                FROM crm.opportunity o
               WHERE o.deleted_at IS NULL AND
                 ($1='manager' OR ($1='sales' AND o.owner_user_ref_id=$2::uuid)
                   OR ($1='supervisor' AND o.owner_team_id=ANY($3::uuid[])))
            ),
            visible_visits AS (
              SELECT v.id, v.customer_id, v.opportunity_id, v.interaction_at,
                     v.next_action, v.follow_up_record
                FROM activity.visit v
               WHERE v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
                 AND ($1='manager' OR ($1='sales' AND v.recorder_user_ref_id=$2::uuid)
                   OR ($1='supervisor' AND v.recorder_team_id=ANY($3::uuid[])))
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
                WHERE timezone('Asia/Shanghai',v.interaction_at)::date >= p.quarter_start) AS quarter_customer_count,
              (SELECT count(*) FROM visible_visits
                WHERE NULLIF(trim(next_action), '') IS NOT NULL) AS visits_with_next_action
            """,
            actor.role.value,
            actor.user_id,
            list(actor.team_ids),
        )
        values = dict(row)
        members: list[dict[str, Any]] = []
        teams: list[dict[str, Any]] = []
        if actor.role.value in {"supervisor", "manager"}:
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
                       AND (
                         $2 = 'manager'
                         OR ($2 = 'supervisor' AND tm.team_id = ANY($3::uuid[]))
                       )
                     ORDER BY visit_count DESC, opportunity_amount DESC, u.display_name
                    """,
                    actor.workspace_id,
                    actor.role.value,
                    list(actor.team_ids),
                )
            ]
        if actor.role.value == "manager":
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
               AND (($3 = 'manager' AND rb.role_code IN ('sales', 'supervisor'))
                    OR ($3 = 'supervisor' AND rb.role_code = 'sales' AND t.id = ANY($4::uuid[])))
             LIMIT 1
            """,
            actor.workspace_id,
            account_code,
            actor.role.value,
            list(actor.team_ids),
        )
        return dict(row) if row else None

    async def ensure_daily_competency_review(
        self,
        connection: asyncpg.Connection,
        actor: ActorContext,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        framework = await connection.fetchrow(
            """
            SELECT version_no
              FROM config.sales_competency_framework
             WHERE status = 'active' AND effective_to = 'infinity'
               AND (workspace_id IS NULL OR workspace_id = $1::uuid)
             ORDER BY (workspace_id IS NOT NULL) DESC, version_no DESC
             LIMIT 1
            """,
            actor.workspace_id,
        )
        if not framework:
            raise LookupError("active competency framework not found")
        review = await connection.fetchrow(
            """
            INSERT INTO insight.sales_competency_review (
              workspace_id, subject_user_ref_id, review_date, framework_version, status
            ) VALUES (
              $1::uuid, $2::uuid,
              timezone('Asia/Shanghai', clock_timestamp())::date,
              $3, 'queued'
            )
            ON CONFLICT (workspace_id, subject_user_ref_id, review_date, framework_version)
            DO UPDATE SET status = CASE
              WHEN insight.sales_competency_review.status = 'running' THEN 'running'
              WHEN $4::boolean AND insight.sales_competency_review.status = 'succeeded' THEN 'queued'
              WHEN insight.sales_competency_review.status = 'failed' THEN 'queued'
              ELSE insight.sales_competency_review.status
            END,
            error_code = CASE
              WHEN insight.sales_competency_review.status IN ('failed')
                OR ($4::boolean AND insight.sales_competency_review.status = 'succeeded') THEN NULL
              ELSE insight.sales_competency_review.error_code
            END,
            error_detail = CASE
              WHEN insight.sales_competency_review.status IN ('failed')
                OR ($4::boolean AND insight.sales_competency_review.status = 'succeeded') THEN NULL
              ELSE insight.sales_competency_review.error_detail
            END,
            reviewed_at = CASE
              WHEN $4::boolean AND insight.sales_competency_review.status = 'succeeded' THEN NULL
              ELSE insight.sales_competency_review.reviewed_at
            END
            RETURNING id::text, status, review_date
            """,
            actor.workspace_id,
            actor.user_id,
            framework["version_no"],
            force,
        )
        if review["status"] == "queued":
            await connection.execute(
                """
                INSERT INTO ops.job (
                  workspace_id, job_type, aggregate_type, aggregate_id,
                  payload, priority
                )
                SELECT $1::uuid, 'sales_competency.review',
                       'sales_competency_review', $2::uuid, $3::jsonb, 45
                 WHERE NOT EXISTS (
                   SELECT 1 FROM ops.job
                    WHERE aggregate_id = $2::uuid
                      AND job_type = 'sales_competency.review'
                      AND status IN ('queued', 'running', 'failed')
                 )
                """,
                actor.workspace_id,
                review["id"],
                {
                    "user_id": actor.user_id,
                    "role": actor.role.value,
                    "data_scope": actor.data_scope.value,
                    "team_ids": list(actor.team_ids),
                },
            )
        return dict(review)

    async def competency_growth(
        self,
        connection: asyncpg.Connection,
        actor: ActorContext,
        *,
        days: int,
        subject_user_id: str | None = None,
    ) -> dict[str, Any]:
        subject_id = subject_user_id or actor.user_id
        framework = await competency_framework(connection, actor.workspace_id)
        reviews = await connection.fetch(
            """
            SELECT id::text, review_date, status, overall_score,
                   dimension_scores, summary, strengths, improvements,
                   input_snapshot, reviewed_at, error_code
             FROM insight.sales_competency_review
             WHERE workspace_id = $1::uuid
               AND subject_user_ref_id = $2::uuid
               AND review_date >= timezone('Asia/Shanghai', clock_timestamp())::date - $3::int
             ORDER BY review_date DESC
            """,
            actor.workspace_id,
            subject_id,
            days,
        )
        items = [dict(row) for row in reviews]
        latest = next((item for item in items if item["status"] == "succeeded"), None)
        return {
            "framework": dict(framework) if framework else None,
            "latest": latest,
            "today_status": items[0]["status"] if items else "missing",
            "history": list(reversed([item for item in items if item["status"] == "succeeded"])),
        }
