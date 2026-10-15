"""Active authorized map points, with bounded fields and database ACV facts.

This read model intentionally excludes customer attributes, import metadata and
visit bodies. Customer scope is resolved before related facts are aggregated.
FDE participation selects customers; the facts then retain RLS-visible customer
context (including other visible opportunities for that customer).
"""

from __future__ import annotations

from datetime import date

from sales_backend.domain.activity_window import activity_window_bounds, map_activity_window_bounds
from sales_backend.repositories.customer_assets import today


def activity_since(as_of: date) -> date:
    """Backward-compatible start of the current business year."""
    return activity_window_bounds(as_of, "year").start


class CustomerMapRepository:
    async def read(self, connection, *, fde_user_ids=None, as_of=None, activity_window=None,
                   subject_member_ids=(), subject_scope=None, subject_team_ids=(), explicit_subject=False,
                   row_limit=None, offset=0, business_identity="all"):
        as_of = as_of or today()
        window = map_activity_window_bounds(as_of, activity_window)
        if row_limit is None and window.key in {"all", "quarter"}:
            row_limit = 200
        if row_limit is not None and (type(row_limit) is not int or not 1 <= row_limit <= 501):
            raise ValueError("地图读取条数超出范围")
        if type(offset) is not int or not 0 <= offset <= 2147483647:
            raise ValueError("地图读取偏移超出范围")
        rows = await connection.fetch(
            """
            WITH recent_visits AS MATERIALIZED (
              SELECT v.id,v.customer_id,v.opportunity_id,v.interaction_at FROM activity.visit v
              WHERE v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
                AND ($2::date IS NULL OR v.interaction_at >= ($2::date::timestamp AT TIME ZONE 'Asia/Shanghai'))
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
              SELECT c.id,c.workspace_id,c.name,c.industry_code,c.customer_type_code,c.business_identities,c.partner_profile_id,c.primary_partner_id,c.level_code,
                c.lifecycle_status,c.data_kind,c.owner_user_ref_id,c.owner_team_id,
                security.profile_customer_owner(c.id) AS claimant_id
              FROM crm.customer c
              LEFT JOIN active_customers active ON active.customer_id=c.id
              WHERE c.deleted_at IS NULL AND 'customer'=ANY(c.business_identities)
                AND ($11::text='all' OR $11=ANY(c.business_identities)) AND ($8::boolean OR active.customer_id IS NOT NULL)
                AND security.authorization_portfolio_customer('battle_map.read',c.id)
            ), scored_customers AS MATERIALIZED (
              -- Reuse the already-authorized claimant instead of re-reading
              -- crm.customer and profile_customer_owner for subject scores.
              -- This is the same membership predicate as subject_customers;
              -- it selects an assessment, never expands customer visibility.
              SELECT c.*,COALESCE(cardinality($4::uuid[])>0 AND (
                c.claimant_id=ANY($4::uuid[])
                OR ($5::text='team' AND c.owner_team_id=ANY($6::uuid[]))
                OR ($5::text='department' AND EXISTS (
                  SELECT 1 FROM platform.team_membership tm
                  WHERE tm.team_id=c.owner_team_id AND tm.user_ref_id=ANY($4::uuid[])
                    AND tm.workspace_id=c.workspace_id AND clock_timestamp()>=tm.valid_from
                    AND clock_timestamp()<tm.valid_to))),false) AS selects_subject
              FROM authorized c
              WHERE $1::uuid[] IS NULL OR c.claimant_id=ANY($1::uuid[])
                OR c.id IN (SELECT customer_id FROM scoped_opportunities)
            ), page_customers AS MATERIALIZED (
              -- Limit only after customer authorization and selected-member scope,
              -- and before expensive related-fact projection. NULL retains legacy reads.
              SELECT c.* FROM scored_customers c
              LEFT JOIN active_customers active ON active.customer_id=c.id
              ORDER BY active.latest_visit_at DESC NULLS LAST,c.name,c.id
              LIMIT $9::integer OFFSET $10::bigint
            )
            SELECT c.id::text,c.name,c.industry_code,c.customer_type_code,c.business_identities,c.partner_profile_id,c.primary_partner_id,c.level_code,
              c.lifecycle_status,c.data_kind,c.owner_user_ref_id::text,c.owner_team_id::text,
              u.display_name AS owner_name,t.name AS team_name,
              CASE WHEN u.id IS NULL THEN '[]'::jsonb ELSE
                jsonb_build_array(jsonb_build_object('id',u.id::text,'name',u.display_name)) END AS sales_members,
              q.potential_score,q.relationship_score,q.quadrant_code,
              q.assessment_id,q.subject_user_ref_id,q.quadrant_policy,
              active.latest_visit_at,COALESCE(active.weekly_follow_up_count,0) AS weekly_follow_up_count,
              risk.title AS risk_title,risk.severity_code AS risk_severity,
              opportunities.name AS opportunity_name,opportunities.stage_code AS opportunity_stage,
              opportunities.amount AS opportunity_amount,opportunities.amount AS acv_amount,
              opportunities.open_count,opportunities.unknown_amount_count,
              opportunities.plan_close_dates,opportunities.plan_close_periods,
              COALESCE(people.members,'[]'::jsonb) AS fde_members
            FROM page_customers c
            LEFT JOIN active_customers active ON active.customer_id=c.id
            LEFT JOIN platform.user_ref u ON u.id=c.claimant_id
            LEFT JOIN platform.team t ON t.id=c.owner_team_id
            -- Same latest visible score as v_customer_current_quadrant, without
            -- making the invoker view read/authorize the customer a second time.
            LEFT JOIN LATERAL (
              SELECT score.potential_score,score.relationship_score,score.quadrant_code,
                score.id::text AS assessment_id,score.subject_user_ref_id::text,
                score.input_snapshot->'company_policy' AS quadrant_policy
              FROM insight.quadrant_score score
              WHERE score.customer_id=c.id AND score.workspace_id=c.workspace_id
                AND score.valid_to='infinity'
                AND (NOT c.selects_subject OR NOT $7::boolean
                  OR score.subject_user_ref_id=ANY($4::uuid[]))
              -- Prefer a matching subject as the previous overlay did, even
              -- if a different subject has a newer score. Explicit personal
              -- views keep NULL when their own evidence is absent; customer
              -- and FDE views fall back to the latest visible score.
              ORDER BY CASE WHEN c.selects_subject AND score.subject_user_ref_id=ANY($4::uuid[])
                THEN 0 ELSE 1 END,score.calculated_at DESC,score.id DESC LIMIT 1
            ) q ON true
            LEFT JOIN LATERAL (
              SELECT r.title,r.severity_code FROM insight.risk r
              WHERE r.customer_id=c.id AND r.deleted_at IS NULL
                AND r.status <> 'resolved'
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
            window.start,
            window.end or as_of,
            list(subject_member_ids),
            subject_scope,
            list(subject_team_ids),
            explicit_subject,
            window.key == "all",
            row_limit,
            offset,
            business_identity,
        )
        return [dict(row) for row in rows]
