"""Complete subject-scoped profile facts, before UI pagination."""

from datetime import date

from sales_backend.repositories.profile_scope import resolve_scope


class ProfilePerformanceRepository:
    async def scope(self, connection, actor, **kwargs):
        return await resolve_scope(connection, actor, **kwargs)

    async def members(self, connection, actor, scope):
        return scope['member_ids']

    async def targets(self, connection, actor, scope, period_start):
        return {r['kind']: r['amount'] for r in await connection.fetch(
            """SELECT kind,amount FROM crm.sales_target WHERE workspace_id=$1::uuid
             AND period_type='quarter' AND period_start=$2 AND kind IN ('collection','recognized')
             AND scope_type=$3 AND user_ref_id IS NOT DISTINCT FROM $4::uuid
             AND team_id IS NOT DISTINCT FROM $5::uuid
             AND (scope_type<>'department' OR department_code='sales')""",
            actor.workspace_id, period_start, scope['scope'], scope['user_id'], scope['team_id'])}

    async def facts(self, connection, members, year, as_of, visit_start, scope, *,
                    target_start, target_end, structure_start=None, structure_end=None):
        # Linked actuals belong to the opportunity owner; unlinked historical actuals
        # belong to the primary owner. A shared customer never multiplies money.
        row = await connection.fetchrow(
            """WITH actuals AS (
              SELECT a.customer_id,a.kind,a.amount,a.occurred_on FROM crm.customer_actual a
               LEFT JOIN crm.customer c ON c.id=a.customer_id AND c.deleted_at IS NULL
               LEFT JOIN crm.opportunity o ON o.id=a.opportunity_id AND o.deleted_at IS NULL
              WHERE a.voided_at IS NULL AND security.customer_reference(a.customer_id) IS NOT NULL
                AND a.occurred_on BETWEEN $2 AND $4
                AND (($6='team' AND CASE WHEN a.opportunity_id IS NOT NULL
                  THEN o.owner_team_id ELSE c.owner_team_id END=ANY($7::uuid[])) OR ($6<>'team' AND
                  CASE WHEN a.opportunity_id IS NOT NULL THEN o.owner_user_ref_id ELSE c.owner_user_ref_id END
                  =ANY($1::uuid[])))
            ), cohort AS (
              SELECT customer_id,sum(amount) FILTER(WHERE occurred_on<$3) AS previous,
                COALESCE(sum(amount) FILTER(WHERE occurred_on>=$3),0) AS current
              FROM actuals WHERE kind='recognized' GROUP BY customer_id
              HAVING sum(amount) FILTER(WHERE occurred_on<$3)>0
            ), opportunities AS (
              SELECT o.amount,o.status,o.created_at FROM crm.opportunity o
              WHERE o.deleted_at IS NULL AND o.status IN ('open','won')
                AND (($6='team' AND o.owner_team_id=ANY($7::uuid[])) OR
                  ($6<>'team' AND o.owner_user_ref_id=ANY($1::uuid[])))
            ), structure_opportunities AS (
              SELECT * FROM opportunities WHERE
                ($10::date IS NULL OR created_at>=($10::date::timestamp AT TIME ZONE 'Asia/Shanghai'))
                AND ($11::date IS NULL OR created_at<(($11::date+1)::timestamp AT TIME ZONE 'Asia/Shanghai'))
            ), visits AS (
              SELECT follow_up_score,customer_id FROM activity.visit WHERE recorder_user_ref_id=ANY($1::uuid[])
                 AND deleted_at IS NULL AND status IN ('confirmed','archived')
                 AND interaction_at >= ($5::date::timestamp AT TIME ZONE 'Asia/Shanghai')
                 AND interaction_at < (($12::date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
            ) SELECT
              (SELECT sum(amount) FROM actuals WHERE occurred_on BETWEEN $8 AND $9 AND kind='recognized') AS recognized,
              (SELECT sum(amount) FROM actuals WHERE occurred_on BETWEEN $8 AND $9 AND kind='collection') AS collection,
              (SELECT sum(previous) FROM cohort) AS retention_previous,
              (SELECT sum(current) FROM cohort) AS retention_current,
              (SELECT count(*) FROM cohort) AS retention_customers,
              (SELECT COALESCE(sum(amount),0) FROM opportunities WHERE status='open') AS active_opportunity_amount,
              (SELECT COALESCE(sum(amount),0) FROM opportunities WHERE status='won') AS won_amount,
              (SELECT avg(follow_up_score) FROM visits) AS followup_score,
              (SELECT count(*) FROM visits) AS followup_count,
              (SELECT count(DISTINCT customer_id) FROM visits) AS followup_customer_count,
              (SELECT count(follow_up_score) FROM visits) AS followup_evaluated_count,
              (SELECT count(*) FROM structure_opportunities) AS opportunity_count,
              (SELECT count(*) FROM structure_opportunities WHERE amount>=0) AS opportunity_evaluated_count,
              (SELECT count(*) FROM structure_opportunities WHERE amount>=500000) AS opportunity_ab_count
            """, members, date(year-1, 1, 1), date(year, 1, 1), min(as_of, date(year, 12, 31)), visit_start,
            scope['scope'], scope['team_ids'], target_start, target_end, structure_start, structure_end, as_of)
        return dict(row)
