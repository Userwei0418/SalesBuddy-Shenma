"""Narrow projections and guarded collaboration writes; no direct table grants."""

from sales_backend.db import json_value
from sales_backend.repositories.follow_up_recency import opportunity_recency_ctes


class OpportunityAccessRepository:
    async def access(self, connection, opportunity_id):
        return json_value(await connection.fetchval("SELECT security.opportunity_access($1::uuid)", opportunity_id))

    async def options(self, connection, *, query, customer_id, limit, offset, order="name"):
        if order == "recent_follow_up":
            return json_value(await connection.fetchval(
                RECENT_FOLLOW_UP_OPTIONS_SQL, query, customer_id, limit, offset
            ))
        return json_value(await connection.fetchval(
            "SELECT security.opportunity_follow_up_options($1,$2::uuid,$3,$4)", query, customer_id, limit, offset
        ))

    async def candidates(self, connection, opportunity_id, *, query, limit, offset):
        return json_value(await connection.fetchval(
            "SELECT security.opportunity_collaborator_candidates($1::uuid,$2,$3,$4)",
            opportunity_id, query, limit, offset,
        ))

    async def save_collaborators(self, connection, opportunity_id, body):
        return json_value(await connection.fetchval(
            "SELECT security.save_opportunity_collaborators($1::uuid,$2,$3::uuid[])",
            opportunity_id, body.version_no, body.member_ids,
        ))


async def follow_up_reference(connection, customer_id, opportunity_id=None):
    if opportunity_id:
        minimal = json_value(await connection.fetchval(
            "SELECT security.company_opportunity_follow_up_reference($1::uuid)", opportunity_id
        ))
        if minimal:
            customer = minimal["customer_reference"]
            if str(customer["id"]) != str(customer_id):
                return None
            reference = {**customer, "opportunity_name": minimal["name"]}
            legacy = json_value(await connection.fetchval(
                "SELECT security.customer_reference($1::uuid)", customer_id
            ))
            if legacy:
                reference.update(customer_type_code=legacy.get("customer_type_code"),
                                 owner_team_id=legacy.get("owner_team_id"))
            return reference
        reference = json_value(await connection.fetchval(
            "SELECT security.opportunity_follow_up_reference($1::uuid,$2::uuid)", customer_id, opportunity_id
        ))
        if not reference:
            return None
        # Preserve legacy recording metadata only when independently authorized.
        # This internal helper is never the public minimal customer projection.
        legacy = json_value(await connection.fetchval("SELECT security.customer_reference($1::uuid)", customer_id))
        if legacy:
            reference.update(customer_type_code=legacy.get("customer_type_code"),
                             owner_team_id=legacy.get("owner_team_id"))
        # Legacy authorized callers retain a name for the immutable receipt.
        reference["opportunity_name"] = await connection.fetchval(
            "SELECT name FROM crm.opportunity WHERE id=$1::uuid AND customer_id=$2::uuid AND deleted_at IS NULL",
            opportunity_id, customer_id,
        )
        return reference
    legacy = json_value(await connection.fetchval("SELECT security.customer_reference($1::uuid)", customer_id))
    if legacy:
        return legacy
    minimal = json_value(await connection.fetchval(
        "SELECT security.company_customer_follow_up_reference($1::uuid)", customer_id
    ))
    return minimal["customer_reference"] if minimal else None


# The customer reference is independent of customer-detail RLS: a collaborator
# must retain their existing project even without access to the customer page.
# All dynamic input is bound; the appended CTE relation is an internal constant.
RECENT_FOLLOW_UP_OPTIONS_SQL = """WITH authorized AS MATERIALIZED (
  SELECT o.id,o.name,o.customer_id,security.opportunity_follow_up_reference(o.customer_id,o.id) AS customer
  FROM crm.opportunity o
  WHERE o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL
    AND ($2::uuid IS NULL OR o.customer_id=$2)
    AND security.authorization_opportunity('opportunity.read',o.id)
    AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY[
      'visit.create','visit.structure','visit.quality_review','visit.read'
    ]) permission WHERE NOT security.authorization_visit_target(
      permission,o.customer_id,o.id,common.current_user_ref_id(),
      NULLIF(split_part(COALESCE(current_setting('app.team_ids',true),''),',',1),'')::uuid))
), candidates AS MATERIALIZED (
  SELECT id,name,customer_id FROM authorized WHERE customer IS NOT NULL
    AND (COALESCE($1::text,'')='' OR strpos(lower(name),lower($1))>0
      OR strpos(lower(customer->>'name'),lower($1))>0)
)""" + opportunity_recency_ctes("candidates") + """, page AS MATERIALIZED (
  SELECT * FROM recency_ranked
  ORDER BY latest_follow_up_at DESC NULLS LAST,name,id LIMIT $3 OFFSET $4
), projected AS MATERIALIZED (
  SELECT page.*,security.opportunity_access(id)-'collaborators' AS item,
    security.opportunity_transfer_capability(id) AS transfer FROM page
)
SELECT jsonb_build_object('contract_version',1,'items',COALESCE((
  SELECT jsonb_agg(jsonb_set(jsonb_set(item,'{capabilities,can_transfer}',transfer->'can_transfer'),
    '{capability_reasons,transfer}',transfer->'reason')
    ORDER BY latest_follow_up_at DESC NULLS LAST,name,id) FROM projected),'[]'::jsonb),
  'total',(SELECT count(*) FROM candidates),'limit',$3::integer,'offset',$4::integer)"""
