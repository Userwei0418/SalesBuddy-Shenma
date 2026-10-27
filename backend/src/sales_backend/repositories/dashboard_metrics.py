# ruff: noqa: S608 -- selected_scope has only a fixed allowlisted opportunity alias.
"""One authorized unpaged quarterly row set for monitoring and its details."""

from sales_backend.repositories.dashboard import (scope_arguments, selected_scope, actual_selected_scope, unresolved_count_scope)


async def opportunity_rows(connection, actor, selection, *, year, quarter):
    records = await connection.fetch(
        """/* dashboard_metric_opportunities_v1 */
        SELECT o.id::text,o.version_no,o.name,o.customer_id::text,c.name AS customer_name,
          o.owner_user_ref_id::text AS owner_id,o.owner_team_id::text AS team_id,
          CASE WHEN security.authorization_subject('dashboard.read','person',owner.id,NULL)
            AND security.authorization_subject('opportunity.read','person',owner.id,NULL)
            THEN owner.display_name END AS owner_name,
          o.status,o.probability::text,o.amount::text,o.currency,
          f.id::text AS forecast_id,f.updated_at AS forecast_updated_at,
          f.collection_amount::text,f.recognized_amount::text,f.collection_confidence
        FROM crm.opportunity o
        JOIN crm.customer c ON c.workspace_id=o.workspace_id AND c.id=o.customer_id
        LEFT JOIN platform.user_ref owner ON owner.workspace_id=o.workspace_id AND owner.id=o.owner_user_ref_id
        LEFT JOIN crm.opportunity_forecast f ON f.workspace_id=o.workspace_id AND f.opportunity_id=o.id
          AND f.year=$6 AND f.quarter=$7
        WHERE o.workspace_id=common.current_workspace_id()
          AND o.deleted_at IS NULL AND c.deleted_at IS NULL AND o.status IN ('open','won')
          AND security.authorization_opportunity('opportunity.read',o.id)
          AND security.authorization_customer('customer.read',c.id)
          AND """ + selected_scope("o") + " ORDER BY o.id",
        *scope_arguments(actor, selection), year, quarter,
    )
    return [dict(row) for row in records]


async def subject_allowed(connection, permission, kind, subject_id=None):
    return bool(await connection.fetchval(
        "SELECT security.authorization_subject($1,$2,$3::uuid,$4::uuid)",
        permission, kind, subject_id if kind == "person" else None,
        subject_id if kind == "team" else None,
    ))


async def source_scope_complete(connection, selection, *, actual=False):
    subjects = (
        [("person", selection.member_id)] if selection.personal else
        [("team", team_id) for team_id in selection.team_ids] if selection.team_ids else
        [("department", None)]
    )
    for kind, subject_id in subjects:
        permissions = ("dashboard.read", "opportunity.read", "customer.read") + (("actual.read",) if actual else ())
        for permission in permissions:
            if not await subject_allowed(connection, permission, kind, subject_id):
                return False
    return True


async def targets_for_subject(connection, kind, subject_id, *, start):
    # Same target authorization as Review, with one additive net-actual kind.
    # Do not broaden Review's target list or conflate inclusive forecast goals.
    if kind in {"person", "team"}:
        person, team = (subject_id, None) if kind == "person" else (None, subject_id)
        allowed = await connection.fetchval(
            "SELECT security.authorization_subject('target.read',$1,$2::uuid,$3::uuid)"
            " AND security.target_scope_read($1,$2::uuid,$3::uuid,'sales')", kind, person, team,
        )
        if not allowed:
            return {}, False
        records = await connection.fetch(
            """SELECT id::text,kind,version_no,change_reason AS note,
              CASE WHEN (COALESCE(source,'') LIKE '%Q4目标表 revision54 导入%'
                         OR COALESCE(change_reason,'') LIKE '%Q4目标表 revision54 导入%')
                   THEN (amount*10000)::text ELSE amount::text END AS value
            FROM crm.sales_target WHERE workspace_id=common.current_workspace_id() AND period_type='quarter'
              AND period_start=$1 AND scope_type=$2 AND user_ref_id IS NOT DISTINCT FROM $3::uuid
              AND team_id IS NOT DISTINCT FROM $4::uuid AND department_code='sales'
              AND kind IN ('collection','collection_ppl','forecast_recognized','recognized')""",
            start, kind, person, team,
        )
        return {row["kind"]: dict(row) for row in records}, True
    allowed = await connection.fetchval(
        "SELECT security.authorization_subject('target.read','department',NULL,NULL)"
        " AND security.target_scope_read('department',NULL,NULL,'sales')"
    )
    if not allowed:
        return {}, False
    records = await connection.fetch(
        """/* dashboard_metric_department_targets_v1 */
        SELECT id::text,kind,version_no,change_reason AS note,
          CASE WHEN (COALESCE(source,'') LIKE '%Q4目标表 revision54 导入%'
                     OR COALESCE(change_reason,'') LIKE '%Q4目标表 revision54 导入%')
               THEN (amount*10000)::text ELSE amount::text END AS value
        FROM crm.sales_target
        WHERE workspace_id=common.current_workspace_id() AND period_type='quarter'
          AND period_start=$1 AND scope_type='department' AND user_ref_id IS NULL
          AND team_id IS NULL AND department_code='sales'
          AND kind IN ('collection','collection_ppl','forecast_recognized','recognized')""", start,
    )
    return {row["kind"]: dict(row) for row in records}, True


async def actual_rows(connection, actor, selection, *, year, quarter):
    """Selected adopted leaves; frozen attribution and current object access intersect.

    No opportunity status filter, material/FDE expansion, or forecast join: a
    closed opportunity's receipts and customer-only facts remain independent.
    """
    records = await connection.fetch(
        f"""/* dashboard_metric_actuals_v1 */
        SELECT a.id,a.customer_id::text,c.name AS customer_name,a.opportunity_id::text,
          o.name AS opportunity_name,a.material_line_id::text,a.year,a.quarter,a.kind,
          a.amount::text,a.raw_amount::text,a.tax_basis,a.tax_unresolved,a.source,a.status,
          a.occurred_on,a.period_start,a.period_end,a.period_precision,
          a.owner_user_ref_id::text AS owner_id,a.owner_team_id::text AS team_id,
          CASE WHEN security.authorization_subject('dashboard.read','person',owner.id,NULL)
            AND security.authorization_subject('actual.read','person',owner.id,NULL)
            THEN owner.display_name END AS owner_name,
          a.complete,a.missing_material_count,a.version_no,a.attribution_status,
          NOT {actual_selected_scope()} AS _attribution_excluded
        FROM crm.effective_actual a
        JOIN crm.customer c ON c.id=a.customer_id AND c.workspace_id=a.workspace_id AND c.deleted_at IS NULL
        LEFT JOIN crm.opportunity o ON o.id=a.opportunity_id AND o.workspace_id=a.workspace_id
          AND o.deleted_at IS NULL
        LEFT JOIN platform.user_ref owner ON owner.id=a.owner_user_ref_id AND owner.workspace_id=a.workspace_id
        CROSS JOIN LATERAL (SELECT a.owner_user_ref_id,a.owner_team_id) fact
        WHERE a.workspace_id=common.current_workspace_id() AND a.year=$6 AND a.quarter=$7
          AND (a.occurred_on IS NULL OR a.occurred_on <= timezone('Asia/Shanghai',transaction_timestamp())::date)
          AND security.authorization_customer('customer.read',a.customer_id)
          AND security.authorization_customer('actual.read',a.customer_id)
          AND (a.opportunity_id IS NULL OR (o.id IS NOT NULL
            AND security.authorization_opportunity('opportunity.read',o.id)
            AND security.authorization_opportunity('actual.read',o.id)))
          AND ({actual_selected_scope()} OR {unresolved_count_scope()}) ORDER BY a.year,a.quarter,a.kind,a.id""",
        *scope_arguments(actor, selection), year, quarter,
    )
    return [dict(row) for row in records]
