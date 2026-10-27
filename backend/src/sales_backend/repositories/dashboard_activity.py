# ruff: noqa: S608 -- selected_scope is a fixed internal predicate; values are bound.
"""Active-quarter object scope first; existing visit authority supplies dates only."""

from sales_backend.repositories.dashboard import scope_arguments, selected_scope


async def activity_rows(connection, actor, selected, *, year, quarter, now):
    records = await connection.fetch(
        """/* dashboard_activity_objects_v1 */
        SELECT o.id::text,o.name,o.customer_id::text,c.name AS customer_name,
          o.owner_user_ref_id::text AS owner_id,o.owner_team_id::text AS team_id,
          CASE WHEN security.authorization_subject('dashboard.read','person',owner.id,NULL)
            AND security.authorization_subject('opportunity.read','person',owner.id,NULL)
            THEN owner.display_name END AS owner_name,
          o.stage_code,o.probability::text,o.status,o.currency,
          f.collection_amount::text,f.recognized_amount::text
        FROM crm.opportunity o
        JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
        JOIN crm.opportunity_forecast f ON f.opportunity_id=o.id AND f.workspace_id=o.workspace_id
          AND f.year=$6 AND f.quarter=$7
        LEFT JOIN platform.user_ref owner ON owner.id=o.owner_user_ref_id AND owner.workspace_id=o.workspace_id
        WHERE o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL AND c.deleted_at IS NULL
          AND (f.collection_amount>0 OR f.recognized_amount>0)
          AND security.authorization_opportunity('opportunity.read',o.id)
          AND security.authorization_customer('customer.read',c.id)
          AND """ + selected_scope("o") + " ORDER BY o.id",
        *scope_arguments(actor, selected), year, quarter,
    )
    rows = [dict(row) for row in records]
    if not rows:
        return []
    # V292 claim-based formal-visit reads are intentionally limited to their
    # actual read feature. Never change the dashboard object query's feature.
    # Date metadata uses only the preceding authorized IDs, in this same
    # read-only repeatable-read transaction; original route context is restored.
    previous = await connection.fetchval("SELECT current_setting('app.authorized_feature',true)")
    try:
        # A savepoint permits feature restoration even after a SQL error.
        async with connection.transaction():
            await connection.execute("SELECT set_config('app.authorized_feature','visit.read',true)")
            dates = await connection.fetch(
                """/* dashboard_activity_dates_v1 */
                WITH authorized AS MATERIALIZED (
                  SELECT id,customer_id FROM unnest($1::uuid[],$2::uuid[]) AS a(id,customer_id)
                ), authorized_links AS MATERIALIZED (
                  SELECT vo.visit_id,vo.opportunity_id,vo.workspace_id FROM activity.visit_opportunity vo
                  JOIN authorized o ON o.id=vo.opportunity_id
                  WHERE vo.workspace_id=common.current_workspace_id()
                ), visible_visits AS MATERIALIZED (
                  SELECT v.id,v.workspace_id,v.opportunity_id,v.interaction_at
                  FROM activity.visit v WHERE v.workspace_id=common.current_workspace_id()
                    AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
                    AND v.interaction_at<=$3::timestamptz
                    AND (v.opportunity_id IN (SELECT id FROM authorized)
                      OR v.id IN (SELECT visit_id FROM authorized_links))
                    AND security.authorization_visit('visit.read',v.id)
                ), targets AS (
                  SELECT v.id AS visit_id,o.id AS opportunity_id FROM visible_visits v
                  JOIN authorized o ON o.id=v.opportunity_id
                  UNION
                  SELECT v.id,vo.opportunity_id FROM visible_visits v
                  JOIN authorized_links vo ON vo.visit_id=v.id AND vo.workspace_id=v.workspace_id
                ), latest AS (
                  SELECT t.opportunity_id,max(v.interaction_at) AS last_follow_up_at
                  FROM targets t JOIN visible_visits v ON v.id=t.visit_id GROUP BY t.opportunity_id
                ) SELECT o.id::text,latest.last_follow_up_at,
                  (security.authorization_subject('visit.read','department',NULL,NULL)
                    OR security.customer_claim_visit_reference(o.customer_id,o.id) IS NOT NULL) AS follow_up_complete
                FROM authorized o LEFT JOIN latest ON latest.opportunity_id=o.id ORDER BY o.id""",
                [row["id"] for row in rows], [row["customer_id"] for row in rows], now,
            )
    finally:
        await connection.execute("SELECT set_config('app.authorized_feature',$1,true)", previous or "")
    by_id = {row["id"]: dict(row) for row in dates}
    return [dict(row, last_follow_up_at=by_id.get(row["id"], {}).get("last_follow_up_at"),
                 follow_up_complete=by_id.get(row["id"], {}).get("follow_up_complete", False)) for row in rows]
