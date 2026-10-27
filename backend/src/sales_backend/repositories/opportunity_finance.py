"""RLS financial evidence read in one statement snapshot, never via worker powers."""

from urllib.parse import urlencode
import json

from sales_backend.db import json_value
from sales_backend.domain.opportunity_finance import financial_overview
from sales_backend.repositories.authorization_checks import require_permission


class OpportunityFinanceRepository:
    async def read(self, connection, opportunity_id, *, year=None, quarter=None):
        # Resolve the parent through the caller's existing scope before checking
        # financial access; absent/hidden parents have the same 404 response.
        if not await connection.fetchrow(
            """SELECT o.id AS opportunity_id FROM crm.opportunity o
          WHERE o.id=$1::uuid AND o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL
            AND security.authorization_opportunity('opportunity.read',o.id)""",
            str(opportunity_id),
        ):
            raise LookupError("商机不存在或不在财务查看范围")
        await require_permission(connection, "actual.read", opportunity_id=opportunity_id)
        row = await connection.fetchrow(
            """SELECT o.id::text,o.workspace_id::text,o.customer_id::text,o.name,o.version_no,
          w.external_workspace_id AS company_code,
          security.management_actor() AND security.authorization_has('access.console')
            AND security.authorization_customer('actual.create',o.customer_id)
            AND security.authorization_opportunity('actual.create',o.id) AS can_confirm,
          COALESCE((SELECT jsonb_agg(jsonb_build_object(
            'id',a.id,'opportunity_id',a.opportunity_id,'kind',a.kind,'amount',a.amount::text,
            'tax_basis',a.tax_basis,'occurred_on',a.occurred_on,'voided_at',a.voided_at,'void_reason',a.void_reason,
            'source_ref',a.source_ref,'created_at',a.created_at,
            'updated_at',COALESCE(a.voided_at,a.created_at)) ORDER BY a.id)
            FROM crm.customer_actual a WHERE a.workspace_id=o.workspace_id
              AND a.opportunity_id=o.id),'[]') AS finance_actuals,
          COALESCE((SELECT jsonb_agg(jsonb_build_object(
            'id',h.id,'opportunity_id',h.opportunity_id,'year',h.year,'quarter',h.quarter,'kind',h.kind,
            'raw_amount',h.raw_amount::text,'source_unit',h.source_unit,'tax_basis',h.tax_basis,
            'source_field',h.source_field,'source_record_id',h.source_record_id,
            'import_batch_id',h.import_batch_id) ORDER BY h.id)
            FROM crm.opportunity_period_actual_snapshot h WHERE h.workspace_id=o.workspace_id
              AND h.opportunity_id=o.id),'[]') AS finance_history,
          COALESCE((SELECT jsonb_agg(to_jsonb(e)||jsonb_build_object('amount',e.amount::text,'raw_amount',e.raw_amount::text)
            ORDER BY e.year,e.quarter,e.kind,e.id) FROM crm.effective_actual e
            WHERE e.workspace_id=o.workspace_id AND e.opportunity_id=o.id),'[]') AS finance_effective,
          COALESCE((SELECT jsonb_agg(to_jsonb(a)||jsonb_build_object('amount',a.amount::text,'confirmed_by_name',u.display_name)
            ORDER BY a.year,a.quarter,a.kind,a.id) FROM crm.opportunity_actual_confirmation a LEFT JOIN platform.user_ref u
            ON u.id=a.confirmed_by_user_ref_id AND u.workspace_id=a.workspace_id
            WHERE a.workspace_id=o.workspace_id AND a.opportunity_id=o.id),'[]')::text AS finance_confirmations,
          COALESCE((SELECT jsonb_agg(to_jsonb(b)||jsonb_build_object('decided_by_name',u.display_name)
            ORDER BY b.year,b.quarter,b.kind)
            FROM crm.opportunity_actual_basis b LEFT JOIN platform.user_ref u
              ON u.workspace_id=b.workspace_id AND u.id=b.decided_by_user_ref_id
            WHERE b.workspace_id=o.workspace_id AND b.opportunity_id=o.id),'[]') AS finance_decisions
          FROM crm.opportunity o JOIN platform.workspace w ON w.id=o.workspace_id
          WHERE o.id=$1::uuid AND o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL
            AND security.authorization_opportunity('opportunity.read',o.id)
            AND security.authorization_customer('actual.read',o.customer_id)""",
            str(opportunity_id),
        )
        if not row:
            raise LookupError("商机不存在或不在财务查看范围")
        raw = dict(row)
        # Preserve the immutable material snapshot's numeric prices and quantities
        # before the generic asyncpg JSON codec can round them to binary floats.
        raw['finance_confirmations'] = json.loads(raw['finance_confirmations'], parse_float=str)
        for key in ("finance_actuals", "finance_history", "finance_decisions", "finance_effective"):
            raw[key] = json_value(raw[key])
        if year is not None:
            raw['requested_period'] = (year, quarter)
        return {
            **financial_overview(raw),
            "name": raw["name"],
            "can_confirm": raw["can_confirm"],
            "customer_id": raw["customer_id"],
            "review_path": "/admin?"
            + urlencode({"company": raw["company_code"], "finance": raw["id"]})
            + "#opportunities",
        }

    async def lock_for_confirmation(self, connection, opportunity_id):
        if not await connection.fetchval(
            "SELECT security.management_actor() AND security.authorization_has('access.console')"
        ):
            raise PermissionError("仅本公司管理员或运营可确认财务来源")
        await connection.execute("SELECT security.finance_write_lock()")
        row = await connection.fetchrow(
            """SELECT id::text,customer_id::text,version_no,trade_relation FROM crm.opportunity
          WHERE id=$1::uuid AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL FOR NO KEY UPDATE""",
            str(opportunity_id),
        )
        if not row:
            raise LookupError("商机不存在")
        await require_permission(connection, "actual.create", customer_id=row["customer_id"])
        await require_permission(connection, "actual.create", opportunity_id=opportunity_id)
        return row
