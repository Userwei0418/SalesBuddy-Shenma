# ruff: noqa: S608 -- Query fragments are internal; all external values are bound.
"""Paged opportunity browsing; filters and totals share one authorized SQL set.

Only page IDs are hydrated with attributes, members, forecasts and actuals. Date
facets parse a minimal provenance projection; business bodies stay page-scoped.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sales_backend.domain.activity_window import activity_window_bounds
from sales_backend.repositories.collaboration import scope_members
from sales_backend.repositories.follow_up_recency import opportunity_recency_ctes
from sales_backend.repositories.dashboard_scope import dashboard_members
from sales_backend.repositories.opportunity_product_lines import PRODUCT_LINES_SQL, PRODUCT_LINE_DISPLAY_SQL
from sales_backend.repositories.opportunity_business_date import business_created_on, creation_date_projection
from sales_backend.repositories.team_directory import (
    attach_member_teams,
    default_team_id,
    require_team,
    selectable_teams,
)

STAGE_SQL = """CASE WHEN o.status IN ('won','lost') THEN o.status
    WHEN o.probability=10 THEN 'identified' WHEN o.probability=30 THEN 'qualified'
    WHEN o.probability=50 THEN 'solution' WHEN o.probability=70 THEN 'proposal'
    WHEN o.probability=90 THEN 'negotiation' ELSE o.stage_code END"""


async def browse_opportunities(
    connection,
    actor,
    *,
    limit,
    offset=0,
    purpose="list",
    customer_id=None,
    owner_name=None,
    owner_id=None,
    probability=None,
    stage_code=None,
    close_from=None,
    close_to=None,
    include_closed=False,
    trade_relation=None,
    exclude_customer_id=None,
    exclude_opportunity_id=None,
    scope=None,
    member_id=None,
    member_ids=None,
    query=None,
    stages=None,
    team=None,
    team_id=None,
    grade=None,
    product_line=None,
    product_line_id=None,
    year=None,
    quarters=None,
    close_period=None,
    order="close_date",
    created_year=None,
    created_quarter=None,
    active_quarter=False,
    financial_year=None,
    financial_quarter=None,
    include_overview=False,
    overview_year=None,
    overview_quarters=None,
    activity_window=None,
):
    if purpose not in ("list", "upstream"):
        raise ValueError("请选择有效的商机查询用途")
    if purpose == "upstream":
        if not exclude_customer_id:
            raise ValueError("上游候选查询必须排除当前客户")
        if customer_id is not None or trade_relation not in (None, "distributor"):
            raise ValueError("上游候选仅支持不同客户的总代商机")
        trade_relation = "distributor"
    overview_quarters = sorted(set(overview_quarters or []))
    if include_overview and (
        overview_year is None or not 2000 <= overview_year <= 2100
        or any(quarter not in {1, 2, 3, 4} for quarter in overview_quarters)
    ):
        raise ValueError("请选择有效的统计年份和季度")
    if ((created_year is None) != (created_quarter is None)
            or (created_year is not None and (not 2000 <= created_year <= 2100 or created_quarter not in {1, 2, 3, 4}))):
        raise ValueError("请选择完整的创建年份和季度")
    if active_quarter and (financial_year is None or not 2000 <= financial_year <= 2100
                           or financial_quarter not in {1, 2, 3, 4}):
        raise ValueError("请选择有效的财务年份和季度")
    from sales_backend.services.sales_opportunity_visibility import require_read_feature

    permissions = await require_read_feature(connection) if actor is not None else None
    original_read = actor is not None and permissions.allows('opportunity.read')
    team_options = await selectable_teams(connection, actor, permission='opportunity.read', permissions=permissions) if original_read else []
    owner_options = await dashboard_members(connection, actor, permission='opportunity.read') if original_read else []
    owner_options = await attach_member_teams(connection, actor, owner_options, team_options) if actor is not None else []
    primary_team_id = await default_team_id(connection, actor, team_options) if actor is not None else None
    if team_id is not None:
        selected = await require_team(connection, actor, team_id, permission='opportunity.read')
        if team and team != selected['name']:
            raise ValueError('团队ID与名称不一致，请刷新团队目录')
    args = []

    def bind(value, cast):
        args.append(value)
        return f"${len(args)}::{cast}"

    base = ["o.deleted_at IS NULL"]
    if trade_relation:
        base.append(f"o.trade_relation={bind(trade_relation,'text')}")
    if exclude_customer_id:
        base.append(f"o.customer_id<>{bind(str(exclude_customer_id),'uuid')}")
    if exclude_opportunity_id:
        base.append(f"o.id<>{bind(str(exclude_opportunity_id),'uuid')}")
    scope_cte = ""
    opportunity_source = "crm.opportunity o"
    if not include_closed:
        base.append("o.status='open'")
    if actor:
        # Project lists use the target's own business scope. Customer panoramas
        # and explicit upstream selection retain ordinary read visibility.
        # Trade/exclusion filters alone never opt a list into panorama scope.
        permission = "authorization_opportunity_direct" if purpose == "list" and not customer_id else "authorization_opportunity"
        base.append(f"security.{permission}('opportunity.read',o.id)")
        people_scope = None if scope == "authorized" else scope
        if people_scope or member_id or member_ids:
            _, people, _ = await scope_members(connection, actor, people_scope, member_id, member_ids, permission='opportunity.read')
            base.append(f"(o.owner_user_ref_id=ANY({bind(people, 'uuid[]')}) OR EXISTS(SELECT 1 FROM crm.opportunity_participant p "
                        f"WHERE p.opportunity_id=o.id AND p.user_ref_id=ANY({bind(people, 'uuid[]')}) "
                        "AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to))")
    if customer_id:
        base.append(f"o.customer_id={bind(customer_id, 'uuid')}")
    # The activity selector is a server-side filter so page IDs, totals,
    # facets and the optional overview all use one identical authorized set.
    # ``all`` keeps the old API behaviour for callers that have not opted in;
    # product clients send ``today`` explicitly as their default.
    if activity_window and activity_window != "all":
        bounds = activity_window_bounds(datetime.now(ZoneInfo("Asia/Shanghai")).date(), activity_window)
        start_arg, end_arg = bind(bounds.start, "date"), bind(bounds.end, "date")
        base.append(
            f"EXISTS (SELECT 1 FROM activity.visit v WHERE "
            f"(v.opportunity_id=o.id OR (v.opportunity_id IS NULL AND EXISTS "
            f"(SELECT 1 FROM activity.visit_opportunity vo WHERE vo.visit_id=v.id AND vo.opportunity_id=o.id))) "
            f"AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived') "
            f"AND v.interaction_at >= ({start_arg}::timestamp AT TIME ZONE 'Asia/Shanghai') "
            f"AND v.interaction_at < (({end_arg}::date + 1)::timestamp AT TIME ZONE 'Asia/Shanghai'))"
        )

    if active_quarter:
        base.append(
            "EXISTS (SELECT 1 FROM crm.opportunity_forecast forecast WHERE forecast.opportunity_id=o.id "
            f"AND forecast.year={bind(financial_year, 'integer')} "
            f"AND forecast.quarter={bind(financial_quarter, 'integer')} "
            "AND (forecast.recognized_amount>0 OR forecast.collection_amount>0))"
        )
    if created_year is not None:
        # Reuse the same provenance parser as the typed Web workspace. Read only
        # IDs/date facts in the identical authorized scope, then filter SQL before
        # page IDs and every summary; import timestamps never stand in for dates.
        date_rows = await connection.fetch(
            "SELECT o.id::text," + creation_date_projection() + " AS fact FROM "
            + opportunity_source + " WHERE " + " AND ".join(base), *args
        )
        matching = [row["id"] for row in date_rows
                    if (day := business_created_on(row["fact"])) is not None
                    and day.year == created_year and (day.month - 1) // 3 + 1 == created_quarter]
        base.append(f"o.id=ANY({bind(matching, 'uuid[]')})")

    filters = []
    if owner_id is not None:
        filters.append(f"owner_id={bind(str(owner_id), 'uuid')}")
    elif owner_name:
        filters.append(f"owner_name={bind(owner_name, 'text')}")
    team_predicate = (f"team_id={bind(str(team_id), 'uuid')}" if team_id is not None
                      else f"team_name={bind(team, 'text')}" if team else "true")
    if team or team_id is not None:
        filters.append(team_predicate)
    if probability is not None:
        filters.append(f"probability={bind(probability, 'integer')}")
    if stage_code:
        filters.append(f"stage_code={bind(stage_code, 'text')}")
    if stages:
        filters.append(f"display_stage=ANY({bind(stages, 'text[]')})")
    if query:
        # strpos keeps search literal, so '%' and '_' are not wildcard input.
        filters.append(f"strpos(lower(concat_ws(' ',name,customer_name,product_line)),lower({bind(query, 'text')}))>0")
    if product_line:
        value = bind(product_line, 'text')
        filters.append(f"(EXISTS(SELECT 1 FROM jsonb_array_elements(product_lines) p WHERE p->>'name'={value})"
                       f" OR (product_lines='[]'::jsonb AND product_line={value}))")
    if product_line_id:
        filters.append(f"product_lines @> jsonb_build_array(jsonb_build_object('id',{bind(str(product_line_id), 'text')}))")
    if grade:
        from sales_backend.domain.business_options import OPPORTUNITY_GRADES
        selected_grade = next(item for item in OPPORTUNITY_GRADES if item["code"] == grade)
        low, high = selected_grade["min"], selected_grade["max"]
        filters.append(f"amount>={bind(low, 'numeric')}")
        if high is not None:
            filters.append(f"amount<{bind(high, 'numeric')}")
    if close_from:
        filters.append(f"expected_close_date>={bind(close_from, 'date')}")
    if close_to:
        filters.append(f"expected_close_date<={bind(close_to, 'date')}")
    if quarters:
        ranges = []
        for quarter in sorted(set(quarters)):
            start = date(year, (quarter - 1) * 3 + 1, 1)
            end = date(year + (quarter == 4), quarter * 3 % 12 + 1, 1)
            ranges.append(f"((expected_close_date>={bind(start, 'date')} AND expected_close_date<{bind(end, 'date')})"
                          f" OR (expected_close_date IS NULL AND expected_close_year={bind(year, 'integer')}"
                          f" AND expected_close_quarter={bind(quarter, 'integer')}))")
        filters.append("(" + " OR ".join(ranges) + ")")
    if close_period and close_period != "all":
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        month = (
            1
            if close_period == "year"
            else ((today.month - 1) // 3 * 3 + 1 if close_period == "quarter" else today.month)
        )
        width = {"year": 12, "quarter": 3, "month": 1}[close_period]
        end_month = month - 1 + width
        start, end = date(today.year, month, 1), date(today.year + end_month // 12, end_month % 12 + 1, 1)
        dated = f"expected_close_date>={bind(start, 'date')} AND expected_close_date<{bind(end, 'date')}"
        if close_period in {"year", "quarter"}:
            period = f"expected_close_year={bind(today.year, 'integer')}"
            if close_period == "quarter":
                period += f" AND expected_close_quarter={bind((today.month - 1) // 3 + 1, 'integer')}"
            filters.append(f"(({dated}) OR (expected_close_date IS NULL AND {period}))")
        else:
            # Quarter precision cannot imply a particular month or day.
            filters.append(dated)
    limit_arg, offset_arg = bind(limit, "integer"), bind(offset, "integer")
    ordering = (
        "CASE WHEN status='open' THEN 0 ELSE 1 END,"
        "COALESCE(extract(year FROM expected_close_date)::integer,expected_close_year) NULLS LAST,"
        "COALESCE(extract(quarter FROM expected_close_date)::integer,expected_close_quarter) NULLS LAST,"
        "probability DESC NULLS LAST,expected_close_date,id"
        if order == "quarter_stage"
        else "expected_close_date NULLS LAST,amount DESC,updated_at DESC,id"
    )
    recent_ctes = opportunity_recency_ctes("filtered") if order == "recent_follow_up" else ""
    page_source = "recency_ranked" if recent_ctes else "filtered"
    if recent_ctes:
        ordering = "latest_follow_up_at DESC NULLS LAST,name,id"
    overview_sql = ""
    overview_ctes = ""
    if include_overview:
        # These aggregates share filtered with page IDs and summary. No second
        # scope builder, pagination restriction or snapshot can change the set.
        overview_year_arg = bind(overview_year, "integer")
        overview_quarters_arg = bind(overview_quarters, "integer[]")

        def in_overview_period(column):
            return (f"(cardinality({overview_quarters_arg})=0 OR ("
                    f"extract(year FROM timezone('Asia/Shanghai',{column}))={overview_year_arg} AND "
                    f"extract(quarter FROM timezone('Asia/Shanghai',{column}))::integer=ANY({overview_quarters_arg})))")

        # Materialize the RLS-visible visit/link sets before mapping them to
        # the complete filtered set, instead of probing visits per opportunity.
        # Legacy direct links take precedence; only absent ones use link rows.
        overview_ctes = f""", overview_visits AS MATERIALIZED (
          SELECT v.id,v.workspace_id,v.opportunity_id FROM activity.visit v
          WHERE v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL
            AND v.status IN ('confirmed','archived')
            AND {in_overview_period('v.interaction_at')}
        ), overview_visit_links AS MATERIALIZED (
          SELECT vo.visit_id,vo.opportunity_id,vo.workspace_id FROM activity.visit_opportunity vo
          WHERE vo.workspace_id=common.current_workspace_id()
            AND vo.opportunity_id IN (SELECT id FROM filtered)
        ), overview_active_ids AS MATERIALIZED (
          SELECT v.opportunity_id FROM overview_visits v
          JOIN filtered f ON f.id=v.opportunity_id
          UNION
          SELECT vo.opportunity_id FROM overview_visits v
          JOIN overview_visit_links vo ON vo.visit_id=v.id AND vo.workspace_id=v.workspace_id
          WHERE v.opportunity_id IS NULL
        )"""
        overview_sql = f""", (SELECT jsonb_build_object(
          'total',count(*),
          'won',count(*) FILTER(WHERE status='won' AND {in_overview_period('closed_at')}),
          'active',count(*) FILTER(WHERE status<>'lost'
            AND id IN (SELECT opportunity_id FROM overview_active_ids)),
          'missingCloseDates',count(*) FILTER(WHERE cardinality({overview_quarters_arg})>0
            AND status<>'lost' AND expected_close_date IS NULL AND expected_close_year IS NULL),
          'missingWonDates',count(*) FILTER(WHERE cardinality({overview_quarters_arg})>0
            AND status='won' AND closed_at IS NULL),
          'creation_date_facts',COALESCE(jsonb_agg(creation_date_fact),'[]'::jsonb),
          'demo_scene_count',(SELECT count(*) FROM crm.opportunity_demo_scenes d
            WHERE d.deleted_at IS NULL AND d.opportunity_id IN (SELECT id FROM filtered)
              AND {in_overview_period('d.created_at')})
        ) FROM filtered) AS overview_metrics"""
    # Customer names are a per-project reference lookup, not another scope scan.
    # LIMIT 1 preserves that bounded lookup when PostgreSQL reuses a generic plan
    # across actors; a normal join can scan every customer's expensive RLS policy.
    # Customer IDs are unique, and both customer RLS and the reference fallback
    # remain in effect. Memoize can reuse lookups for projects sharing a customer.
    # All fragments above are owned by this module. Every external value is bound.
    sql = f"""WITH {scope_cte}authorized AS MATERIALIZED (
        SELECT o.id,o.name,o.customer_id,o.amount,o.status,o.probability,o.stage_code,
          {STAGE_SQL} AS display_stage,{PRODUCT_LINE_DISPLAY_SQL} AS product_line,pl.items AS product_lines,
          o.expected_close_date,o.updated_at,o.closed_at,
          o.expected_close_year,o.expected_close_quarter,o.owner_user_ref_id AS owner_id,
          COALESCE(c.name,security.customer_reference(o.customer_id)->>'name',security.opportunity_read_context(o.id)->>'customer_name') AS customer_name,
          COALESCE(owner.display_name,'待分配') AS owner_name,
          COALESCE(team.name,'待分配团队') AS team_name,o.owner_team_id AS team_id,
          {creation_date_projection()} AS creation_date_fact,
          extract(year FROM timezone('Asia/Shanghai',o.closed_at))::integer AS closed_year
        FROM {opportunity_source}
        CROSS JOIN LATERAL(SELECT {PRODUCT_LINES_SQL} AS items) pl
        LEFT JOIN LATERAL (
          SELECT customer.name FROM crm.customer customer
          WHERE customer.id=o.customer_id AND customer.deleted_at IS NULL LIMIT 1
        ) c ON true
        LEFT JOIN platform.user_ref owner ON owner.id=o.owner_user_ref_id
        LEFT JOIN platform.team team ON team.id=o.owner_team_id
        WHERE {" AND ".join(base)}
      ), filtered AS MATERIALIZED (SELECT * FROM authorized WHERE {" AND ".join(filters) or "true"}){recent_ctes}{overview_ctes}
      SELECT (SELECT jsonb_build_object('total',count(*),
          'open_count',count(*) FILTER(WHERE status='open'),
          'unknown_open_amount_count',count(*) FILTER(WHERE status='open' AND (amount IS NULL OR amount<0)),
          'open_amount',CASE WHEN count(*) FILTER(WHERE status='open' AND (amount IS NULL OR amount<0))>0
            THEN NULL ELSE COALESCE(sum(amount) FILTER(WHERE status='open'),0) END) FROM filtered) AS summary,
        (SELECT COALESCE(jsonb_agg(id), '[]'::jsonb) FROM
          (SELECT id FROM {page_source} ORDER BY {ordering} LIMIT {limit_arg} OFFSET {offset_arg}) page) AS ids,
        (SELECT COALESCE(jsonb_agg(creation_date_fact),'[]'::jsonb) FROM authorized) AS creation_date_facts,
        jsonb_build_object(
          'owners',(SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]'::jsonb) FROM
            (SELECT DISTINCT owner_name AS value FROM authorized
             WHERE owner_name IS NOT NULL AND {team_predicate}) valueset),
          'teams',(SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]'::jsonb) FROM
            (SELECT DISTINCT team_name AS value FROM authorized WHERE team_name IS NOT NULL) valueset),
          'product_lines',(SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]'::jsonb) FROM
            (SELECT DISTINCT p->>'name' AS value FROM authorized,jsonb_array_elements(product_lines) p
             UNION SELECT product_line FROM authorized WHERE product_lines='[]'::jsonb
              AND product_line IS NOT NULL AND product_line<>'') valueset),
          'years',(SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]'::jsonb) FROM
            (SELECT COALESCE(extract(year FROM expected_close_date)::integer,expected_close_year) AS value
             FROM authorized
             UNION SELECT closed_year FROM authorized) valueset
            WHERE value IS NOT NULL)
        ) AS facets{overview_sql}"""  # noqa: S608
    result = await connection.fetchrow(sql, *args)
    facets = dict(result["facets"])
    creation_years = {
        day.year for fact in result["creation_date_facts"] if (day := business_created_on(fact)) is not None
    }
    facets["years"] = sorted(set(facets["years"]) | creation_years)
    ids = result["ids"]
    rows = (
        await connection.fetch(
            """SELECT o.id::text,o.customer_id::text,o.name,o.stage_code,o.amount,o.currency,o.probability,
          o.expected_close_date,o.expected_close_year,o.expected_close_quarter,o.original_owner_name,o.ownership_resolution,
          o.status,o.version_no,o.partner_name,o.partner_id::text,o.sales_channel,o.trade_relation,o.upstream_opportunity_id::text,
          o.product_line,o.closed_at,o.source_code,o.attributes,o.import_meta,o.created_at,o.updated_at,
          COALESCE(c.name,security.customer_reference(o.customer_id)->>'name',security.opportunity_read_context(o.id)->>'customer_name') AS customer_name,
          COALESCE((security.opportunity_read_context(o.id)->>'shared_read_only')::boolean,false) AS shared_read_only,
          COALESCE((security.opportunity_read_context(o.id)->>'can_read_customer')::boolean,false) AS can_read_customer,
          o.owner_user_ref_id::text AS owner_id,o.owner_team_id::text AS team_id,
          owner.display_name AS owner_name,team.name AS team_name,
          security.can_manage_fde_members(o.id) AS can_manage_fde_members,
          security.authorization_opportunity('opportunity.update',o.id) AS can_edit,
          security.authorization_opportunity('opportunity.close',o.id) AS can_close,
          security.authorization_opportunity('opportunity.reopen',o.id) AS can_reopen
        FROM unnest($1::uuid[]) WITH ORDINALITY page(id,ordinal)
        JOIN crm.opportunity o ON o.id=page.id
        LEFT JOIN crm.customer c ON c.id=o.customer_id AND c.deleted_at IS NULL
        LEFT JOIN platform.user_ref owner ON owner.id=o.owner_user_ref_id
        LEFT JOIN platform.team team ON team.id=o.owner_team_id ORDER BY page.ordinal""",
            ids,
        )
        if ids
        else []
    )
    from sales_backend.repositories.opportunities import OpportunityRepository

    items = await OpportunityRepository().enrich(connection, rows)
    has_more = offset + len(items) < result["summary"]["total"]
    page = {
        "items": items,
        "summary": result["summary"],
        "facets": facets,
        "team_options": team_options,
        "owner_options": owner_options,
        "defaults": {"team_id": primary_team_id},
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
        "offset": offset,
    }
    if include_overview:
        metrics = dict(result["overview_metrics"])
        creation_dates = [business_created_on(fact) for fact in metrics.pop("creation_date_facts")]
        metrics["newCount"] = sum(
            day is not None and (not overview_quarters or (
                day.year == overview_year and (day.month - 1) // 3 + 1 in overview_quarters
            )) for day in creation_dates
        )
        metrics["missingCreatedDates"] = sum(day is None for day in creation_dates)
        page["overview"] = {
            "metrics": metrics,
            "year": overview_year,
            "quarters": overview_quarters,
            "definition_version": "opportunity_filtered_overview_v1",
        }
    return page
