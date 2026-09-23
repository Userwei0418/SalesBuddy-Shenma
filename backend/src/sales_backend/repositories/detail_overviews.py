"""Bounded customer/opportunity overviews from complete, RLS-authorized facts.

History bodies are read separately. Aggregates never use the loaded history page,
and scoring stays in the domain module shared with the legacy detail contract.
"""

from sales_backend.domain.customer_profile import customer_profile_from_facts
from sales_backend.domain.visit_dates import visit_date_fields
from sales_backend.repositories.customer_risk import current_clear_assessment
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.repositories.opportunities import OpportunityRepository


async def related_summary(connection, customer_id, opportunity_id=None):
    row = await connection.fetchrow(
        """WITH visits AS MATERIALIZED (
          SELECT v.id,v.status,v.interaction_at,v.created_at,v.recorded_on,v.history_sort_date
          FROM activity.visit v WHERE (v.customer_id=$1::uuid OR (v.opportunity_id IS NULL AND EXISTS (
            SELECT 1 FROM activity.visit_opportunity vo JOIN crm.opportunity linked ON linked.id=vo.opportunity_id
            WHERE vo.visit_id=v.id AND linked.customer_id=$1::uuid AND linked.deleted_at IS NULL))
            OR (v.customer_id IS NULL AND v.opportunity_id IS NOT NULL AND EXISTS (
              SELECT 1 FROM crm.opportunity linked WHERE linked.id=v.opportunity_id
                AND linked.customer_id=$1::uuid AND linked.deleted_at IS NULL))) AND v.deleted_at IS NULL
            AND ($2::uuid IS NULL OR v.opportunity_id=$2::uuid OR (v.opportunity_id IS NULL AND EXISTS (
              SELECT 1 FROM activity.visit_opportunity vo WHERE vo.visit_id=v.id AND vo.opportunity_id=$2::uuid)))
        ), visit_counts AS (
          SELECT count(*)::integer AS visit_count,
            count(*) FILTER(WHERE status IN ('confirmed','archived'))::integer AS confirmed_visit_count
          FROM visits
        ), task_counts AS (
          SELECT status,count(*)::integer AS count FROM workflow.task
          WHERE customer_id=$1::uuid AND deleted_at IS NULL
            AND ($2::uuid IS NULL OR opportunity_id=$2::uuid) GROUP BY status
        ), risks AS MATERIALIZED (
          SELECT id,status,severity_code,opened_at
          FROM insight.risk WHERE customer_id=$1::uuid AND deleted_at IS NULL
            AND ($2::uuid IS NULL OR opportunity_id=$2::uuid)
        ) SELECT
          (SELECT visit_count FROM visit_counts) AS visit_count,
          (SELECT confirmed_visit_count FROM visit_counts) AS confirmed_visit_count,
          (SELECT to_jsonb(selected)||jsonb_build_object('next_action',body.next_action)
           FROM (SELECT v.id,v.status,v.interaction_at,v.created_at,v.recorded_on FROM visits v
             ORDER BY v.interaction_at DESC NULLS LAST,v.history_sort_date DESC,v.id DESC LIMIT 1) selected
           JOIN LATERAL (SELECT left(v.next_action,600) AS next_action FROM activity.visit v
             WHERE v.id=selected.id LIMIT 1) body ON true) AS latest_visit,
          (SELECT COALESCE(jsonb_object_agg(status,count),'{}'::jsonb) FROM task_counts) AS task_status_counts,
          (SELECT count(*)::integer FROM risks) AS risk_count,
          (SELECT COALESCE(jsonb_agg(DISTINCT CASE WHEN severity_code IN ('critical','high','medium','low')
            THEN severity_code ELSE NULL END),'[]'::jsonb) FROM risks
            WHERE status NOT IN ('resolved','accepted')) AS open_risk_severities,
          (SELECT to_jsonb(body) FROM (SELECT id FROM risks WHERE status<>'resolved'
            ORDER BY CASE WHEN status IN ('new','pending','in_progress','escalated') THEN 0 ELSE 1 END,
              opened_at DESC,id LIMIT 1) selected JOIN LATERAL (
                SELECT id::text,opportunity_id::text,status,severity_code,title,
                  left(description,600) AS description,opened_at FROM insight.risk WHERE id=selected.id LIMIT 1
              ) body ON true) AS status_risk,
          (SELECT to_jsonb(body) FROM (SELECT id FROM risks WHERE status NOT IN ('resolved','accepted')
            ORDER BY CASE WHEN status IN ('new','pending','in_progress','escalated') THEN 0 ELSE 1 END,
              opened_at DESC,id LIMIT 1) selected JOIN LATERAL (
                SELECT id::text,opportunity_id::text,status,severity_code,title,
                  left(description,600) AS description,opened_at FROM insight.risk WHERE id=selected.id LIMIT 1
              ) body ON true) AS open_risk""",
        customer_id, opportunity_id,
    )
    result = dict(row)
    result["task_count"] = sum(result["task_status_counts"].values())
    if result["latest_visit"]:
        result["latest_visit"] = visit_date_fields(result["latest_visit"])
    return result


async def opportunity_risk_summaries(connection, ids):
    """At most two risk references per displayed opportunity; keep accepted-risk semantics."""
    if not ids:
        return {}
    rows = await connection.fetch(
        """SELECT selected.id::text,
          (SELECT to_jsonb(r) FROM (
            SELECT id::text,opportunity_id::text,title,severity_code,status FROM insight.risk
            WHERE opportunity_id=selected.id AND deleted_at IS NULL AND status<>'resolved'
            ORDER BY CASE WHEN status IN ('new','pending','in_progress','escalated') THEN 0 ELSE 1 END,
              opened_at DESC,id LIMIT 1) r) AS first_risk,
          (SELECT to_jsonb(r) FROM (
            SELECT id::text,opportunity_id::text,title,severity_code,status FROM insight.risk
            WHERE opportunity_id=selected.id AND deleted_at IS NULL AND status<>'resolved'
              AND severity_code IN ('critical','high')
            ORDER BY CASE WHEN status IN ('new','pending','in_progress','escalated') THEN 0 ELSE 1 END,
              opened_at DESC,id LIMIT 1) r) AS high_risk
        FROM unnest($1::uuid[]) selected(id)""",
        ids,
    )
    return {r["id"]: {"first_risk": r["first_risk"], "high_risk": r["high_risk"]} for r in rows}


class DetailOverviewRepository:
    async def customer_header(self, connection, customer_id):
        """Authorized master data only; never scan visit/task/risk histories."""
        customer = await CustomerRepository().base(connection, customer_id=customer_id)
        if customer is None:
            return None
        references = await connection.fetchrow(
            """SELECT
              (SELECT to_jsonb(c) FROM (SELECT id::text,name,title,department,contact_category_code,
                relationship_role_code,is_primary FROM crm.contact WHERE customer_id=$1::uuid
                AND deleted_at IS NULL ORDER BY is_primary DESC,name,id LIMIT 1) c) AS primary_contact,
              (SELECT to_jsonb(o) FROM (SELECT id::text,name,stage_code,status,amount,probability,expected_close_date,
                expected_close_year,expected_close_quarter,original_owner_name,ownership_resolution
                FROM crm.opportunity WHERE customer_id=$1::uuid AND deleted_at IS NULL
                ORDER BY (status='open') DESC,expected_close_date NULLS LAST,amount DESC,updated_at DESC,id LIMIT 1
              ) o) AS primary_opportunity""", customer_id,
        )
        return {**customer, **dict(references), "read_model": "detail_header_v1"}

    async def opportunity_reference(self, connection, actor, opportunity_id, *, customer_id=None):
        """Same subject predicate as the existing targeted opportunity detail read."""
        return await connection.fetchrow(
            """SELECT id::text,customer_id::text FROM crm.opportunity WHERE id=$1::uuid AND deleted_at IS NULL
              AND ($2::uuid IS NULL OR customer_id=$2)
              AND ($3::text IS NULL OR $3<>'sales' OR owner_user_ref_id=$4::uuid)
              AND ($3::text IS NULL OR $3<>'supervisor' OR owner_team_id=ANY($5::uuid[]))""",
            opportunity_id, customer_id, actor.role.value if actor else None,
            actor.user_id if actor else None, list(actor.team_ids) if actor else [],
        )

    async def customer(self, connection, customer_id):
        customer = await CustomerRepository().base(connection, customer_id=customer_id)
        if customer is None:
            return None
        facts = await connection.fetchrow(
            """WITH opportunity_facts AS (
              SELECT count(*)::integer AS opportunity_count,
                count(*) FILTER(WHERE status='open')::integer AS open_opportunity_count,
                count(*) FILTER(WHERE status='open' AND (amount IS NULL OR amount<0))::integer
                  AS unknown_open_amount_count,
                sum(amount) FILTER(WHERE status='open') AS known_open_amount,
                max(probability) FILTER(WHERE status NOT IN ('lost','cancelled')) AS max_probability
              FROM crm.opportunity WHERE customer_id=$1::uuid AND deleted_at IS NULL
            ), contact_facts AS (
              SELECT count(*)::integer AS contact_count,
                COALESCE(jsonb_agg(DISTINCT relationship_role_code)
                  FILTER(WHERE relationship_role_code IN ('decision_maker','influencer','user')),'[]'::jsonb)
                  AS contact_roles
              FROM crm.contact WHERE customer_id=$1::uuid AND deleted_at IS NULL
            ) SELECT opportunity_facts.*,contact_facts.*,
              (SELECT to_jsonb(c) FROM (SELECT id::text,name,title,department,contact_category_code,
                relationship_role_code,is_primary FROM crm.contact WHERE customer_id=$1::uuid
                AND deleted_at IS NULL ORDER BY is_primary DESC,name,id LIMIT 1) c) AS primary_contact,
              (SELECT to_jsonb(o) FROM (SELECT id::text,name,stage_code,status,amount,probability,expected_close_date,
                expected_close_year,expected_close_quarter,original_owner_name,ownership_resolution
                FROM crm.opportunity WHERE customer_id=$1::uuid AND deleted_at IS NULL
                ORDER BY (status='open') DESC,expected_close_date NULLS LAST,amount DESC,updated_at DESC,id LIMIT 1
              ) o) AS primary_opportunity
            FROM opportunity_facts CROSS JOIN contact_facts""",
            customer_id,
        )
        related = await related_summary(connection, customer_id)
        result = dict(facts)
        result["open_amount"] = None if result["unknown_open_amount_count"] else (result.pop("known_open_amount") or 0)
        result.pop("known_open_amount", None)
        summary = {**related, **result}
        profile_facts = {
            **summary,
            "risk_assessment_clear": await current_clear_assessment(connection, customer_id),
        }
        return {
            **customer, "summary": summary,
            "profile": customer_profile_from_facts(customer, profile_facts),
            "primary_contact": summary.pop("primary_contact"),
            "primary_opportunity": summary.pop("primary_opportunity"),
            "read_model": "detail_overview_v1",
        }

    async def opportunity_header(self, connection, actor, opportunity_id, *, customer_id=None):
        rows = await OpportunityRepository().list(
            connection, actor, customer_id=customer_id, opportunity_id=opportunity_id,
            owner_name=None, probability=None, stage_code=None, close_from=None, close_to=None,
            limit=1, include_closed=True,
        )
        if not rows:
            return None
        item = rows[0]
        return {
            "id": item["customer_id"], "name": item["customer_name"], "opportunities": [item],
            "primary_opportunity": item, "read_model": "detail_header_v1",
        }

    async def opportunity(self, connection, actor, opportunity_id, *, customer_id=None):
        header = await self.opportunity_header(connection, actor, opportunity_id, customer_id=customer_id)
        if header is None:
            return None
        item = header["primary_opportunity"]
        summary = await related_summary(connection, item["customer_id"], opportunity_id)
        summary.update(opportunity_count=1, open_opportunity_count=int(item["status"] == "open"),
                       unknown_open_amount_count=int(item["status"] == "open" and
                                                     (item["amount"] is None or item["amount"] < 0)))
        summary["open_amount"] = (None if summary["unknown_open_amount_count"] else item["amount"]) \
            if item["status"] == "open" else 0
        item["risk_summary"] = (await opportunity_risk_summaries(connection, [opportunity_id]))[opportunity_id]
        return {
            "id": item["customer_id"], "name": item["customer_name"], "opportunities": [item],
            "primary_opportunity": item, "summary": summary, "read_model": "detail_overview_v1",
        }
