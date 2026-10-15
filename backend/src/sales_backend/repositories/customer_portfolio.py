"""One statement/snapshot supplies exact customer summary, page and complete map."""

from datetime import date

from sales_backend.db import json_value
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.repositories.customer_assets import today
from sales_backend.repositories.team_directory import require_team

SQL = """

      WITH input AS (SELECT $1::jsonb q),
      subjects AS (SELECT value::uuid id FROM input,jsonb_array_elements_text(q->'subject_user_ids')),
      readable_opportunities AS MATERIALIZED (
       SELECT o.id,o.workspace_id,o.customer_id,o.status,o.probability,o.amount,
         COALESCE(extract(year FROM o.expected_close_date)::int,o.expected_close_year) close_year
       FROM crm.opportunity o WHERE o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL
      ),
      customers AS MATERIALIZED (
       SELECT c.id,c.workspace_id,c.name,c.level_code,c.owner_team_id,
         c.business_identities,c.partner_profile_id,c.primary_partner_id,c.customer_type_code,
         own.owner_user_ref_id,own.state AS ownership_state,u.display_name owner_name,t.name team_name,
         COALESCE(own.state='claimed',false) claimed,
         CASE WHEN own.state='claimed' AND (q->>'activity')::boolean
           THEN security.authorization_portfolio_customer('visit.read',c.id) ELSE false END
      activity_scope_covered,
         CASE WHEN own.state='claimed' AND (q->>'opportunity')::boolean
           THEN security.authorization_portfolio_customer('opportunity.read',c.id) ELSE false END
      opportunity_scope_covered,
         CASE WHEN own.state='claimed' AND (q->>'map')::boolean
           THEN security.authorization_portfolio_customer('battle_map.read',c.id) ELSE false END
      map_readable
       FROM crm.customer c CROSS JOIN input
       LEFT JOIN crm.customer_ownership own ON own.customer_id=c.id AND own.workspace_id=c.workspace_id
       LEFT JOIN platform.user_ref u ON u.id=own.owner_user_ref_id AND u.workspace_id=c.workspace_id
       LEFT JOIN platform.team t ON t.id=c.owner_team_id AND t.workspace_id=c.workspace_id
       WHERE 'customer'=ANY(c.business_identities) AND (q->>'business_identity'='all' OR (q->>'business_identity')=ANY(c.business_identities))
         AND own.state='claimed' AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
         AND (q->>'q' IS NULL OR c.name ILIKE '%'||(q->>'q')||'%'
           OR u.display_name ILIKE '%'||(q->>'q')||'%')
         AND (q->>'level' IS NULL OR c.level_code=q->>'level')
         AND (NOT (q->>'subject_filter')::boolean
           OR own.owner_user_ref_id IN (SELECT id FROM subjects)
           OR (q->>'member_id' IS NULL AND q->>'scope'='team' AND c.owner_team_id=(q->>'team_id')::uuid)
           OR EXISTS(SELECT 1 FROM crm.customer_sales_member m WHERE m.customer_id=c.id AND
      m.workspace_id=c.workspace_id
             AND m.user_ref_id IN (SELECT id FROM subjects))
           OR EXISTS(SELECT 1 FROM readable_opportunities o JOIN crm.opportunity_participant p
             ON p.opportunity_id=o.id AND p.workspace_id=o.workspace_id
             WHERE o.customer_id=c.id AND o.workspace_id=c.workspace_id
               AND p.user_ref_id IN (SELECT id FROM subjects) AND p.participant_role='fde'
               AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
               AND security.fde_user_is_active(p.user_ref_id)))
      ), opportunities AS MATERIALIZED (
       SELECT o.id,o.customer_id,o.status,o.probability,o.amount,o.close_year
       FROM readable_opportunities o JOIN customers c ON c.id=o.customer_id AND
      c.workspace_id=o.workspace_id
      ), opportunity_facts AS MATERIALIZED (
       SELECT o.customer_id,count(*) FILTER(WHERE o.status='open')::int open_count,
         max(o.probability) FILTER(WHERE o.status='open') max_open_probability,
         bool_or(o.status='won') has_won,
         count(*) FILTER(WHERE o.status='open' AND (o.amount IS NULL OR o.amount<0))::int
      unknown_amount_count,
         CASE WHEN count(*) FILTER(WHERE o.status='open' AND (o.amount IS NULL OR o.amount<0))>0 THEN NULL
           ELSE COALESCE(sum(o.amount) FILTER(WHERE o.status='open'),0) END amount,
         bool_or(o.status='open' AND o.close_year=(q->>'year')::int) current_year,
         bool_or(o.status='open' AND o.close_year>(q->>'year')::int) future_year
       FROM opportunities o CROSS JOIN input GROUP BY o.customer_id
      ), formal_visits AS MATERIALIZED (
       SELECT v.id,v.workspace_id,v.customer_id,v.opportunity_id,v.interaction_at
       FROM activity.visit v CROSS JOIN input
       WHERE (q->>'activity')::boolean AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
         AND v.interaction_at < (((q->>'as_of')::date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
      ), visit_links AS MATERIALIZED (
       SELECT v.id,v.customer_id,v.interaction_at FROM formal_visits v WHERE v.customer_id IS NOT NULL
       UNION
       SELECT v.id,o.customer_id,v.interaction_at FROM formal_visits v JOIN readable_opportunities o
         ON o.id=v.opportunity_id AND o.workspace_id=v.workspace_id
       UNION
       SELECT v.id,o.customer_id,v.interaction_at FROM formal_visits v
       JOIN LATERAL (
         SELECT vo.opportunity_id FROM activity.visit_opportunity vo
         WHERE vo.visit_id=v.id AND vo.workspace_id=v.workspace_id OFFSET 0
       ) vo ON true JOIN readable_opportunities o
         ON o.id=vo.opportunity_id AND o.workspace_id=v.workspace_id
      ), activity AS MATERIALIZED (
       SELECT v.customer_id,max(v.interaction_at) latest_visit_at,
         bool_or(v.interaction_at >= ((q->>'activity_start')::date::timestamp AT TIME ZONE
      'Asia/Shanghai')
           AND v.interaction_at < (((q->>'activity_end')::date+1)::timestamp AT TIME ZONE
      'Asia/Shanghai')) is_active
       FROM visit_links v JOIN customers c ON c.id=v.customer_id CROSS JOIN input
       GROUP BY v.customer_id
      ) , resource_availability AS MATERIALIZED (
       SELECT c.*,
         (c.activity_scope_covered OR EXISTS(SELECT 1 FROM activity a WHERE a.customer_id=c.id))
      activity_readable,
         (c.opportunity_scope_covered OR EXISTS(SELECT 1 FROM opportunity_facts o WHERE
      o.customer_id=c.id)) opportunity_readable
       FROM customers c
      ), facts AS MATERIALIZED (
       SELECT c.*,CASE WHEN c.activity_readable THEN COALESCE(a.is_active,false) END is_active,
         CASE WHEN c.activity_readable THEN a.latest_visit_at END latest_visit_at,
         CASE WHEN c.opportunity_readable THEN COALESCE(o.open_count,0) END open_count,
         CASE WHEN c.opportunity_readable THEN o.max_open_probability END max_open_probability,
         CASE WHEN c.opportunity_readable THEN COALESCE(o.has_won,false) END has_won,
         CASE WHEN c.opportunity_readable THEN CASE WHEN o.customer_id IS NULL THEN 0 ELSE o.amount END
      END amount,
         CASE WHEN c.opportunity_readable THEN COALESCE(o.unknown_amount_count,0) END
      unknown_amount_count,
         CASE WHEN c.opportunity_readable THEN CASE WHEN o.current_year THEN 'current_year'
           WHEN o.future_year THEN 'long_term' END END plan_segment,
         score.potential_score,score.relationship_score,score.quadrant_code
       FROM resource_availability c CROSS JOIN input LEFT JOIN opportunity_facts o ON o.customer_id=c.id
       LEFT JOIN activity a ON a.customer_id=c.id
       LEFT JOIN LATERAL (
         SELECT s.potential_score,s.relationship_score,s.quadrant_code
         FROM insight.quadrant_score s WHERE c.map_readable AND s.customer_id=c.id AND
      s.workspace_id=c.workspace_id
           AND s.valid_to='infinity' AND (q->>'score_subject' IS NULL OR
      s.subject_user_ref_id=(q->>'score_subject')::uuid)
         ORDER BY s.calculated_at DESC,s.id DESC LIMIT 1
       ) score ON true
       WHERE c.claimed
      ), common_set AS MATERIALIZED (
       SELECT f.*,f.amount acv_amount,true can_open,
         (f.potential_score BETWEEN 0 AND 100 AND f.relationship_score BETWEEN 0 AND 100) IS TRUE scored
       FROM facts f CROSS JOIN input
       WHERE (q->>'quadrant' IS NULL OR f.map_readable AND f.quadrant_code=q->>'quadrant_code')
         AND (NOT (q->>'unscored')::boolean OR f.map_readable AND (f.potential_score IS NULL OR
      f.relationship_score IS NULL
           OR f.potential_score NOT BETWEEN 0 AND 100 OR f.relationship_score NOT BETWEEN 0 AND 100))
         AND (q->>'plan' IS NULL OR f.opportunity_readable AND f.plan_segment=q->>'plan')
         AND (q->>'amount_min' IS NULL OR f.opportunity_readable AND
      f.amount>=(q->>'amount_min')::numeric)
         AND (q->>'amount_max' IS NULL OR f.opportunity_readable AND
      f.amount<=(q->>'amount_max')::numeric)
      ), selected AS MATERIALIZED (
       SELECT c.* FROM common_set c CROSS JOIN input WHERE c.claimed AND
         (q->>'segment'='claimed' OR q->>'segment'='active' AND c.is_active IS TRUE
          OR q->>'segment'='pipeline' AND c.max_open_probability>=(q->>'min_probability')::int
          OR q->>'segment'='won' AND c.has_won IS TRUE)
      ), page AS (
       SELECT * FROM selected ORDER BY latest_visit_at DESC NULLS LAST,name,id
       LIMIT (SELECT (q->>'limit')::int FROM input) OFFSET (SELECT (q->>'offset')::int FROM input)
      ), summary AS (
       SELECT count(id)::int claimed,
        CASE WHEN (q->>'activity')::boolean THEN count(*) FILTER(WHERE claimed AND is_active)::int END
      active,
        CASE WHEN (q->>'opportunity')::boolean THEN count(*) FILTER(WHERE claimed AND
      max_open_probability>=10)::int END pipeline_ge_10,
        CASE WHEN (q->>'opportunity')::boolean THEN count(*) FILTER(WHERE claimed AND
      max_open_probability>=30)::int END pipeline_ge_30,
        CASE WHEN (q->>'opportunity')::boolean THEN count(*) FILTER(WHERE claimed AND
      max_open_probability>=50)::int END pipeline_ge_50,
        CASE WHEN (q->>'opportunity')::boolean THEN count(*) FILTER(WHERE claimed AND
      max_open_probability>=70)::int END pipeline_ge_70,
        CASE WHEN (q->>'opportunity')::boolean THEN count(*) FILTER(WHERE claimed AND
      max_open_probability>=90)::int END pipeline_ge_90,
        CASE WHEN (q->>'opportunity')::boolean THEN count(*) FILTER(WHERE claimed AND has_won)::int END
      won,
        count(*) FILTER(WHERE claimed AND NOT activity_readable)::int activity_unavailable_count,
        count(*) FILTER(WHERE claimed AND NOT opportunity_readable)::int opportunity_unavailable_count
       FROM common_set RIGHT JOIN input ON true GROUP BY q
      ), pool AS (
       SELECT CASE WHEN (q->>'pool')::boolean THEN
        (security.company_customer_directory_search(NULL,1,0,NULL,NULL,'{}'::text[],
          CASE WHEN q->>'business_identity'='partner' THEN 'customer_partner' ELSE 'customer' END)->>'total')::int END
      pool_total FROM input
      )
      SELECT to_jsonb(summary)||jsonb_build_object('pool_total',pool.pool_total) summary,
       COALESCE((SELECT jsonb_agg(to_jsonb(p) ORDER BY p.latest_visit_at DESC NULLS LAST,p.name,p.id) FROM
      page p),'[]'::jsonb) items,
       COALESCE((SELECT jsonb_agg(to_jsonb(s) ORDER BY s.name,s.id) FROM selected s WHERE s.map_readable
      AND s.scored),'[]'::jsonb) map_points,
       (SELECT count(*)::int FROM selected) total,
       CASE WHEN (q->>'map')::boolean THEN (SELECT count(*)::int FROM selected WHERE map_readable AND NOT
      scored) END unscored_count,
       (SELECT count(*)::int FROM selected WHERE NOT map_readable) map_unavailable_count
      FROM summary CROSS JOIN pool CROSS JOIN input
      """


class CustomerPortfolioRepository:
    async def read(self, connection, actor, query, *, as_of=None):
        as_of = as_of or today()
        effective = await AuthorizationRepository().effective(connection)
        effective.require("customer.read")
        availability = {
            key: effective.allows(permission)
            for key, permission in {
                "activity": "visit.read",
                "opportunity": "opportunity.read",
                "map": "battle_map.read",
                "pool": "customer.claim_directory",
            }.items()
        }
        required = set()
        if query.segment == "active":
            required.add("activity")
        if (
            query.segment in {"pipeline", "won"}
            or query.plan
            or query.amount_min is not None
            or query.amount_max is not None
        ):
            required.add("opportunity")
        if query.quadrant or query.unscored:
            required.add("map")
        if any(not availability[key] for key in required):
            raise PermissionError("当前账号无权查看所选客户分层或筛选事实")
        selected = query.member_id or (actor.user_id if query.scope == "self" else None)
        if query.team_id:
            await require_team(connection, actor, query.team_id, permission="customer.read")
        if selected:
            permitted = await connection.fetchval(
                """
                SELECT security.authorization_subject('customer.read','person',$1::uuid,NULL)
                              AND ($2::uuid IS NULL OR EXISTS(SELECT 1 FROM platform.team_membership tm
                                WHERE tm.user_ref_id=$1::uuid AND tm.team_id=$2::uuid AND
                tm.workspace_id=common.current_workspace_id()
                                 AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to))
                """,
                str(selected),
                query.team_id,
            )
            if not permitted:
                raise PermissionError("所选成员已失效或不在当前授权范围及团队内")
        subjects = [str(selected)] if selected else []
        if query.team_id and not selected:
            subjects = [
                str(r["id"])
                for r in await connection.fetch(
                    """
            SELECT DISTINCT u.id FROM platform.user_ref u
                          JOIN platform.team_membership tm ON tm.user_ref_id=u.id AND
            tm.workspace_id=u.workspace_id
                          WHERE u.workspace_id=common.current_workspace_id() AND u.status='active' AND
            u.deleted_at IS NULL
                            AND tm.team_id=$1::uuid AND clock_timestamp()>=tm.valid_from AND
            clock_timestamp()<tm.valid_to
            """,
                    query.team_id,
                )
            ]
        # Subject selection must not silently lose formal FDE relationships when
        # opportunity.read is denied/narrower. Prove coverage from the same grants
        # and active memberships used by resource RLS, or reject the selection.
        if subjects:
            fde_subjects = await connection.fetch(
                """
                SELECT u.id FROM platform.user_ref u
                              WHERE u.workspace_id=common.current_workspace_id() AND u.id=ANY($1::uuid[])
                                AND security.fde_user_is_active(u.id)
                """,
                subjects,
            )
            grants = effective.for_permission("opportunity.read")
            workspace_covered = any(g.scope == "workspace" for g in grants)
            assigned = any(g.scope == "assigned" for g in grants)
            teams = sorted({team for g in grants if g.scope == "teams" for team in g.team_ids})
            for subject in fde_subjects:
                if workspace_covered or assigned and str(subject["id"]) == str(actor.user_id):
                    continue
                covered = await connection.fetchval(
                    """
            SELECT EXISTS(
                              SELECT 1 FROM platform.team_membership tm JOIN platform.team t ON t.id=tm.team_id
                              WHERE tm.workspace_id=common.current_workspace_id() AND tm.user_ref_id=$1
                                AND tm.team_id=ANY($2::uuid[]) AND t.status='active' AND t.deleted_at IS NULL
                                AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
                                AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to)
            """,
                    subject["id"],
                    teams,
                )
                if not covered:
                    raise PermissionError("所选FDE关联范围缺少完整商机读取授权，无法准确筛选客户")
        params = query.model_dump(mode="json")
        params["activity_start"] = (query.activity_start or date(as_of.year, 1, 1)).isoformat()
        params["activity_end"] = min(query.activity_end or as_of, as_of).isoformat()
        if params["activity_start"] > params["activity_end"]:
            raise ValueError("活动开始日期不得晚于结束日期")
        params["quadrant_code"] = {
            "attack": "main_attack",
            "asset": "customer_asset",
            "spot": "order_driven",
            "resource": "customer_resource",
        }.get(query.quadrant)
        params.update(
            availability,
            year=as_of.year,
            as_of=as_of.isoformat(),
            subject_user_ids=subjects,
            subject_filter=bool(selected or query.team_id),
            score_subject=str(selected) if selected and actor.role.value not in {"fde", "fde_lead"} else None,
        )
        row = dict(await connection.fetchrow(SQL, params))
        result = {
            key: json_value(value) if key in {"summary", "items", "map_points"} else value for key, value in row.items()
        }
        result.update(
            as_of=as_of,
            availability=availability,
            offset=query.offset,
            limit=query.limit,
            has_more=query.offset + len(result["items"]) < result["total"],
            filters_applied={
                **query.model_dump(mode="json"),
                "activity_start": params["activity_start"],
                "activity_end": params["activity_end"],
                "pool_total_scope": "authorized_customer_directory",
                "identity_base": "customer",
            },
        )
        return result
