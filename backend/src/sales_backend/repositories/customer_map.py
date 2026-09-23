"""Active authorized map points, with bounded fields and database ACV facts.

This read model intentionally excludes customer attributes, import metadata and
visit bodies. Customer scope is resolved before related facts are aggregated.
FDE participation selects customers; the facts then retain RLS-visible customer
context (including other visible opportunities for that customer).
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date

from sales_backend.repositories.customer_assets import today


def activity_since(as_of: date) -> date:
    """Six calendar months, clamping month-end, never a fixed 180-day window."""
    month_index = as_of.year * 12 + as_of.month - 1 - 6
    year, month = divmod(month_index, 12)
    month += 1
    return date(year, month, min(as_of.day, monthrange(year, month)[1]))


class CustomerMapRepository:
    async def read(self, connection, *, fde_user_ids=None, as_of=None):
        as_of = as_of or today()
        rows = await connection.fetch(
            """
            WITH recent_visits AS MATERIALIZED (
              SELECT v.id,v.customer_id,v.opportunity_id,v.interaction_at FROM activity.visit v
              WHERE v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
                AND v.interaction_at >= ($2::date::timestamp AT TIME ZONE 'Asia/Shanghai')
                AND v.interaction_at < (($3::date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
            ) , visit_customers AS MATERIALIZED (
              SELECT id,customer_id,interaction_at FROM recent_visits WHERE customer_id IS NOT NULL
              UNION
              SELECT v.id,o.customer_id,v.interaction_at FROM recent_visits v
              JOIN crm.opportunity o ON o.id=v.opportunity_id AND o.deleted_at IS NULL
              UNION
              SELECT v.id,o.customer_id,v.interaction_at FROM recent_visits v
              JOIN activity.visit_opportunity vo ON vo.visit_id=v.id
              JOIN crm.opportunity o ON o.id=vo.opportunity_id AND o.deleted_at IS NULL
              WHERE v.opportunity_id IS NULL
            ), active_customers AS MATERIALIZED (
              SELECT customer_id,max(interaction_at) AS latest_visit_at,
                count(*) FILTER (WHERE interaction_at >=
                  date_trunc('week',timezone('Asia/Shanghai',clock_timestamp()))
                    AT TIME ZONE 'Asia/Shanghai')::integer AS weekly_follow_up_count
              FROM visit_customers GROUP BY customer_id
            ), scoped_opportunities AS MATERIALIZED (
              SELECT DISTINCT o.id,o.customer_id
              FROM crm.opportunity o JOIN crm.opportunity_participant p ON p.opportunity_id=o.id
              WHERE $1::uuid[] IS NOT NULL AND o.deleted_at IS NULL
                AND p.user_ref_id=ANY($1::uuid[]) AND p.participant_role='fde'
                AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
                AND security.fde_user_is_active(p.user_ref_id)
            ), scoped_people AS MATERIALIZED (
              SELECT DISTINCT o.customer_id,u.id::text,u.display_name AS name
              FROM scoped_opportunities o JOIN crm.opportunity_participant p ON p.opportunity_id=o.id
              JOIN platform.user_ref u ON u.id=p.user_ref_id
              WHERE p.participant_role='fde'
                AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
                AND security.fde_user_is_active(u.id)
            ), people AS (
              SELECT customer_id,jsonb_agg(jsonb_build_object('id',id,'name',name)
                ORDER BY name,id) AS members FROM scoped_people GROUP BY customer_id
            ), authorized AS MATERIALIZED (
              SELECT c.id,c.workspace_id,c.name,c.industry_code,c.customer_type_code,c.level_code,
                c.lifecycle_status,c.data_kind,c.owner_user_ref_id,c.owner_team_id,
                security.profile_customer_owner(c.id) AS claimant_id
              FROM active_customers active JOIN LATERAL (
                SELECT c.* FROM crm.customer c WHERE c.id=active.customer_id AND c.deleted_at IS NULL LIMIT 1
              ) c ON true
              WHERE true
                AND (common.current_role_code()<>'sales'
                     OR security.profile_customer_owner(c.id)=common.current_user_ref_id())
                AND ($1::uuid[] IS NULL OR c.id IN (SELECT customer_id FROM scoped_opportunities))
            )
            SELECT c.id::text,c.name,c.industry_code,c.customer_type_code,c.level_code,
              c.lifecycle_status,c.data_kind,c.owner_user_ref_id::text,c.owner_team_id::text,
              u.display_name AS owner_name,t.name AS team_name,
              CASE WHEN u.id IS NULL THEN '[]'::jsonb ELSE
                jsonb_build_array(jsonb_build_object('id',u.id::text,'name',u.display_name)) END AS sales_members,
              q.potential_score,q.relationship_score,q.quadrant_code,
              (SELECT s.input_snapshot->'company_policy' FROM insight.quadrant_score s
                WHERE s.customer_id=c.id AND s.valid_to='infinity'
                  AND s.subject_user_ref_id=common.current_user_ref_id()) AS quadrant_policy,
              active.latest_visit_at,active.weekly_follow_up_count,
              risk.title AS risk_title,risk.severity_code AS risk_severity,
              opportunities.name AS opportunity_name,opportunities.stage_code AS opportunity_stage,
              opportunities.amount AS opportunity_amount,opportunities.amount AS acv_amount,
              opportunities.open_count,opportunities.unknown_amount_count,
              opportunities.plan_close_dates,opportunities.plan_close_periods,
              COALESCE(people.members,'[]'::jsonb) AS fde_members
            FROM authorized c
            JOIN active_customers active ON active.customer_id=c.id
            LEFT JOIN platform.user_ref u ON u.id=c.claimant_id
            LEFT JOIN platform.team t ON t.id=c.owner_team_id
            -- Same latest visible score as v_customer_current_quadrant, without
            -- making the invoker view read/authorize the customer a second time.
            LEFT JOIN LATERAL (
              SELECT score.potential_score,score.relationship_score,score.quadrant_code
              FROM insight.quadrant_score score
              WHERE score.customer_id=c.id AND score.workspace_id=c.workspace_id
                AND score.valid_to='infinity'
              ORDER BY score.calculated_at DESC,score.id DESC LIMIT 1
            ) q ON true
            LEFT JOIN LATERAL (
              SELECT r.title,r.severity_code FROM insight.risk r
              WHERE r.customer_id=c.id AND r.deleted_at IS NULL
                AND r.status IN ('new','pending','in_progress','escalated')
              ORDER BY CASE r.severity_code WHEN 'critical' THEN 1 WHEN 'high' THEN 2 ELSE 3 END,
                r.opened_at DESC LIMIT 1
            ) risk ON true
            LEFT JOIN LATERAL (
              SELECT count(*)::integer AS open_count,
                count(*) FILTER(WHERE o.amount IS NULL OR o.amount<0)::integer AS unknown_amount_count,
                CASE WHEN count(*) FILTER(WHERE o.amount IS NULL OR o.amount<0)>0 THEN NULL
                     ELSE COALESCE(sum(o.amount),0) END AS amount,
                (array_agg(o.name ORDER BY o.updated_at DESC,o.id))[1] AS name,
                (array_agg(o.stage_code ORDER BY o.updated_at DESC,o.id))[1] AS stage_code,
                COALESCE(array_agg(o.expected_close_date ORDER BY o.id),'{}'::date[]) AS plan_close_dates,
                COALESCE(jsonb_agg(jsonb_build_object('date',o.expected_close_date,
                  'year',COALESCE(extract(year FROM o.expected_close_date)::integer,o.expected_close_year),
                  'quarter',COALESCE(extract(quarter FROM o.expected_close_date)::integer,o.expected_close_quarter))
                  ORDER BY o.id),'[]'::jsonb) AS plan_close_periods
              FROM crm.opportunity o WHERE o.customer_id=c.id AND o.deleted_at IS NULL AND o.status='open'
            ) opportunities ON true
            LEFT JOIN people ON people.customer_id=c.id
            ORDER BY active.latest_visit_at DESC NULLS LAST,c.name,c.id
            """,
            fde_user_ids,
            activity_since(as_of),
            as_of,
        )
        return [dict(row) for row in rows]
