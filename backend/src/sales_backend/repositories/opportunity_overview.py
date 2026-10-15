# ruff: noqa: S608 -- Only the internal owner-scope fragment is interpolated; inputs are bound.
"""Complete opportunity counts, independently scoped from the paginated list."""

from datetime import datetime
from zoneinfo import ZoneInfo

from sales_backend.domain.activity_window import activity_window_bounds
from sales_backend.repositories.collaboration import scoped_opportunity_ids
from sales_backend.repositories.dashboard import owner_scope
from sales_backend.repositories.opportunity_business_date import business_created_on, creation_date_projection


async def opportunity_overview(
    connection, actor, *, year, quarters, scope=None, member_id=None, member_ids=None, team_id=None,
    owner_id=None, activity_window="all"
):
    scoped_ids = None
    if scope or member_id or member_ids or team_id:
        scope, _, _, scoped = await scoped_opportunity_ids(
            connection, actor, scope, member_id, member_ids, team_id=team_id, permission='opportunity.read'
        )
        scoped_ids = [r["id"] for r in scoped]
    activity_start = activity_end = None
    if activity_window != "all":
        bounds = activity_window_bounds(datetime.now(ZoneInfo("Asia/Shanghai")).date(), activity_window)
        activity_start, activity_end = bounds.start, bounds.end
    row = await connection.fetchrow(
        f"""WITH scoped AS (
          SELECT o.*,
            {creation_date_projection()} AS creation_date_fact,
            cardinality($6::integer[])=0 OR (
              extract(year FROM timezone('Asia/Shanghai',o.closed_at))=$5::integer AND
              extract(quarter FROM timezone('Asia/Shanghai',o.closed_at))::integer=ANY($6::integer[])) AS in_won_period
          FROM crm.opportunity o WHERE o.deleted_at IS NULL AND security.authorization_opportunity_direct('opportunity.read',o.id)
            AND (($10::uuid[] IS NOT NULL AND o.id=ANY($10::uuid[]))
              OR ($10::uuid[] IS NULL AND {owner_scope("o")}))
            AND ($9::uuid IS NULL OR o.owner_user_ref_id=$9::uuid)
            AND ($7::date IS NULL OR EXISTS(
              SELECT 1 FROM activity.visit av WHERE (av.opportunity_id=o.id OR
                (av.opportunity_id IS NULL AND EXISTS(SELECT 1 FROM activity.visit_opportunity avo
                  WHERE avo.visit_id=av.id AND avo.opportunity_id=o.id)))
                AND av.deleted_at IS NULL AND av.status IN ('confirmed','archived')
                AND av.interaction_at >= ($7::timestamp AT TIME ZONE 'Asia/Shanghai')
                AND av.interaction_at < (($8::date + 1)::timestamp AT TIME ZONE 'Asia/Shanghai')))
        ), visit_facts AS (
          SELECT DISTINCT x.opportunity_id
          FROM (
            SELECT av.opportunity_id, av.interaction_at
            FROM activity.visit av
            WHERE av.workspace_id=$3::uuid AND av.opportunity_id IS NOT NULL
              AND av.deleted_at IS NULL AND av.status IN ('confirmed','archived')
            UNION ALL
            SELECT avo.opportunity_id, av.interaction_at
            FROM activity.visit av
            JOIN activity.visit_opportunity avo ON avo.visit_id=av.id
            WHERE av.workspace_id=$3::uuid AND avo.workspace_id=$3::uuid AND av.opportunity_id IS NULL
              AND av.deleted_at IS NULL AND av.status IN ('confirmed','archived')
          ) x
          WHERE (cardinality($6::integer[])=0 OR (
            extract(year FROM timezone('Asia/Shanghai',x.interaction_at))=$5::integer AND
            extract(quarter FROM timezone('Asia/Shanghai',x.interaction_at))::integer=ANY($6::integer[])))
        ) SELECT (SELECT count(*) FROM crm.opportunity_demo_scenes d
            WHERE d.deleted_at IS NULL AND d.opportunity_id IN (SELECT id FROM scoped)
              AND (cardinality($6::integer[])=0 OR (
                extract(year FROM timezone('Asia/Shanghai',d.created_at))=$5::integer AND
                extract(quarter FROM timezone('Asia/Shanghai',d.created_at))::integer=ANY($6::integer[]))))
            AS demo_scene_count,
          count(*) FILTER(WHERE status='won' AND in_won_period) AS won,
          count(*) AS total,
          count(*) FILTER(WHERE status<>'lost' AND scoped.id IN (SELECT opportunity_id FROM visit_facts)) AS active,
          COALESCE(jsonb_agg(creation_date_fact),'[]'::jsonb) AS creation_date_facts,
          count(*) FILTER(WHERE cardinality($6::integer[])>0 
            AND status<>'lost' AND expected_close_date IS NULL AND expected_close_year IS NULL) AS "missingCloseDates",
          count(*) FILTER(WHERE cardinality($6::integer[])>0 
            AND status='won' AND closed_at IS NULL) AS "missingWonDates"
          FROM scoped""",
        "opportunity.read",
        actor.user_id,
        actor.workspace_id,
        False,
        year,
        quarters,
        activity_start,
        activity_end,
        owner_id,
        scoped_ids,
    )
    metrics = dict(row)
    creation_dates = [business_created_on(fact) for fact in metrics.pop("creation_date_facts")]
    metrics["newCount"] = sum(
        day is not None and (not quarters or (day.year == year and (day.month - 1) // 3 + 1 in quarters))
        for day in creation_dates
    )
    metrics["missingCreatedDates"] = sum(day is None for day in creation_dates)
    return {
        "metrics": metrics,
        "year": year,
        "quarters": sorted(set(quarters)),
        "scope": scope or "authorized",
        "activity_window": activity_window,
        "definition_version": "opportunity_overview_v4" if activity_window != "all" else "opportunity_overview_v3",
    }
