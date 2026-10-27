# ruff: noqa: S608 -- Only server-owned fragments; all request values are bound.
"""Creator history under live object authorization, with server-side pagination.

The date parser is shared with opportunity views. Only minimal authorized date
facts leave SQL before being bound back into the final full-set query. Imported
CRM creators are unknown: their created_by column records the import operator.
"""

from sales_backend.db import json_value
from sales_backend.repositories.authorization_checks import require_permission
from sales_backend.repositories.opportunity_browse import STAGE_SQL
from sales_backend.repositories.opportunity_business_date import business_created_on, creation_date_projection
from sales_backend.repositories.opportunity_workspace_query import QueryBuilder
from sales_backend.repositories.team_directory import selectable_teams

HISTORICAL = "COALESCE(o.import_meta->>'import_type'='crm_history',false)"
CREATOR = f"CASE WHEN {HISTORICAL} THEN NULL::uuid ELSE o.created_by_user_ref_id END"
READABLE = """o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL
 AND security.authorization_opportunity('opportunity.read',o.id)
 AND NOT COALESCE((security.opportunity_read_context(o.id)->>'shared_read_only')::boolean,false)"""
MEMBERSHIP = """tm.workspace_id=common.current_workspace_id()
 AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
 AND t.workspace_id=tm.workspace_id AND t.status='active' AND t.deleted_at IS NULL
 AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to"""


def scope_predicate(builder, actor, request):
    terms = [READABLE]
    if request.scope == "self":
        terms.append(f"({CREATOR})=" + builder.bind(str(actor.user_id), "uuid"))
    if request.member_id is not None:
        terms.append(f"({CREATOR})=" + builder.bind(str(request.member_id), "uuid"))
    if request.team_id is not None:
        team = builder.bind(str(request.team_id), "uuid")
        terms.append(f"""EXISTS(SELECT 1 FROM platform.team_membership tm
          JOIN platform.team t ON t.id=tm.team_id WHERE {MEMBERSHIP}
          AND tm.user_ref_id=({CREATOR}) AND tm.team_id={team})""")
    return " AND ".join(terms)


async def authorized_teams(connection, actor, team_id):
    teams = await selectable_teams(connection, actor, permission="opportunity.read")
    if team_id is not None and str(team_id) not in {row["id"] for row in teams}:
        raise PermissionError("所选部门已失效或不在当前商机授权范围内")
    return [{"id": row["id"], "name": row["name"]} for row in teams]


def compile_page(actor, request, date_map):
    builder = QueryBuilder()
    scope = scope_predicate(builder, actor, request)
    dates = builder.bind(date_map, "jsonb")
    filters = []
    if request.q:
        query = builder.bind(request.q, "text")
        filters.append(f"strpos(lower(concat_ws(' ',f.name,f.customer_name,f.creator_name)),lower({query}))>0")
    if request.created_from:
        filters.append("f.business_created_on>=" + builder.bind(request.created_from, "date"))
    if request.created_to:
        filters.append("f.business_created_on<=" + builder.bind(request.created_to, "date"))
    limit, offset = builder.bind(request.limit, "integer"), builder.bind(request.offset, "integer")
    sql = f"""WITH authorized AS MATERIALIZED (
      SELECT o.id,o.workspace_id,o.customer_id,o.name,o.status,o.probability,o.stage_code,
       o.owner_user_ref_id,({CREATOR}) AS creator_id,
       CASE WHEN {HISTORICAL} THEN NULL ELSE o.created_at END AS created_at
      FROM crm.opportunity o WHERE {scope}
    ), projected AS MATERIALIZED (
      SELECT o.id,o.customer_id,o.name,
       COALESCE(c.name,security.customer_reference(o.customer_id)->>'name') AS customer_name,
       o.creator_id,CASE WHEN o.creator_id IS NOT NULL THEN COALESCE(creator.display_name,'成员') END AS creator_name,
       ({dates}->>o.id::text)::date AS business_created_on,o.created_at,
       CASE WHEN o.owner_user_ref_id IS NOT NULL THEN COALESCE(owner.display_name,'成员') END AS owner_name,
       o.status,{STAGE_SQL} AS stage,
       security.authorization_customer('customer.read',o.customer_id) AS can_read_customer
      FROM authorized o
      LEFT JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id AND c.deleted_at IS NULL
      LEFT JOIN platform.user_ref creator ON creator.id=o.creator_id AND creator.workspace_id=o.workspace_id
      LEFT JOIN platform.user_ref owner ON owner.id=o.owner_user_ref_id AND owner.workspace_id=o.workspace_id
    ), filtered AS MATERIALIZED (SELECT * FROM projected f WHERE {' AND '.join(filters) or 'true'}),
    page AS (SELECT f.*,row_number() OVER(ORDER BY f.business_created_on DESC NULLS LAST,f.id ASC) AS row_position
      FROM filtered f ORDER BY f.business_created_on DESC NULLS LAST,f.id ASC LIMIT {limit} OFFSET {offset})
    SELECT (SELECT count(*) FROM filtered) AS total,
     (SELECT COALESCE(jsonb_agg(to_jsonb(p)-'row_position' ORDER BY p.row_position),'[]'::jsonb)
      FROM page p) AS items"""
    return sql, builder.args


def page_result(result, request, key="items"):
    total = result["total"]
    more = request.offset + request.limit < total
    return {key: json_value(result[key]), "total": total, "has_more": more,
            "next_offset": request.offset + request.limit if more else None}


async def page(connection, actor, request):
    await require_permission(connection, "opportunity.read")
    if request.team_id is not None:
        await authorized_teams(connection, actor, request.team_id)
    builder = QueryBuilder()
    scope = scope_predicate(builder, actor, request)
    # Validate a member against the complete authorized history, not active staff
    # or the requested page/date/search. This retains inactive creators.
    if request.member_id is not None and not await connection.fetchval(
            f"SELECT EXISTS(SELECT 1 FROM crm.opportunity o WHERE {scope})", *builder.args):
        raise PermissionError("所选创建人没有当前可查看的创建历史，或不属于所选部门")
    facts = await connection.fetch(
        f"SELECT o.id::text,{creation_date_projection()} AS fact FROM crm.opportunity o WHERE {scope}", *builder.args)
    dates = {}
    for row in facts:
        value = business_created_on(json_value(row["fact"]))
        dates[row["id"]] = value.isoformat() if value else None
    sql, args = compile_page(actor, request, dates)
    return page_result(await connection.fetchrow(sql, *args), request)


def compile_members(request, allowed_team_ids):
    builder = QueryBuilder()
    allowed = builder.bind(allowed_team_ids, "uuid[]")
    terms = []
    if request.team_id is not None:
        terms.append(builder.bind(str(request.team_id), "uuid") + "=ANY(m.team_ids)")
    if request.q.strip():
        terms.append("strpos(lower(m.name),lower(" + builder.bind(request.q.strip(), "text") + "))>0")
    limit, offset = builder.bind(request.limit, "integer"), builder.bind(request.offset, "integer")
    sql = f"""WITH readable_creators AS MATERIALIZED (
      SELECT DISTINCT ({CREATOR}) AS id FROM crm.opportunity o
       WHERE {READABLE} AND ({CREATOR}) IS NOT NULL
    ), members AS MATERIALIZED (
      SELECT u.id,COALESCE(u.display_name,'成员') AS name,
       (u.status='active' AND u.deleted_at IS NULL) AS is_active,
       ARRAY(SELECT DISTINCT tm.team_id FROM platform.team_membership tm
        JOIN platform.team t ON t.id=tm.team_id WHERE {MEMBERSHIP}
         AND tm.user_ref_id=u.id AND tm.team_id=ANY({allowed}) ORDER BY tm.team_id) AS team_ids
      FROM readable_creators r JOIN platform.user_ref u ON u.id=r.id AND u.workspace_id=common.current_workspace_id()
    ), filtered AS MATERIALIZED (SELECT * FROM members m WHERE {' AND '.join(terms) or 'true'}),
    page AS (SELECT m.*,row_number() OVER(ORDER BY m.name COLLATE "C",m.id) AS row_position
      FROM filtered m ORDER BY m.name COLLATE "C",m.id LIMIT {limit} OFFSET {offset})
    SELECT (SELECT count(*) FROM filtered) AS total,
     (SELECT COALESCE(jsonb_agg(to_jsonb(p)-'row_position' ORDER BY p.row_position),'[]'::jsonb)
      FROM page p) AS members"""
    return sql, builder.args


async def options(connection, actor, request):
    await require_permission(connection, "opportunity.read")
    teams = await authorized_teams(connection, actor, request.team_id)
    sql, args = compile_members(request, [row["id"] for row in teams])
    return {"teams": teams, **page_result(await connection.fetchrow(sql, *args), request, "members"),
            "default_scope": "self", "team_basis": "current_creator_membership"}
