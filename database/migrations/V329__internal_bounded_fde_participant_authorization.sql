-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V181__bounded_fde_participant_authorization.sql.
-- Original SHA-256: fd81dee6b724c3b17b438ca2703ce054adae54ae7e61275601c49dfc868e0178. See shenma_whole_121_manifest.json.
BEGIN;

-- Restrict participant candidates to the current/related opportunity IDs before
-- checking FDE validity. The prior correlated OR/EXISTS can scan every tenant
-- participant for each map row. The ID array preserves the direct project plus
-- same-customer, non-deleted projects for read permission only, including the
-- existing NULL/direct-ID semantics. All grants, denies and live validity checks
-- remain unchanged; no cached identity or RLS bypass is introduced.

CREATE OR REPLACE FUNCTION security.authorization_opportunity(p_permission text,p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE((SELECT (CASE WHEN security.authorization_allows(p_permission,o.workspace_id,o.owner_user_ref_id,ARRAY[o.owner_team_id],o.owner_user_ref_id=common.current_user_ref_id()) THEN true ELSE security.authorization_allows(p_permission,o.workspace_id,o.owner_user_ref_id,
  ARRAY[o.owner_team_id] || ARRAY(
   SELECT DISTINCT tm.team_id FROM crm.opportunity_participant p
   JOIN platform.team_membership tm ON tm.user_ref_id=p.user_ref_id AND tm.workspace_id=p.workspace_id
   JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
   WHERE p.workspace_id=o.workspace_id AND p.opportunity_id=ANY(ARRAY(
    SELECT o.id UNION ALL SELECT related.id FROM crm.opportunity related
    WHERE p_permission='opportunity.read' AND related.customer_id=o.customer_id
     AND related.workspace_id=o.workspace_id AND related.deleted_at IS NULL))
    AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
    AND security.fde_user_is_active(p.user_ref_id)
    AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
    AND t.status='active' AND t.deleted_at IS NULL),
  o.owner_user_ref_id=common.current_user_ref_id() OR EXISTS(
   SELECT 1 FROM crm.opportunity_participant p JOIN platform.user_ref u ON u.id=p.user_ref_id AND u.workspace_id=p.workspace_id
   WHERE p.workspace_id=o.workspace_id AND p.user_ref_id=common.current_user_ref_id()
   AND u.status='active' AND u.deleted_at IS NULL
   AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
   AND p.opportunity_id=ANY(ARRAY(
    SELECT o.id UNION ALL SELECT related.id FROM crm.opportunity related
    WHERE p_permission='opportunity.read' AND related.customer_id=o.customer_id
     AND related.workspace_id=o.workspace_id AND related.deleted_at IS NULL)))) END)
 FROM crm.opportunity o WHERE o.id=p_opportunity AND o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL),false);
$$;

CREATE OR REPLACE FUNCTION security.authorization_visit(p_permission text,p_visit uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE((SELECT ((CASE WHEN security.authorization_allows(p_permission,v.workspace_id,v.recorder_user_ref_id,ARRAY[v.recorder_team_id],false) THEN true ELSE security.authorization_allows(p_permission,v.workspace_id,v.recorder_user_ref_id,
  ARRAY[v.recorder_team_id] || ARRAY(SELECT DISTINCT tm.team_id
   FROM crm.opportunity_participant p JOIN platform.team_membership tm
    ON tm.user_ref_id=p.user_ref_id AND tm.workspace_id=p.workspace_id
   JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
   WHERE p.opportunity_id=ANY(ARRAY(
     SELECT v.opportunity_id UNION ALL SELECT related.id FROM crm.opportunity related
     WHERE p_permission='visit.read' AND related.workspace_id=v.workspace_id
      AND related.customer_id=v.customer_id AND related.deleted_at IS NULL))
    AND p.workspace_id=v.workspace_id
    AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
    AND security.fde_user_is_active(p.user_ref_id) AND t.status='active' AND t.deleted_at IS NULL
    AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to),
  (p_permission<>'visit.supplement' OR (v.recorder_user_ref_id=common.current_user_ref_id()
   AND v.created_by_user_ref_id=common.current_user_ref_id() AND v.confirmed_by_user_ref_id=common.current_user_ref_id()
   AND v.status='archived')) AND (
   (p_permission='visit.read' AND v.recorder_user_ref_id=common.current_user_ref_id()) OR
   (p_permission<>'visit.supplement' AND EXISTS(SELECT 1 FROM activity.visit_participant p
     WHERE p.visit_id=v.id AND p.workspace_id=v.workspace_id AND p.user_ref_id=common.current_user_ref_id()))
   OR EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=v.opportunity_id AND o.workspace_id=v.workspace_id
     AND (o.owner_user_ref_id=common.current_user_ref_id() OR EXISTS(
      SELECT 1 FROM crm.opportunity_participant p WHERE p.opportunity_id=ANY(ARRAY(
        SELECT o.id UNION ALL SELECT related.id FROM crm.opportunity related
        WHERE p_permission='visit.read' AND related.workspace_id=o.workspace_id
         AND related.customer_id=o.customer_id AND related.deleted_at IS NULL))
       AND p.workspace_id=o.workspace_id
       AND p.user_ref_id=common.current_user_ref_id() AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to)))
  )) END) OR (p_permission IN ('battle_map.read','actual.read','overview.read')
  AND security.authorization_customer(p_permission,v.customer_id)))
 AND (v.opportunity_id IS NULL OR p_permission IN ('overview.read','dashboard.read','dashboard.ranking','profile.sales_read','profile.fde_read','profile.fde_activity','agent.chatbi','agent.customer_chatbi','agent.operating_report') OR security.has_opportunity_read_access(v.opportunity_id))
 FROM activity.visit v WHERE v.id=p_visit AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL),false);
$$;

COMMIT;
