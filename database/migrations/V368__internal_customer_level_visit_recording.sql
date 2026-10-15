-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V220__customer_level_visit_recording.sql.
-- Original SHA-256: efbd8c39628510dc35026831baa179b7b48225b4fe264ecdd0ff23f8d60f7bed. See shenma_whole_121_manifest.json.
BEGIN;
-- WEB007: the absence of an opportunity does not invent a new write grant.
-- Only existing visit actions gain an assigned customer target; all explicit
-- grants/denies, tenant/reference checks and selected-opportunity paths stay.
-- Replacing these same signatures retains existing EXECUTE privileges.

CREATE OR REPLACE FUNCTION security.authorization_visit_target(p_permission text,p_customer uuid,p_opportunity uuid,
 p_recorder uuid,p_team uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.customer_reference(p_customer) IS NOT NULL
 AND (p_opportunity IS NULL OR EXISTS(SELECT 1 FROM crm.opportunity o
  WHERE o.id=p_opportunity AND o.workspace_id=common.current_workspace_id() AND o.customer_id=p_customer
  AND o.deleted_at IS NULL AND security.has_opportunity_read_access(o.id)))
 AND security.authorization_allows(p_permission,common.current_workspace_id(),p_recorder,ARRAY[p_team],
  (p_opportunity IS NULL AND p_permission IN ('visit.create','visit.structure','visit.quality_review',
    'visit.first_visit','visit.attendance_manage') AND security.authorization_customer(p_permission,p_customer))
  OR p_opportunity IS NOT NULL AND EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=p_opportunity
   AND o.workspace_id=common.current_workspace_id() AND (o.owner_user_ref_id=common.current_user_ref_id()
    OR EXISTS(SELECT 1 FROM crm.opportunity_participant p WHERE p.opportunity_id=o.id
     AND p.workspace_id=o.workspace_id AND p.user_ref_id=common.current_user_ref_id()
     AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to))));
$$;

-- Customer-only archived supplements remain the original recorder/creator/
-- confirmer's action and must still have that customer-level supplement grant.
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
  )) END) OR (p_permission='visit.supplement' AND v.opportunity_id IS NULL
  AND v.customer_id IS NOT NULL AND v.status='archived'
  AND v.recorder_user_ref_id=common.current_user_ref_id()
  AND v.created_by_user_ref_id=common.current_user_ref_id()
  AND v.confirmed_by_user_ref_id=common.current_user_ref_id()
  AND security.authorization_customer(p_permission,v.customer_id))
 OR (p_permission IN ('battle_map.read','actual.read','overview.read')
  AND security.authorization_customer(p_permission,v.customer_id)))
 AND (v.opportunity_id IS NULL OR p_permission IN ('overview.read','dashboard.read','dashboard.ranking','profile.sales_read','profile.fde_read','profile.fde_activity','agent.chatbi','agent.customer_chatbi','agent.operating_report') OR security.has_opportunity_read_access(v.opportunity_id))
 FROM activity.visit v WHERE v.id=p_visit AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL),false);
$$;

INSERT INTO ops.schema_migration(version,description) VALUES('V368','Customer-level visit recording under current scoped grants');
COMMIT;
