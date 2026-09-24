from __future__ import annotations

from typing import Any

import asyncpg

from sales_backend.domain.agent import ActorContext
from sales_backend.repositories.attribute_overlay import overlay_opportunity_attributes
from sales_backend.repositories.collaboration import members_by_opportunity, scoped_opportunity_ids

NAME_CONFLICT_MESSAGE = "该客户已有同名商机，请选择已有商机，或在名称中补充部门、项目期次以区分"


async def opportunity_name_available(connection, customer_id, name, exclude_id=None):
    if not str(name or "").strip():
        raise ValueError("请填写商机名称")
    try:
        return await connection.fetchval(
            "SELECT security.opportunity_name_is_available($1::uuid,$2,$3::uuid)", customer_id, name, exclude_id
        )
    except asyncpg.InsufficientPrivilegeError as exc:
        raise ValueError("客户或商机不在当前权限范围内") from exc


class OpportunityRepository:
    async def list(
        self,
        connection: asyncpg.Connection,
        actor: ActorContext | None,
        *,
        customer_id: str | None,
        owner_name: str | None,
        probability: int | None,
        stage_code: str | None,
        close_from: Any,
        close_to: Any,
        limit: int | None,
        include_closed: bool = False,
        offset: int = 0,
        opportunity_id: str | None = None,
        scope: str | None = None,
        member_id: str | None = None,
        member_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        scoped_ids = None
        if actor and (scope or member_id or member_ids) and not customer_id and not opportunity_id:
            _, _, _, scoped = await scoped_opportunity_ids(connection, actor, scope, member_id, member_ids, permission='opportunity.read')
            scoped_ids = [r["id"] for r in scoped]
        rows = await connection.fetch(
            """
            SELECT o.id::text, o.customer_id::text, o.name, o.stage_code,
                   o.amount, o.currency, o.probability, o.expected_close_date,
                   o.expected_close_year,o.expected_close_quarter,o.original_owner_name,o.ownership_resolution,
                   o.status, o.version_no, o.partner_name, o.partner_id::text,
                   o.sales_channel, o.product_line, o.closed_at, o.source_code,
                   o.attributes, o.import_meta, o.created_at, o.updated_at,
                   COALESCE(c.name,security.customer_reference(o.customer_id)->>'name') AS customer_name,
                   o.owner_user_ref_id::text AS owner_id,o.owner_team_id::text AS team_id,
                   owner.display_name AS owner_name, team.name AS team_name,
                   security.can_manage_fde_members(o.id) AS can_manage_fde_members,
          security.authorization_opportunity('opportunity.update',o.id) AS can_edit,
          security.authorization_opportunity('opportunity.close',o.id) AS can_close,
          security.authorization_opportunity('opportunity.reopen',o.id) AS can_reopen
              FROM crm.opportunity o
              LEFT JOIN crm.customer c ON c.id = o.customer_id AND c.deleted_at IS NULL
              LEFT JOIN platform.user_ref owner ON owner.id = o.owner_user_ref_id
              LEFT JOIN platform.team team ON team.id = o.owner_team_id
             WHERE o.deleted_at IS NULL AND ($9::boolean OR o.status = 'open')
               AND ($5::boolean OR security.authorization_opportunity_direct('opportunity.read',o.id))
               AND ($12::uuid[] IS NULL OR o.id=ANY($12::uuid[]))
               AND ($1::text IS NULL OR o.customer_id = $1::uuid)
               AND ($2::text IS NULL OR owner.display_name = $2)
               AND ($3::integer IS NULL OR o.probability = $3)
               AND ($4::text IS NULL OR o.stage_code = $4)
               AND ($6::date IS NULL OR o.expected_close_date >= $6::date)
               AND ($7::date IS NULL OR o.expected_close_date <= $7::date)
               AND ($11::uuid IS NULL OR o.id=$11::uuid)
             ORDER BY o.expected_close_date NULLS LAST, o.amount DESC, o.updated_at DESC,o.id
             LIMIT $8 OFFSET $10
            """,
            customer_id,
            owner_name,
            probability,
            stage_code,
            bool(customer_id or opportunity_id),
            close_from,
            close_to,
            limit,
            include_closed,
            offset,
            opportunity_id,
            scoped_ids,
        )
        return await self.enrich(connection, rows)

    async def enrich(self, connection, rows):
        ids = [row["id"] for row in rows]
        fde_members = await members_by_opportunity(connection, ids)
        plans = (
            await connection.fetch(
                """SELECT opportunity_id::text,year,quarter,recognized_amount,collection_amount,collection_confidence
            FROM crm.opportunity_forecast WHERE opportunity_id=ANY($1::uuid[]) ORDER BY year,quarter""",
                ids,
            )
            if ids
            else []
        )
        actuals = (
            await connection.fetch(
                """SELECT opportunity_id::text,
            sum(amount) FILTER(WHERE kind='recognized') AS recognized_amount,
            sum(amount) FILTER(WHERE kind='collection') AS collection_amount
            FROM crm.customer_actual WHERE opportunity_id=ANY($1::uuid[]) AND voided_at IS NULL
            GROUP BY opportunity_id""",
                ids,
            )
            if ids
            else []
        )
        associated = (await connection.fetch(
            """SELECT related.opportunity_id::text,p.id::text,p.name
            FROM crm.opportunity_related_partner related JOIN crm.partner p ON p.id=related.partner_id
            WHERE related.opportunity_id=ANY($1::uuid[]) ORDER BY p.name,p.id""", ids
        )) if ids else []
        historical = (await connection.fetch(
            """SELECT opportunity_id::text,year,quarter,kind,raw_amount,source_unit,tax_basis,source_field
            FROM crm.opportunity_period_actual_snapshot WHERE opportunity_id=ANY($1::uuid[])
            ORDER BY year,quarter,kind,source_field,id""", ids
        )) if ids else []
        partners_by_id: dict[str, list] = {}
        for partner in associated:
            value = dict(partner)
            partners_by_id.setdefault(value.pop("opportunity_id"), []).append(value)
        historical_by_id: dict[str, list] = {}
        for period in historical:
            value = dict(period)
            historical_by_id.setdefault(value.pop("opportunity_id"), []).append(value)
        plans_by_id: dict[str, list] = {}
        for plan in plans:
            value = dict(plan)
            plans_by_id.setdefault(value.pop("opportunity_id"), []).append(value)
        actuals_by_id = {row["opportunity_id"]: dict(row) for row in actuals}
        result = []
        for row in rows:
            item = overlay_opportunity_attributes(dict(row))
            item["quarterly_forecasts"] = plans_by_id.get(item["id"], [])
            item["associated_partners"] = partners_by_id.get(item["id"], [])
            item["historical_period_actuals"] = historical_by_id.get(item["id"], [])
            actual = actuals_by_id.get(item["id"], {})
            item["actuals"] = {key: actual.get(key) for key in ("recognized_amount", "collection_amount")}
            item["fde_members"] = fde_members.get(item["id"], [])
            result.append(item)
        return result

    async def for_customer(self, connection, customer_id):
        """Internal customer projection; the request transaction's RLS remains authoritative."""
        return await self.list(
            connection,
            None,
            customer_id=customer_id,
            owner_name=None,
            probability=None,
            stage_code=None,
            close_from=None,
            close_to=None,
            limit=None,
            include_closed=True,
        )

    async def detail(self, connection, actor, opportunity_id):
        items = await self.list(
            connection,
            actor,
            customer_id=None,
            owner_name=None,
            probability=None,
            stage_code=None,
            close_from=None,
            close_to=None,
            limit=1,
            include_closed=True,
            opportunity_id=opportunity_id,
        )
        if not items:
            return None
        from sales_backend.repositories.customer_records import related_records

        item = items[0]
        return {
            "id": item["customer_id"],
            "name": item["customer_name"],
            "opportunities": [item],
            **await related_records(connection, item["customer_id"], opportunity_id=opportunity_id),
        }

    async def page(self, connection, actor, *, limit, offset=0, **filters):
        from sales_backend.repositories.opportunity_browse import browse_opportunities

        return await browse_opportunities(connection, actor, limit=limit, offset=offset, **filters)
