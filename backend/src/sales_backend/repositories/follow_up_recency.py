# ruff: noqa: S608 -- Relation names are allowlisted internal constants; no request interpolation.
"""Batch formal follow-up facts under the calling API role's existing visit RLS.

The caller supplies an already-authorized candidate relation. Never move these
reads inside a SECURITY DEFINER directory projection: its owner can bypass RLS.
"""


def opportunity_recency_ctes(candidates: str) -> str:
    # Relation names are internal constants, never request input.
    if candidates not in {"filtered", "candidates"}:
        raise ValueError("Unknown internal candidate relation")
    return f""", recency_visible_visits AS MATERIALIZED (
      SELECT v.id,v.workspace_id,v.opportunity_id,v.interaction_at
      FROM activity.visit v
      WHERE v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL
        AND v.status IN ('confirmed','archived') AND v.interaction_at IS NOT NULL
    ), recency_visit_links AS MATERIALIZED (
      SELECT vo.visit_id,vo.opportunity_id,vo.workspace_id
      FROM activity.visit_opportunity vo
      WHERE vo.workspace_id=common.current_workspace_id()
        AND vo.opportunity_id IN (SELECT id FROM {candidates})
    ), recency_targets AS MATERIALIZED (
      SELECT v.id,v.interaction_at,c.id AS opportunity_id
      FROM recency_visible_visits v JOIN {candidates} c ON c.id=v.opportunity_id
      UNION
      SELECT v.id,v.interaction_at,vo.opportunity_id
      FROM recency_visible_visits v JOIN recency_visit_links vo
        ON vo.visit_id=v.id AND vo.workspace_id=v.workspace_id
    ), recency_latest AS MATERIALIZED (
      SELECT opportunity_id,max(interaction_at) AS latest_follow_up_at
      FROM recency_targets GROUP BY opportunity_id
    ), recency_ranked AS MATERIALIZED (
      SELECT c.*,r.latest_follow_up_at FROM {candidates} c
      LEFT JOIN recency_latest r ON r.opportunity_id=c.id
    )"""
