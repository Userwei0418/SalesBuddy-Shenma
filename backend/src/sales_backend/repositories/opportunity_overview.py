# ruff: noqa: S608 -- Only the internal owner-scope fragment is interpolated; inputs are bound.
"""Complete opportunity counts, independently scoped from the paginated list."""

from sales_backend.repositories.collaboration import scoped_opportunity_ids
from sales_backend.repositories.dashboard import owner_scope
from sales_backend.repositories.opportunity_business_date import business_created_on, creation_date_projection


async def opportunity_overview(connection, actor, *, year, quarters, scope=None, member_id=None, member_ids=None):
    scoped_ids = None
    if scope or member_id or member_ids:
        scope, _, _, scoped = await scoped_opportunity_ids(connection, actor, scope, member_id, member_ids, permission='opportunity.read')
        scoped_ids = [r["id"] for r in scoped]
    row = await connection.fetchrow(
        f"""WITH scoped AS (
          SELECT o.*,
            {creation_date_projection()} AS creation_date_fact,
            cardinality($6::integer[])=0 OR (
              extract(year FROM timezone('Asia/Shanghai',o.closed_at))=$5::integer AND
              extract(quarter FROM timezone('Asia/Shanghai',o.closed_at))::integer=ANY($6::integer[])) AS in_won_period
          FROM crm.opportunity o WHERE o.deleted_at IS NULL AND security.authorization_opportunity_direct('opportunity.read',o.id)
            AND (($7::uuid[] IS NOT NULL AND o.id=ANY($7::uuid[]))
              OR ($7::uuid[] IS NULL AND {owner_scope("o")}))
        ) SELECT (SELECT count(*) FROM crm.opportunity_demo_scenes d
            WHERE d.deleted_at IS NULL AND d.opportunity_id IN (SELECT id FROM scoped)
              AND (cardinality($6::integer[])=0 OR (
                extract(year FROM timezone('Asia/Shanghai',d.created_at))=$5::integer AND
                extract(quarter FROM timezone('Asia/Shanghai',d.created_at))::integer=ANY($6::integer[]))))
            AS demo_scene_count,
          count(*) FILTER(WHERE status='won' AND in_won_period) AS won,
          count(*) AS total,
          count(*) FILTER(WHERE status<>'lost' AND EXISTS(
            SELECT 1 FROM activity.visit v WHERE (v.opportunity_id=scoped.id OR
              (v.opportunity_id IS NULL AND EXISTS(SELECT 1 FROM activity.visit_opportunity vo
                WHERE vo.visit_id=v.id AND vo.opportunity_id=scoped.id))) AND v.deleted_at IS NULL
              AND v.status IN ('confirmed','archived') AND (cardinality($6::integer[])=0 OR (
                extract(year FROM timezone('Asia/Shanghai',v.interaction_at))=$5::integer AND
                extract(quarter FROM timezone('Asia/Shanghai',v.interaction_at))::integer=ANY($6::integer[])))
          )) AS active,
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
        "definition_version": "opportunity_overview_v3",
    }
