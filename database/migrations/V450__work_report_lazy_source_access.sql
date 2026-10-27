-- Report-only planning fix. Keep every V399 source predicate and caller RLS.
-- SQL CASE plans all source subqueries (including finance) at function startup.
-- PL/pgSQL prepares only the branch actually used by each frozen dependency.
-- No shared authorization function, table policy, grant, or timeout is changed.
BEGIN;
CREATE OR REPLACE FUNCTION security.work_report_source_access(p_manifest jsonb,p_selected jsonb) RETURNS boolean
 LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE s jsonb; allowed boolean;
BEGIN
 SELECT security.authorization_has('work_report.read')
 AND jsonb_typeof(p_manifest)='array' AND jsonb_typeof(p_selected)='array'
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(p_selected) selected(source_type)
   WHERE selected.source_type NOT IN ('visit','opportunity','task','risk','actual')
      OR NOT security.authorization_has(selected.source_type||'.read')) INTO allowed;
 IF allowed IS NOT TRUE THEN RETURN allowed; END IF;
 FOR s IN SELECT value FROM jsonb_array_elements(p_manifest) LOOP
  CASE s->>'source_type'
   WHEN 'visit' THEN
    allowed := security.authorization_visit('visit.read',(s->>'record_id')::uuid)
     AND security.authorization_visit('work_report.read',(s->>'record_id')::uuid)
     AND EXISTS(SELECT 1 FROM activity.visit v WHERE v.id=(s->>'record_id')::uuid
       AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived'));
   WHEN 'opportunity' THEN
    allowed := security.authorization_opportunity('opportunity.read',(s->>'record_id')::uuid)
     AND security.authorization_opportunity('work_report.read',(s->>'record_id')::uuid)
     AND COALESCE((SELECT true FROM crm.opportunity v WHERE v.id=(s->>'record_id')::uuid
       AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL),false);
   WHEN 'task' THEN
    allowed := security.authorization_task('task.read',(s->>'record_id')::uuid)
     AND security.authorization_task('work_report.read',(s->>'record_id')::uuid)
     AND EXISTS(SELECT 1 FROM workflow.task v WHERE v.id=(s->>'record_id')::uuid
       AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL);
   WHEN 'risk' THEN
    allowed := security.authorization_risk('risk.read',(s->>'record_id')::uuid)
     AND security.authorization_risk('work_report.read',(s->>'record_id')::uuid)
     AND EXISTS(SELECT 1 FROM insight.risk v WHERE v.id=(s->>'record_id')::uuid
       AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL);
   WHEN 'actual' THEN
    allowed := EXISTS(SELECT 1 FROM crm.effective_actual e
     WHERE e.workspace_id=common.current_workspace_id() AND e.kind='collection'
       AND e.id=s->>'record_id' AND e.id=s->'actual_lineage'->>'effective_fact_id'
       AND e.customer_id=(s->'actual_lineage'->>'customer_id')::uuid
       AND e.opportunity_id IS NOT DISTINCT FROM (s->'actual_lineage'->>'opportunity_id')::uuid
       AND e.year=(s->'actual_lineage'->>'year')::integer AND e.quarter=(s->'actual_lineage'->>'quarter')::integer
       AND e.source=s->'actual_lineage'->>'source' AND e.status=s->'actual_lineage'->>'status'
       AND e.version_no=(s->'actual_lineage'->>'resolution_version')::integer
       AND e.source_fingerprint IS NOT DISTINCT FROM s->'actual_lineage'->>'source_fingerprint'
       AND e.amount IS NOT DISTINCT FROM (s->'actual_lineage'->>'amount')::numeric
       AND e.occurred_on IS NOT DISTINCT FROM (s->'actual_lineage'->>'occurred_on')::date
       AND e.period_precision=s->'actual_lineage'->>'period_precision'
       AND e.period_start=(s->'actual_lineage'->>'period_start')::date
       AND e.period_end=(s->'actual_lineage'->>'period_end')::date
       AND e.complete=(s->'actual_lineage'->>'complete')::boolean
       AND e.reference_changed=(s->'actual_lineage'->>'reference_changed')::boolean
       AND security.authorization_customer('actual.read',e.customer_id)
       AND security.authorization_customer('work_report.read',e.customer_id)
       AND (e.opportunity_id IS NULL OR (security.authorization_opportunity('actual.read',e.opportunity_id)
         AND security.authorization_opportunity('work_report.read',e.opportunity_id))));
   WHEN 'actual_period' THEN
    allowed := EXISTS(SELECT 1 FROM crm.effective_actual e
     WHERE e.workspace_id=common.current_workspace_id() AND e.kind='collection'
       AND e.id=s->'period_dependency'->>'effective_fact_id'
       AND e.customer_id=(s->'period_dependency'->>'customer_id')::uuid
       AND e.opportunity_id IS NOT DISTINCT FROM (s->'period_dependency'->>'opportunity_id')::uuid
       AND COALESCE(e.opportunity_id,e.customer_id)=(s->>'record_id')::uuid
       AND e.year=(s->'period_dependency'->>'year')::integer AND e.quarter=(s->'period_dependency'->>'quarter')::integer
       AND e.version_no=(s->'period_dependency'->>'resolution_version')::integer
       AND e.status=s->'period_dependency'->>'status'
       AND e.source_fingerprint IS NOT DISTINCT FROM s->'period_dependency'->>'source_fingerprint'
       AND security.authorization_customer('actual.read',e.customer_id)
       AND security.authorization_customer('work_report.read',e.customer_id)
       AND (e.opportunity_id IS NULL OR (security.authorization_opportunity('actual.read',e.opportunity_id)
         AND security.authorization_opportunity('work_report.read',e.opportunity_id))));
   WHEN 'opportunity_history' THEN
    allowed := (s->>'record_id')::uuid=(s->'history_guard'->>'opportunity_id')::uuid
     AND security.work_report_history_access(s->'history_guard');
   WHEN 'meeting_claim' THEN
    allowed := EXISTS(SELECT 1 FROM crm.customer_ownership_event e
     JOIN crm.customer_claim_request r ON r.id=e.claim_request_id AND r.workspace_id=e.workspace_id
     WHERE e.id=(s->>'record_id')::uuid AND e.workspace_id=common.current_workspace_id()
       AND e.customer_id=(s->>'customer_id')::uuid AND e.event_type='approved' AND r.status='approved'
       AND r.customer_id=e.customer_id AND r.applicant_user_ref_id=e.owner_user_ref_id
       AND security.authorization_customer('customer.read',e.customer_id)
       AND security.authorization_customer('work_report.read',e.customer_id));
   WHEN 'meeting_customer' THEN
    allowed := COALESCE((SELECT true FROM crm.customer c
     WHERE c.id=(s->>'record_id')::uuid AND c.workspace_id=common.current_workspace_id()
       AND c.deleted_at IS NULL AND security.authorization_customer('customer.read',c.id)
       AND security.authorization_customer('work_report.read',c.id)),false);
   WHEN 'meeting_state' THEN
    allowed := EXISTS(SELECT 1 FROM crm.opportunity_state_event e
     WHERE e.id=(s->>'record_id')::uuid AND e.workspace_id=common.current_workspace_id()
       AND e.opportunity_id=(s->>'opportunity_id')::uuid
       AND security.authorization_opportunity('opportunity.read',e.opportunity_id)
       AND security.authorization_opportunity('work_report.read',e.opportunity_id));
   WHEN 'meeting_forecast' THEN
    allowed := EXISTS(SELECT 1 FROM crm.opportunity_forecast_event e
     WHERE e.id=(s->>'record_id')::uuid AND e.workspace_id=common.current_workspace_id()
       AND e.opportunity_id=(s->>'opportunity_id')::uuid
       AND security.authorization_opportunity('opportunity.read',e.opportunity_id)
       AND security.authorization_opportunity('work_report.read',e.opportunity_id));
   WHEN 'meeting_member' THEN
    allowed := EXISTS(SELECT 1 FROM platform.user_ref u
     WHERE u.id=(s->>'record_id')::uuid AND u.workspace_id=common.current_workspace_id()
       AND security.authorization_subject('work_report.read','person',u.id,NULL));
   WHEN 'review_subject' THEN
    allowed := security.work_report_review_access(s);
   WHEN 'review_target' THEN
    allowed := security.work_report_review_access(s);
   WHEN 'review_objective' THEN
    allowed := security.work_report_review_access(s);
   WHEN 'review_key_result' THEN
    allowed := security.work_report_review_access(s);
   WHEN 'review_actual' THEN
    allowed := security.work_report_review_access(s);
   ELSE RETURN false;
  END CASE;
  IF allowed IS NOT TRUE THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;
COMMIT;
