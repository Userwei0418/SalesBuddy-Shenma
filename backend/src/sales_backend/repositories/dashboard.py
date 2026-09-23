# ruff: noqa: S608 -- SQL fragments contain only allowlisted aliases; all values are parameterized.
"""Complete DB-backed dashboard facts. RLS and ownership both apply, never list-page samples."""


from sales_backend.repositories.dashboard_scope import resolve_selection


def owner_scope(alias):
    if alias not in {"o", "c", "fact"}:
        raise ValueError("Unknown internal SQL alias")
    # $1 role, $2 actor UUID, $3 allowed teams, $4 personal view.
    fde_oid = f"{alias}.id" if alias == "o" else "o.id"
    fde = (f"security.fde_customer_in_scope({alias}.id)" if alias == "c" else
           f"""(security.fde_opportunity_in_scope({fde_oid}) AND (NOT $4::boolean OR EXISTS(
             SELECT 1 FROM crm.opportunity_participant fp WHERE fp.opportunity_id={fde_oid}
             AND fp.user_ref_id=$2::uuid AND fp.participant_role='fde'
             AND clock_timestamp()>=fp.valid_from AND clock_timestamp()<fp.valid_to)))""")
    return f"""(($1::text IN('fde','fde_lead') AND {fde}) OR
      ($4::boolean AND {alias}.owner_user_ref_id=$2::uuid) OR
      (NOT $4::boolean AND ($1::text='manager' OR
        ($1='supervisor' AND {alias}.owner_team_id=ANY($3::uuid[])) OR
        ($1='sales' AND {alias}.owner_user_ref_id=$2::uuid))))"""


def selected_scope(alias):
    return owner_scope(alias) + f" AND ($5::uuid[] IS NULL OR {alias}.owner_team_id=ANY($5::uuid[]))"


def scope_arguments(actor, selection):
    return (actor.role.value, selection.member_id, list(actor.team_ids), selection.personal,
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
        actuals = await connection.fetch(
            f"""SELECT extract(year FROM a.occurred_on)::int AS year,
              extract(quarter FROM a.occurred_on)::int AS quarter,
              sum(a.amount) FILTER(WHERE a.kind='recognized') AS recognized_amount,
              sum(a.amount) FILTER(WHERE a.kind='collection') AS collection_amount,
              count(*) FILTER(WHERE a.kind='recognized') AS recognized_count,
              count(*) FILTER(WHERE a.kind='collection') AS collection_count
            FROM crm.customer_actual a LEFT JOIN crm.customer c ON c.id=a.customer_id AND c.deleted_at IS NULL
            LEFT JOIN crm.opportunity o ON o.id=a.opportunity_id AND o.deleted_at IS NULL
            CROSS JOIN LATERAL (SELECT COALESCE(o.owner_user_ref_id,c.owner_user_ref_id) AS owner_user_ref_id,
              COALESCE(o.owner_team_id,c.owner_team_id) AS owner_team_id) fact
            WHERE a.voided_at IS NULL AND security.customer_reference(a.customer_id) IS NOT NULL
              AND {selected_scope("fact")}
              AND a.occurred_on <= timezone('Asia/Shanghai',clock_timestamp())::date
            GROUP BY 1,2 ORDER BY 1,2""",
            *args,
        )
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
            "team" if selection.team_ids is not None else actor.data_scope.value
        )
        return dict(
            data_source="database",
            contract_version=3,
            forecast_basis='filled_quarter_plan_times_probability',
            scope=scope,
            selection=dict(personal=personal, member_id=selection.member_id if personal else None,
                           team_groups=list(selection.team_groups)),
            as_of=as_of,
            opportunities=[dict(r) for r in opportunities],
            quarter_forecasts=[dict(r) for r in forecasts],
            quarter_actuals=[dict(r) for r in actuals],
            customers=[dict(r) for r in customers],
            recent_visits=[dict(r) for r in visits],
            product_links=[dict(r) for r in links],
            summary=dict(source_date=as_of),
        )

    async def opportunities(self, connection, actor, *, personal=False, selection=None):
        selection = selection or await resolve_selection(connection, actor, personal=personal)
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
        return [dict(row) for row in rows]

    async def forecasts(self, connection, actor, *, personal=False, selection=None):
        selection = selection or await resolve_selection(connection, actor, personal=personal)
        args = scope_arguments(actor, selection)
        rows = await connection.fetch(
            f"""SELECT f.year,f.quarter,f.recognized_amount,f.collection_amount,
              o.id::text AS opportunity_id,o.customer_id::text,o.status,o.probability,
              (o.status IN ('open','won') AND o.probability>=10) AS forecast_eligible,
              CASE WHEN o.status IN ('open','won') AND o.probability>=10 THEN
                round(f.recognized_amount * o.probability / 100,2) END AS weighted_recognized_amount,
              CASE WHEN o.status IN ('open','won') AND o.probability>=10 THEN
                round(f.collection_amount * o.probability / 100,2) END AS weighted_collection_amount,
              o.owner_user_ref_id::text AS owner_id
            FROM crm.opportunity_forecast f JOIN crm.opportunity o ON o.id=f.opportunity_id
            WHERE o.deleted_at IS NULL AND {selected_scope("o")}
            ORDER BY f.year,f.quarter,o.id""",
            *args,
        )
        return [dict(row) for row in rows]

    async def recent_visits(self, connection, actor, *, personal=False, selection=None):
        selection = selection or await resolve_selection(connection, actor, personal=personal)
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
              AND (($4::boolean AND v.recorder_user_ref_id=$2::uuid) OR
                (NOT $4::boolean AND ($1::text='manager' OR
                  ($1='supervisor' AND v.recorder_team_id=ANY($3::uuid[])) OR
                  ($1='sales' AND v.recorder_user_ref_id=$2::uuid))))
            ORDER BY v.interaction_at,v.id""",
            *args,
        )
        return [dict(row) for row in rows]
