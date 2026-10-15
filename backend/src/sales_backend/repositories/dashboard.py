# ruff: noqa: S608 -- SQL fragments contain only allowlisted aliases; all values are parameterized.
"""Complete DB-backed dashboard facts. RLS and ownership both apply, never list-page samples."""


from sales_backend.repositories.dashboard_scope import resolve_selection
from sales_backend.domain.finance_totals import actual_totals
from collections import defaultdict
from sales_backend.repositories.opportunity_product_lines import attach_product_lines


def actual_detail(row):
    """Whitelisted leaves from the exact rows aggregated by actual_totals."""
    keys = ('id', 'customer_id', 'opportunity_id', 'material_line_id', 'year', 'quarter', 'kind',
            'amount', 'raw_amount', 'tax_basis', 'tax_unresolved', 'source', 'status', 'occurred_on',
            'period_start', 'period_end', 'period_precision', 'owner_user_ref_id', 'owner_team_id',
            'complete', 'missing_material_count', 'version_no')
    result = {key: row.get(key) for key in keys}
    for key in ('id', 'customer_id', 'opportunity_id', 'material_line_id', 'owner_user_ref_id', 'owner_team_id',
                'amount', 'raw_amount'):
        if result[key] is not None:
            result[key] = str(result[key])
    return result


def owner_scope(alias):
    if alias not in {"o", "c", "fact"}:
        raise ValueError("Unknown internal SQL alias")
    # $1 exact feature, $2 selected member, $3 authenticated tenant, $4 personal view.
    if alias in {'o', 'c'}:
        kind = 'opportunity' if alias == 'o' else 'customer'
        permitted = f"security.authorization_{kind}($1::text,{alias}.id)"
    else:
        permitted = """security.authorization_allows($1::text,$3::uuid,fact.owner_user_ref_id,ARRAY[fact.owner_team_id],
          EXISTS(SELECT 1 FROM crm.opportunity_participant p WHERE p.opportunity_id=o.id
          AND p.workspace_id=$3::uuid AND p.user_ref_id=common.current_user_ref_id()
          AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to))"""
    return f"""($3::uuid=common.current_workspace_id() AND {permitted}
      AND (NOT $4::boolean OR {alias}.owner_user_ref_id=$2::uuid))"""


def selected_scope(alias):
    return owner_scope(alias) + f" AND ($5::uuid[] IS NULL OR {alias}.owner_team_id=ANY($5::uuid[]))"


def scope_arguments(actor, selection):
    return (selection.permission, selection.member_id, actor.workspace_id, selection.personal,
            list(selection.team_ids) if selection.team_ids is not None else None)


class DashboardRepository:
    async def load(self, connection, actor, *, personal=False, member_id=None, team_groups=()):
        selection = await resolve_selection(
            connection, actor, personal=personal, member_id=member_id, team_groups=team_groups
        )
        personal = selection.personal
        args = scope_arguments(actor, selection)
        opportunities = await self.opportunities(connection, actor, selection=selection)
        forecasts = await self.forecasts(connection, actor, selection=selection)
        actual_rows = await connection.fetch(
            f"""SELECT a.*
            FROM crm.effective_actual a LEFT JOIN crm.customer c ON c.id=a.customer_id AND c.deleted_at IS NULL
            LEFT JOIN crm.opportunity o ON o.id=a.opportunity_id AND o.deleted_at IS NULL
            CROSS JOIN LATERAL (SELECT a.owner_user_ref_id,a.owner_team_id) fact
            WHERE security.customer_reference(a.customer_id) IS NOT NULL
              AND {selected_scope("fact")}
              AND (a.occurred_on IS NULL OR a.occurred_on <= timezone('Asia/Shanghai',clock_timestamp())::date)
            ORDER BY a.year,a.quarter,a.id""",
            *args,
        )
        periods = defaultdict(list)
        for row in actual_rows:
            periods[(row['year'],row['quarter'])].append(dict(row))
        actuals = [{'year':year,'quarter':quarter,**actual_totals(rows)} for (year,quarter),rows in periods.items()]
        customers = await connection.fetch(
            f"""SELECT c.id::text,c.name,c.source_code,c.primary_partner_name,t.region_code,
              c.owner_user_ref_id::text AS owner_id,t.name AS team
            FROM crm.customer c LEFT JOIN platform.team t ON t.id=c.owner_team_id
            WHERE c.deleted_at IS NULL AND {selected_scope("c")}""", *args
        )
        visits = await self.recent_visits(connection, actor, selection=selection)
        links = await connection.fetch(
            f"""SELECT cp.opportunity_id::text,p.name AS product_name
            FROM crm.customer_product cp JOIN crm.product p ON p.id=cp.product_id
            JOIN crm.opportunity o ON o.id=cp.opportunity_id AND o.deleted_at IS NULL
            WHERE cp.deleted_at IS NULL AND {selected_scope("o")} ORDER BY cp.updated_at DESC""", *args
        )
        as_of = await connection.fetchval("SELECT clock_timestamp()")
        scope = ("self" if selection.member_id == actor.user_id else "member") if personal else (
            "team" if selection.team_ids is not None else "authorized"
        )
        return dict(
            data_source="database",
            contract_version=4,
            forecast_basis='latest_sales_input',
            scope=scope,
            selection=dict(personal=personal, member_id=selection.member_id if personal else None,
                           team_groups=list(selection.team_groups)),
            as_of=as_of,
            opportunities=[dict(r) for r in opportunities],
            quarter_forecasts=[dict(r) for r in forecasts],
            quarter_actuals=[dict(r) for r in actuals],
            actual_details=[actual_detail(dict(r)) for r in actual_rows],
            detail_coverage={"actuals": {"complete": True, "count": len(actual_rows),
                                        "basis": "same_authorized_effective_actual_rows"},
                             "forecasts": {"complete": True, "count": len(forecasts),
                                           "basis": "latest_sales_input"}},
            customers=[dict(r) for r in customers],
            recent_visits=[dict(r) for r in visits],
            product_links=[dict(r) for r in links],
            summary=dict(source_date=as_of),
        )

    async def opportunities(self, connection, actor, *, personal=False, selection=None, permission="dashboard.read"):
        selection = selection or await resolve_selection(connection, actor, personal=personal, permission=permission)
        args = scope_arguments(actor, selection)
        rows = await connection.fetch(
            f"""SELECT o.id::text,o.customer_id::text,o.name,o.amount,o.probability,o.stage_code,
              o.expected_close_date,o.status,o.product_line,o.attributes,o.source_code,
              o.sales_channel,o.partner_id::text,o.partner_name,o.created_at,o.updated_at,o.closed_at,
              o.owner_user_ref_id::text AS owner_id,o.owner_team_id::text AS team_id,
              u.display_name AS owner_name,t.name AS team_name,
              COALESCE(c.name,security.customer_reference(o.customer_id)->>'name') AS customer_name
            FROM crm.opportunity o LEFT JOIN crm.customer c ON c.id=o.customer_id AND c.deleted_at IS NULL
            LEFT JOIN platform.user_ref u ON u.id=o.owner_user_ref_id
            LEFT JOIN platform.team t ON t.id=o.owner_team_id
            WHERE o.deleted_at IS NULL AND o.status='open' AND {selected_scope("o")}
            ORDER BY o.expected_close_date NULLS LAST,o.id""",
            *args,
        )
        return await attach_product_lines(connection, [dict(row) for row in rows])

    async def forecasts(self, connection, actor, *, personal=False, selection=None, permission="dashboard.read"):
        selection = selection or await resolve_selection(connection, actor, personal=personal, permission=permission)
        args = scope_arguments(actor, selection)
        rows = await connection.fetch(
            f"""SELECT f.year,f.quarter,f.recognized_amount::text AS recognized_amount,f.collection_amount::text AS collection_amount,
              o.id::text AS opportunity_id,o.name AS opportunity_name,o.customer_id::text,
              security.customer_reference(o.customer_id)->>'name' AS customer_name,o.status,o.probability,
              f.updated_at,
              o.owner_user_ref_id::text AS owner_id,o.owner_team_id::text AS team_id
            FROM crm.opportunity_forecast f JOIN crm.opportunity o ON o.id=f.opportunity_id
            WHERE o.deleted_at IS NULL AND {selected_scope("o")}
            ORDER BY f.year,f.quarter,o.id""",
            *args,
        )
        return [dict(row) for row in rows]

    async def recent_visits(self, connection, actor, *, personal=False, selection=None, permission="dashboard.read"):
        selection = selection or await resolve_selection(connection, actor, personal=personal, permission=permission)
        args = scope_arguments(actor, selection)
        rows = await connection.fetch(
            """SELECT v.id::text,v.customer_id::text,v.interaction_at,v.status,v.opportunity_id::text,
              v.recorder_team_id::text AS recorder_team_id,
              v.recorder_user_ref_id::text AS recorder_id,u.display_name AS recorder_name,
              team.name AS team_name,team.name AS recorder_team_name,
              COALESCE(c.name,security.customer_reference(v.customer_id)->>'name') AS customer_name
            FROM activity.visit v LEFT JOIN crm.customer c ON c.id=v.customer_id AND c.deleted_at IS NULL
            JOIN platform.user_ref u ON u.id=v.recorder_user_ref_id
            LEFT JOIN platform.team team ON team.id=v.recorder_team_id
            WHERE v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
              AND v.interaction_at >= (((statement_timestamp() AT TIME ZONE 'Asia/Shanghai')::date-6)
                ::timestamp AT TIME ZONE 'Asia/Shanghai')
              AND ($5::uuid[] IS NULL OR v.recorder_team_id=ANY($5::uuid[]))
              AND v.interaction_at < (((statement_timestamp() AT TIME ZONE 'Asia/Shanghai')::date+1)
                ::timestamp AT TIME ZONE 'Asia/Shanghai')
              AND $3::uuid=common.current_workspace_id() AND security.authorization_visit($1::text,v.id)
              AND (NOT $4::boolean OR v.recorder_user_ref_id=$2::uuid)
            ORDER BY v.interaction_at,v.id""",
            *args,
        )
        return [dict(row) for row in rows]
