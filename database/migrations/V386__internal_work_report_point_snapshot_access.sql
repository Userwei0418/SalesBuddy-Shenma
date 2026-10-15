-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V238__work_report_point_snapshot_access.sql.
-- Original SHA-256: 529c8933a5ecf54e8590a357873d2e7adc598beb3ca55599f72774e3c41ca030. See shenma_whole_121_manifest.json.
BEGIN;
-- Keep caller RLS and every existing authorization predicate. A run requires
-- only its own snapshot. EXISTS was planned as a hashed scan of all five owner
-- snapshots in the saved API profile; a scalar subquery retains the target key.
-- work_report_snapshot.id is a primary key, so the subquery returns 0/1 rows.
-- No row -> NULL -> false preserves the existing deny semantics.

ALTER POLICY report_run_read ON agent.work_report_run USING(
 owner_user_ref_id=common.current_user_ref_id() AND COALESCE((SELECT true FROM agent.work_report_snapshot s
 WHERE s.id=snapshot_id AND s.workspace_id=work_report_run.workspace_id),false));

ALTER POLICY report_run_insert ON agent.work_report_run WITH CHECK(
 owner_user_ref_id=common.current_user_ref_id() AND status='queued'
 AND security.authorization_has('work_report.write') AND security.authorization_has('agent.work_report')
 AND COALESCE((SELECT true FROM agent.work_report_snapshot s WHERE s.id=snapshot_id
 AND s.workspace_id=work_report_run.workspace_id AND s.status='ready'),false));

-- Saved source-access plan: customer checked 41 rows for one dependency;
-- opportunity checked 86 rows for two. Preserve every predicate and all other
-- ten source branches; each changed source table has a primary key on id.
CREATE OR REPLACE FUNCTION security.work_report_source_access(p_manifest jsonb,p_selected jsonb) RETURNS boolean
 LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT security.authorization_has('work_report.read')
 AND jsonb_typeof(p_manifest)='array' AND jsonb_typeof(p_selected)='array'
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(p_selected) s
   WHERE s NOT IN ('visit','opportunity','task','risk','actual') OR NOT security.authorization_has(s||'.read'))
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_manifest) s WHERE NOT COALESCE(CASE s->>'source_type'
   WHEN 'visit' THEN security.authorization_visit('visit.read',(s->>'record_id')::uuid)
     AND security.authorization_visit('work_report.read',(s->>'record_id')::uuid)
     AND EXISTS(SELECT 1 FROM activity.visit v WHERE v.id=(s->>'record_id')::uuid
       AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived'))
   WHEN 'opportunity' THEN security.authorization_opportunity('opportunity.read',(s->>'record_id')::uuid)
     AND security.authorization_opportunity('work_report.read',(s->>'record_id')::uuid)
     AND COALESCE((SELECT true FROM crm.opportunity v WHERE v.id=(s->>'record_id')::uuid
       AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL),false)
   WHEN 'task' THEN security.authorization_task('task.read',(s->>'record_id')::uuid)
     AND security.authorization_task('work_report.read',(s->>'record_id')::uuid)
     AND EXISTS(SELECT 1 FROM workflow.task v WHERE v.id=(s->>'record_id')::uuid
       AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL)
   WHEN 'risk' THEN security.authorization_risk('risk.read',(s->>'record_id')::uuid)
     AND security.authorization_risk('work_report.read',(s->>'record_id')::uuid)
     AND EXISTS(SELECT 1 FROM insight.risk v WHERE v.id=(s->>'record_id')::uuid
       AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL)
   WHEN 'actual' THEN EXISTS(SELECT 1 FROM crm.effective_actual e
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
         AND security.authorization_opportunity('work_report.read',e.opportunity_id))))
   WHEN 'actual_period' THEN EXISTS(SELECT 1 FROM crm.effective_actual e
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
         AND security.authorization_opportunity('work_report.read',e.opportunity_id))))
   WHEN 'opportunity_history' THEN (s->>'record_id')::uuid=(s->'history_guard'->>'opportunity_id')::uuid
     AND security.work_report_history_access(s->'history_guard')
   WHEN 'meeting_claim' THEN EXISTS(SELECT 1 FROM crm.customer_ownership_event e
     JOIN crm.customer_claim_request r ON r.id=e.claim_request_id AND r.workspace_id=e.workspace_id
     WHERE e.id=(s->>'record_id')::uuid AND e.workspace_id=common.current_workspace_id()
       AND e.customer_id=(s->>'customer_id')::uuid AND e.event_type='approved' AND r.status='approved'
       AND r.customer_id=e.customer_id AND r.applicant_user_ref_id=e.owner_user_ref_id
       AND security.authorization_customer('customer.read',e.customer_id)
       AND security.authorization_customer('work_report.read',e.customer_id))
   WHEN 'meeting_customer' THEN COALESCE((SELECT true FROM crm.customer c
     WHERE c.id=(s->>'record_id')::uuid AND c.workspace_id=common.current_workspace_id()
       AND c.deleted_at IS NULL AND security.authorization_customer('customer.read',c.id)
       AND security.authorization_customer('work_report.read',c.id)),false)
   WHEN 'meeting_state' THEN EXISTS(SELECT 1 FROM crm.opportunity_state_event e
     WHERE e.id=(s->>'record_id')::uuid AND e.workspace_id=common.current_workspace_id()
       AND e.opportunity_id=(s->>'opportunity_id')::uuid
       AND security.authorization_opportunity('opportunity.read',e.opportunity_id)
       AND security.authorization_opportunity('work_report.read',e.opportunity_id))
   WHEN 'meeting_forecast' THEN EXISTS(SELECT 1 FROM crm.opportunity_forecast_event e
     WHERE e.id=(s->>'record_id')::uuid AND e.workspace_id=common.current_workspace_id()
       AND e.opportunity_id=(s->>'opportunity_id')::uuid
       AND security.authorization_opportunity('opportunity.read',e.opportunity_id)
       AND security.authorization_opportunity('work_report.read',e.opportunity_id))
   WHEN 'meeting_member' THEN EXISTS(SELECT 1 FROM platform.user_ref u
     WHERE u.id=(s->>'record_id')::uuid AND u.workspace_id=common.current_workspace_id()
       AND security.authorization_subject('work_report.read','person',u.id,NULL))
   ELSE false END,false));
$$;

INSERT INTO ops.schema_migration(version,description)
VALUES('V386','Bound report authorization to referenced snapshot and source rows');
COMMIT;
