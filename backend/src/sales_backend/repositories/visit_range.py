# ruff: noqa: S608 -- SQL clauses/identifiers are server constants; every request value stays bound.
"""One-statement summary and page over the existing visit.read/RLS fact set.

No data-kind predicate is introduced: existing authorized trial records remain
visible, with original backend provenance untouched. Only confirmed/archived
records appear, and each visit contributes once regardless of bridge links.
"""

from datetime import datetime, time, timedelta

from sales_backend.domain.visit_dates import BEIJING, visit_date_fields
from sales_backend.repositories.authorization_checks import require_permission


def selection(
    *,
    scope="all",
    q="",
    recorder_ids=None,
    team_id=None,
    customer_id=None,
    opportunity_id=None,
    date_basis="interaction",
    date_from=None,
    date_to=None,
):
    args, predicates = (
        [],
        [
            "v.workspace_id=common.current_workspace_id()",
            "v.deleted_at IS NULL",
            "v.status IN ('confirmed','archived')",
        ],
    )

    def bind(value, cast):
        args.append(value)
        return f"${len(args)}::{cast}"

    if scope == "mine":
        predicates.append("v.recorder_user_ref_id=common.current_user_ref_id()")
    # The Web follow-up page needs a bounded team/member scope without making
    # one request per customer.  Keep all values bound and let the existing
    # visit RLS policy remain the final authorization gate.
    if recorder_ids:
        args.append([str(value) for value in recorder_ids])
        predicates.append(f"v.recorder_user_ref_id=ANY(${len(args)}::uuid[])")
    if team_id:
        predicates.append("v.recorder_team_id=" + bind(team_id, "uuid"))
    if customer_id:
        customer_ref = bind(customer_id, "uuid")
        predicates.append(f"""({customer_ref}=v.customer_id OR EXISTS (
          SELECT 1 FROM activity.visit_opportunity vo JOIN crm.opportunity linked
            ON linked.id=vo.opportunity_id AND linked.deleted_at IS NULL
          WHERE vo.visit_id=v.id AND linked.customer_id={customer_ref}
        ) OR (v.customer_id IS NULL AND v.opportunity_id IS NOT NULL AND EXISTS (
          SELECT 1 FROM crm.opportunity linked
          WHERE linked.id=v.opportunity_id AND linked.customer_id={customer_ref} AND linked.deleted_at IS NULL
        )))""")
    if opportunity_id:
        opportunity_ref = bind(opportunity_id, "uuid")
        predicates.append(f"""({opportunity_ref}=v.opportunity_id OR EXISTS (
          SELECT 1 FROM activity.visit_opportunity vo
          WHERE vo.visit_id=v.id AND vo.opportunity_id={opportunity_ref}
        ))""")
    column = "v.created_at" if date_basis == "created" else "v.interaction_at"
    if date_from:
        predicates.append(column + ">=" + bind(datetime.combine(date_from, time(), BEIJING), "timestamptz"))
    if date_to:
        predicates.append(
            column + "<" + bind(datetime.combine(date_to + timedelta(days=1), time(), BEIJING), "timestamptz")
        )
    customer_name = (
        "COALESCE(NULLIF(v.archived_fields->>'customer_name',''),c.name,"
        "security.customer_reference(v.customer_id)->>'name')"
    )
    partner_name = "COALESCE(p.name,NULLIF(v.partner_name_snapshot,''))"
    if q:
        term = bind(q, "text")
        predicates.append(f"""(strpos(lower(concat_ws(' ',{customer_name},{partner_name},u.display_name,
          v.original_recorder_name,v.follow_up_record,v.next_action)),lower({term}))>0
          OR EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.deleted_at IS NULL
            AND (o.id=v.opportunity_id OR EXISTS(SELECT 1 FROM activity.visit_opportunity vo
              WHERE vo.visit_id=v.id AND vo.opportunity_id=o.id))
            AND strpos(lower(o.name),lower({term}))>0))""")
    # The complete filtered set contains only small metadata. Body text and
    # archived JSON are never materialized for summaries or paginated details.
    cte = f"""WITH filtered AS MATERIALIZED (
      SELECT v.id,v.customer_id,v.partner_id,v.opportunity_id,v.recorder_user_ref_id,
        v.created_at,v.interaction_at,v.follow_up_score,u.display_name AS recorder_name,
        {customer_name} AS customer_name,{partner_name} AS partner_name
      FROM activity.visit v LEFT JOIN platform.user_ref u ON u.id=v.recorder_user_ref_id
      LEFT JOIN crm.customer c ON c.id=v.customer_id
      LEFT JOIN crm.partner p ON p.id=v.partner_id
      WHERE {" AND ".join(predicates)}
    )"""
    return cte, args, column.replace("v.", "f.")


PROJECTION = """SELECT v.id::text,v.status,v.customer_id::text,f.customer_name,v.partner_id::text,f.partner_name,
 v.opportunity_id::text,o.name AS opportunity_name,v.recorder_user_ref_id::text AS recorder_id,f.recorder_name,
 CASE WHEN v.recorder_user_ref_id IS NULL THEN NULL
 ELSE u.status='active' AND u.deleted_at IS NULL END AS recorder_active,
 v.recording_role_code_snapshot,v.recording_team_id_snapshot::text,
 CASE WHEN v.import_meta->>'import_type'='crm_history' THEN 'crm_history'
 WHEN v.recording_role_code_snapshot IS NOT NULL THEN 'recorded' ELSE 'unknown' END AS record_source,
 v.original_recorder_name,v.manager_user_ref_id::text AS manager_id,manager.display_name AS manager_name,
 v.created_by_user_ref_id::text AS creator_id,creator.display_name AS creator_name,
 v.interaction_at,v.recorded_on,v.created_at,v.interaction_mode_code,v.contact_name_snapshot AS contact_name,
 v.is_first_visit,v.follow_up_score,
 CASE WHEN jsonb_typeof(v.quality_review)='object' AND v.quality_review<>'{}'::jsonb
 THEN jsonb_build_object('follow_up_score',v.quality_review->'follow_up_score',
 'grade',v.quality_review->'grade','next_action_passed',v.quality_review->'next_action_passed') END AS quality_review,
 (security.authorization_has('customer.read') AND EXISTS(SELECT 1 FROM crm.customer accessible
   WHERE accessible.id=v.customer_id AND accessible.workspace_id=v.workspace_id
   AND accessible.deleted_at IS NULL)) AS can_open_customer,
 (security.authorization_has('opportunity.read') AND EXISTS(SELECT 1 FROM crm.opportunity accessible
   WHERE accessible.id=v.opportunity_id AND accessible.workspace_id=v.workspace_id
   AND accessible.deleted_at IS NULL)) AS can_open_opportunity,
 true AS can_read_detail,
 COALESCE((SELECT jsonb_agg(jsonb_build_object('id',linked.id::text,'name',linked.name,
   'customer_id',linked.customer_id::text,'customer_name',COALESCE(c.name,security.customer_reference(linked.customer_id)->>'name'),
   'can_open',security.authorization_has('opportunity.read')) ORDER BY linked.name,linked.id)
   FROM crm.opportunity linked LEFT JOIN crm.customer c ON c.id=linked.customer_id
   WHERE linked.deleted_at IS NULL AND (linked.id=v.opportunity_id OR EXISTS(
     SELECT 1 FROM activity.visit_opportunity vo WHERE vo.visit_id=v.id AND vo.opportunity_id=linked.id))),
   '[]'::jsonb) AS linked_opportunities,
 left(v.follow_up_record,600) AS follow_up_record,left(v.next_action,600) AS next_action,true AS is_summary
 FROM page JOIN activity.visit v ON v.id=page.id JOIN filtered f ON f.id=v.id
 LEFT JOIN platform.user_ref u ON u.id=v.recorder_user_ref_id
 LEFT JOIN platform.user_ref manager ON manager.id=v.manager_user_ref_id
 LEFT JOIN platform.user_ref creator ON creator.id=v.created_by_user_ref_id
 LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id"""


def public_record(row):
    item = visit_date_fields(dict(row))
    quality = item.get("quality_review")
    item["quality_review"] = None
    if isinstance(quality, dict) and quality:
        score, grade, passed = (quality.get(k) for k in ("follow_up_score", "grade", "next_action_passed"))
        item["quality_review"] = dict(
            follow_up_score=score if type(score) in (int, float) else None,
            grade=grade if isinstance(grade, str) else None,
            next_action_passed=passed if type(passed) is bool else None,
        )
    kind = (
        "customer"
        if item["customer_id"]
        else "partner"
        if item["partner_id"]
        else "opportunity"
        if item["linked_opportunities"]
        else "unknown"
    )
    item["subject_kind"] = kind
    item["subject_id"] = item["customer_id"] or item["partner_id"] or item["opportunity_id"]
    item["subject_name"] = (
        (item["customer_name"] or "历史客户")
        if kind == "customer"
        else (item["partner_name"] or "历史伙伴")
        if kind == "partner"
        else "、".join(o["name"] for o in item["linked_opportunities"])
        if kind == "opportunity"
        else "历史主体待核对"
    )
    return item


class VisitRangeRepository:
    async def page(self, connection, *, limit=50, offset=0, **filters):
        await require_permission(connection, "visit.read")
        cte, args, order = selection(**filters)
        args = [*args, limit + 1, offset, limit]
        sql = (
            cte
            + f""", subjects AS (
          SELECT 'customer' kind,customer_id id FROM filtered WHERE customer_id IS NOT NULL
          UNION SELECT 'partner',partner_id FROM filtered WHERE partner_id IS NOT NULL
          UNION SELECT 'customer',o.customer_id FROM filtered f JOIN crm.opportunity o ON
            (o.id=f.opportunity_id OR EXISTS(SELECT 1 FROM activity.visit_opportunity vo
              WHERE vo.visit_id=f.id AND vo.opportunity_id=o.id))
            WHERE o.deleted_at IS NULL AND o.customer_id IS NOT NULL
        ), page AS MATERIALIZED (
          SELECT f.id FROM filtered f ORDER BY {order} DESC NULLS LAST,f.created_at DESC,f.id DESC
          LIMIT ${len(args) - 2} OFFSET ${len(args) - 1}
        ), records AS (
        """
            + PROJECTION
            + f"""
        ), public_page AS (
          SELECT * FROM records ORDER BY {order.replace("f.", "")} DESC NULLS LAST,created_at DESC,id DESC
          LIMIT ${len(args)}
        ) SELECT
          (SELECT COALESCE(jsonb_agg(to_jsonb(p) ORDER BY {order.replace("f.", "p.")} DESC NULLS LAST,
              p.created_at DESC,p.id DESC),'[]'::jsonb) FROM public_page p) AS items,
          (SELECT jsonb_build_object('total',count(*),
             'scored_count',count(*) FILTER(WHERE follow_up_score IS NOT NULL),
             'subject_count',(SELECT count(*) FROM subjects)) FROM filtered) AS summary,
          (SELECT count(*)>${len(args)} FROM page) AS has_more,
          clock_timestamp() AS as_of"""
        )
        row = await connection.fetchrow(sql, *args)
        return {
            "items": [public_record(item) for item in row["items"]],
            "summary": dict(row["summary"]),
            "has_more": row["has_more"],
            "next_offset": offset + limit if row["has_more"] else None,
            "as_of": row["as_of"],
        }
