# ruff: noqa: S608 -- Query fragments are internal; all external values are bound.
"""Paged opportunity browsing; filters and totals share one authorized SQL set.

Only page IDs are hydrated with attributes, members, forecasts and actuals. Date
facets parse a minimal provenance projection; business bodies stay page-scoped.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sales_backend.repositories.collaboration import FDE_ROLES, scope_members
from sales_backend.repositories.opportunity_business_date import business_created_on, creation_date_projection
from sales_backend.repositories.team_directory import require_team, selectable_teams

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
    customer_id=None,
    owner_name=None,
    probability=None,
    stage_code=None,
    close_from=None,
    close_to=None,
    include_closed=False,
    scope=None,
    member_id=None,
    member_ids=None,
    query=None,
    stages=None,
    team=None,
    team_id=None,
    grade=None,
    product_line=None,
    year=None,
    quarters=None,
    close_period=None,
    order="close_date",
):
    team_options = await selectable_teams(connection, actor) if actor is not None else []
    if team_id is not None:
        selected = await require_team(connection, actor, team_id)
        if team and team != selected['name']:
            raise ValueError('团队ID与名称不一致，请刷新团队目录')
    args = []

    def bind(value, cast):
        args.append(value)
        return f"${len(args)}::{cast}"

    base = ["o.deleted_at IS NULL"]
    scope_cte = ""
    opportunity_source = "crm.opportunity o"
    if not include_closed:
        base.append("o.status='open'")
    role = actor.role.value if actor is not None else None
    if role == "sales":
        base.append(f"o.owner_user_ref_id={bind(actor.user_id, 'uuid')}")
    elif role == "supervisor":
        base.append(f"o.owner_team_id=ANY({bind(list(actor.team_ids), 'uuid[]')})")
    elif role in FDE_ROLES:
        _, people, _ = await scope_members(connection, actor, scope, member_id, member_ids)
        # A customer detail is the authorized customer panorama. A personal/team
        # project list is precisely the effective participation set.
        if not customer_id or member_id or member_ids:
            # Materialize the small participation set first; otherwise opportunity
            # RLS may be evaluated for every company project before the EXISTS.
            # Both participant and opportunity RLS still apply to the SQL join.
            scope_cte = f"""fde_projects AS MATERIALIZED (
                SELECT DISTINCT p.opportunity_id FROM crm.opportunity_participant p
                WHERE p.user_ref_id=ANY({bind(people, "uuid[]")})
                AND p.participant_role='fde' AND clock_timestamp()>=p.valid_from
                AND clock_timestamp()<p.valid_to AND security.fde_user_is_active(p.user_ref_id)
              ), """
            opportunity_source = "fde_projects fp JOIN crm.opportunity o ON o.id=fp.opportunity_id"
    if customer_id:
        base.append(f"o.customer_id={bind(customer_id, 'uuid')}")

    filters = []
    if owner_name:
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
        filters.append(f"product_line={bind(product_line, 'text')}")
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
        "COALESCE(extract(year FROM expected_close_date)::integer,expected_close_year) NULLS LAST,"
        "COALESCE(extract(quarter FROM expected_close_date)::integer,expected_close_quarter) NULLS LAST,"
        "probability DESC NULLS LAST,expected_close_date,id"
        if order == "quarter_stage"
        else "expected_close_date NULLS LAST,amount DESC,updated_at DESC,id"
    )
    # Customer names are a per-project reference lookup, not another scope scan.
    # LIMIT 1 preserves that bounded lookup when PostgreSQL reuses a generic plan
    # across actors; a normal join can scan every customer's expensive RLS policy.
    # Customer IDs are unique, and both customer RLS and the reference fallback
    # remain in effect. Memoize can reuse lookups for projects sharing a customer.
    # All fragments above are owned by this module. Every external value is bound.
    sql = f"""WITH {scope_cte}authorized AS MATERIALIZED (
        SELECT o.id,o.name,o.customer_id,o.amount,o.status,o.probability,o.stage_code,
          {STAGE_SQL} AS display_stage,o.product_line,o.expected_close_date,o.updated_at,
          o.expected_close_year,o.expected_close_quarter,
          COALESCE(c.name,security.customer_reference(o.customer_id)->>'name') AS customer_name,
          COALESCE(owner.display_name,'待分配') AS owner_name,
          COALESCE(team.name,'待分配团队') AS team_name,o.owner_team_id AS team_id,
          {creation_date_projection()} AS creation_date_fact,
          extract(year FROM timezone('Asia/Shanghai',o.closed_at))::integer AS closed_year
        FROM {opportunity_source}
        LEFT JOIN LATERAL (
          SELECT customer.name FROM crm.customer customer
          WHERE customer.id=o.customer_id AND customer.deleted_at IS NULL LIMIT 1
        ) c ON true
        LEFT JOIN platform.user_ref owner ON owner.id=o.owner_user_ref_id
        LEFT JOIN platform.team team ON team.id=o.owner_team_id
        WHERE {" AND ".join(base)}
      ), filtered AS MATERIALIZED (SELECT * FROM authorized WHERE {" AND ".join(filters) or "true"})
      SELECT (SELECT jsonb_build_object('total',count(*),
          'open_count',count(*) FILTER(WHERE status='open'),
          'unknown_open_amount_count',count(*) FILTER(WHERE status='open' AND (amount IS NULL OR amount<0)),
          'open_amount',CASE WHEN count(*) FILTER(WHERE status='open' AND (amount IS NULL OR amount<0))>0
            THEN NULL ELSE COALESCE(sum(amount) FILTER(WHERE status='open'),0) END) FROM filtered) AS summary,
        (SELECT COALESCE(jsonb_agg(id), '[]'::jsonb) FROM
          (SELECT id FROM filtered ORDER BY {ordering} LIMIT {limit_arg} OFFSET {offset_arg}) page) AS ids,
        (SELECT COALESCE(jsonb_agg(creation_date_fact),'[]'::jsonb) FROM authorized) AS creation_date_facts,
        jsonb_build_object(
          'owners',(SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]'::jsonb) FROM
            (SELECT DISTINCT owner_name AS value FROM authorized
             WHERE owner_name IS NOT NULL AND {team_predicate}) valueset),
          'teams',(SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]'::jsonb) FROM
            (SELECT DISTINCT team_name AS value FROM authorized WHERE team_name IS NOT NULL) valueset),
          'product_lines',(SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]'::jsonb) FROM
            (SELECT DISTINCT product_line AS value FROM authorized
             WHERE product_line IS NOT NULL AND product_line<>'') valueset),
          'years',(SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]'::jsonb) FROM
            (SELECT COALESCE(extract(year FROM expected_close_date)::integer,expected_close_year) AS value
             FROM authorized
             UNION SELECT closed_year FROM authorized) valueset
            WHERE value IS NOT NULL)
        ) AS facets"""  # noqa: S608
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
          o.status,o.version_no,o.partner_name,o.partner_id::text,o.sales_channel,
          o.product_line,o.closed_at,o.source_code,o.attributes,o.import_meta,o.created_at,o.updated_at,
          COALESCE(c.name,security.customer_reference(o.customer_id)->>'name') AS customer_name,
          o.owner_user_ref_id::text AS owner_id,o.owner_team_id::text AS team_id,
          owner.display_name AS owner_name,team.name AS team_name,
          security.can_manage_fde_members(o.id) AS can_manage_fde_members
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
    return {
        "items": items,
        "summary": result["summary"],
        "facets": facets,
        "team_options": team_options,
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
        "offset": offset,
    }
