# ruff: noqa: S608 -- Only a constant internal owner-scope SQL fragment is interpolated; all inputs are bound.
"""Only aggregate projections cross the rank boundary; SQL owns cohort authorization."""

from sales_backend.repositories.dashboard import owner_scope


async def ranking(connection, metric, start, end, *, months=None, personal=False):
    return await connection.fetchval(
        "SELECT security.business_ranking($1,$2,$3,$4::integer[],$5)",
        metric,
        start,
        end,
        months,
        personal,
    )


async def subject_ranking(connection, metric, start, end, *, months, member_id):
    return await connection.fetchval(
        "SELECT security.dashboard_subject_ranking($1,$2,$3,$4::integer[],$5::uuid)",
        metric, start, end, months, member_id,
    )


async def department_ranking_groups(connection, actor, metric, start, end, *, months, team_ids, legacy=False):
    """Public ranks use a scoped aggregate projection; active facts retain caller RLS.

    The public projection returns only statistics, including current-member counts
    and followup totals per person. It grants no access to customer/visit bodies.
    """
    if not await connection.fetchval("SELECT security.authorization_has('dashboard.ranking')"):
        raise PermissionError("当前账号未获查看排名授权")
    if metric not in {"followup", "opportunity_acv", "active_opportunities"}:
        raise ValueError("Unknown department ranking metric")
    if metric != "active_opportunities":
        return await connection.fetchval(
            "SELECT security.dashboard_team_ranking($1,$2,$3,$4::integer[],$5::uuid[],$6)",
            metric, start, end, months, list(team_ids) if team_ids is not None else None, legacy,
        )
    rows = await connection.fetch(
        """WITH members AS (
          SELECT u.id,primary_team.team_id FROM platform.user_ref u
          LEFT JOIN LATERAL (
            SELECT tm.team_id FROM platform.team_membership tm
            WHERE tm.workspace_id=u.workspace_id AND tm.user_ref_id=u.id
              AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
            ORDER BY tm.is_primary DESC,tm.valid_from DESC,tm.id LIMIT 1
          ) primary_team ON true
          WHERE u.workspace_id=$1::uuid AND u.status='active' AND u.deleted_at IS NULL
            AND EXISTS(SELECT 1 FROM platform.role_binding rb
              WHERE rb.workspace_id=u.workspace_id AND rb.user_ref_id=u.id
                AND rb.role_code IN ('sales','supervisor','manager','fde','fde_lead')
                AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to)
        ), events AS (
          SELECT COALESCE(v.recorder_team_id,m.team_id) AS team_id,1::numeric AS value,v.customer_id
          FROM activity.visit v JOIN members m ON m.id=v.recorder_user_ref_id
          WHERE $2='followup' AND v.workspace_id=$1::uuid AND v.deleted_at IS NULL
            AND v.status IN ('confirmed','archived')
            AND timezone('Asia/Shanghai',v.interaction_at)::date BETWEEN $3::date AND $4::date
            AND ($5::integer[] IS NULL OR
              extract(month FROM timezone('Asia/Shanghai',v.interaction_at))::integer=ANY($5::integer[]))
          UNION ALL
          SELECT o.owner_team_id,CASE WHEN $2='opportunity_acv' THEN o.amount ELSE 1 END,o.customer_id
          FROM crm.opportunity o JOIN members m ON m.id=o.owner_user_ref_id
          JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id AND c.deleted_at IS NULL
          WHERE $2 IN ('opportunity_acv','active_opportunities') AND o.workspace_id=$1::uuid
            AND o.deleted_at IS NULL AND o.status IN ('open','won')
            AND CASE WHEN $2='active_opportunities' THEN EXISTS(
              SELECT 1 FROM activity.visit v WHERE v.workspace_id=o.workspace_id AND v.opportunity_id=o.id
                AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
                AND timezone('Asia/Shanghai',v.interaction_at)::date BETWEEN $3::date AND $4::date
                AND ($5::integer[] IS NULL OR
                  extract(month FROM timezone('Asia/Shanghai',v.interaction_at))::integer=ANY($5::integer[])))
              ELSE o.status='open' AND o.expected_close_date BETWEEN $3::date AND $4::date
                AND ($5::integer[] IS NULL OR extract(month FROM o.expected_close_date)::integer=ANY($5::integer[])) END
        ), totals AS (
          SELECT 'team:'||t.id::text AS code,t.name,COALESCE(sum(e.value),0) AS value,
            count(e.team_id) AS record_count,count(DISTINCT e.customer_id) AS customer_count
          FROM platform.team t LEFT JOIN events e ON e.team_id=t.id
          WHERE t.workspace_id=$1::uuid AND t.id=ANY($6::uuid[])
            AND t.status='active' AND t.deleted_at IS NULL
            AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
            AND security.authorization_subject('dashboard.ranking','team',NULL,t.id)
          GROUP BY t.id,t.name
        ) SELECT *,rank() OVER(ORDER BY value DESC) AS rank FROM totals ORDER BY rank,code""",
        actor.workspace_id, metric, start, end, months, list(team_ids),
    )
    return [dict(row) for row in rows]


async def opportunity_distribution(connection, actor, *, year, months, personal, dimension):
    if dimension not in {"partner", "region"}:
        raise ValueError("Unknown distribution dimension")
    rows = await connection.fetch(
        f"""WITH scoped AS (
        SELECT o.customer_id,o.amount,
          CASE WHEN $7='partner' THEN CASE WHEN o.sales_channel='partner' AND o.partner_id IS NOT NULL
            THEN 'partner:'||o.partner_id::text ELSE 'unassigned' END
            ELSE 'region:'||COALESCE(NULLIF(o.attributes->>'region_code',''),
              NULLIF(t.region_code,''),t.name,'未分区域') END AS code,
          CASE WHEN $7='partner' THEN COALESCE(p.name,NULLIF(o.partner_name,''),'未关联伙伴')
            ELSE COALESCE(NULLIF(o.attributes->>'region_code',''),
              NULLIF(t.region_code,''),t.name,'未分区域') END AS name
        FROM crm.opportunity o LEFT JOIN crm.partner p ON p.id=o.partner_id AND p.workspace_id=o.workspace_id
          LEFT JOIN platform.team t ON t.id=o.owner_team_id AND t.workspace_id=o.workspace_id
        WHERE o.deleted_at IS NULL AND o.status='open' AND {owner_scope("o")}
          AND extract(year FROM o.expected_close_date)=$5::integer
          AND extract(month FROM o.expected_close_date)::integer=ANY($6::integer[])
          AND ($7<>'partner' OR o.sales_channel<>'direct')
        ), totals AS (
          SELECT code,CASE WHEN code='unassigned' THEN '未关联伙伴' ELSE max(name) END AS name,
            sum(amount) AS value,count(*) AS record_count,count(DISTINCT customer_id) AS customer_count
          FROM scoped GROUP BY code
        ), ranked AS (
          SELECT *,rank() OVER(ORDER BY value DESC) AS rank FROM totals WHERE code<>'unassigned'
          UNION ALL SELECT *,NULL::bigint AS rank FROM totals WHERE code='unassigned'
        ) SELECT * FROM ranked ORDER BY rank NULLS LAST,code""",
        "dashboard.read",
        actor.user_id,
        actor.workspace_id,
        personal,
        year,
        months,
        dimension,
    )
    return {"rows": [dict(row) for row in rows], "groups": []}
