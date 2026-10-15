-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V227__work_report_sales_week_facts.sql.
-- Original SHA-256: 682d0f44db2e9259211a95f233ebbcce09d930b991ab64e3bd1caf35874e1e14. See shenma_whole_121_manifest.json.
BEGIN;
-- Sales-week actual source lineage. Source-only; never edits the applied V222.
-- CREATE OR REPLACE retains existing owners/ACL. No new role grants or tables.
-- Both functions remain INVOKER; no owner read bypasses current actor RLS.
-- Old four-source manifests retain exactly their original shape and ordering.
CREATE OR REPLACE FUNCTION agent.work_report_source_manifest(p_payload jsonb) RETURNS jsonb
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT COALESCE(jsonb_agg(t.item ORDER BY t.kind,t.id,t.item),'[]'::jsonb)
 FROM (SELECT DISTINCT s->>'source_type' AS kind,(s->>'record_id')::uuid AS id,
   jsonb_build_object('source_type',s->>'source_type','record_id',(s->>'record_id')::uuid)
     || CASE WHEN s->>'source_type'='actual'
        THEN jsonb_build_object('actual_lineage',s->'actual_lineage') ELSE '{}'::jsonb END AS item
   FROM jsonb_array_elements(COALESCE(p_payload->'sources','[]'::jsonb)) s
   UNION
   SELECT 'actual_period',COALESCE(d->>'opportunity_id',d->>'customer_id')::uuid,
     jsonb_build_object('source_type','actual_period','record_id',COALESCE(d->>'opportunity_id',d->>'customer_id')::uuid,
                       'period_dependency',d)
   FROM jsonb_array_elements(COALESCE(p_payload->'sales_week_facts'->'finance'->'dependencies','[]'::jsonb)) d
   UNION
   SELECT 'opportunity_history',(h->>'opportunity_id')::uuid,
     jsonb_build_object('source_type','opportunity_history','record_id',(h->>'opportunity_id')::uuid,
                       'history_guard',h)
   FROM jsonb_array_elements(COALESCE(p_payload->'sales_week_facts'->'history_guards','[]'::jsonb)) h) t;
$$;

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
     AND EXISTS(SELECT 1 FROM crm.opportunity v WHERE v.id=(s->>'record_id')::uuid
       AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL)
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
   ELSE false END,false));
$$;

INSERT INTO ops.schema_migration(version,description)
 VALUES('V375','Sales-week actual source lineage and private report dependency manifests');
COMMIT;
