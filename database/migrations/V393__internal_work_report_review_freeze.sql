-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V245__work_report_review_freeze.sql.
-- Original SHA-256: 36cab1055618b765dc822bdd69fa15f8b73e262316d31481969728adea06d19b. See shenma_whole_121_manifest.json.
-- Server-produced Review facts remain subject to current database access.
BEGIN;

CREATE FUNCTION security.work_report_review_access(p_dependency jsonb) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE typ text; ident uuid; permission text; subject_type text;
BEGIN
 IF jsonb_typeof(p_dependency) IS DISTINCT FROM 'object'
  OR common.current_workspace_id() IS NULL OR common.current_user_ref_id() IS NULL
  OR NOT security.authorization_has('work_report.read')
  OR NOT EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.id=common.current_user_ref_id()
   AND u.workspace_id=common.current_workspace_id() AND u.status='active' AND u.deleted_at IS NULL)
 THEN RETURN false; END IF;
 typ=p_dependency->>'source_type';
 IF typ='review_actual' THEN
  IF p_dependency->>'kind' NOT IN ('collection','recognized')
   OR NOT p_dependency ? 'record_version' OR NOT p_dependency ? 'opportunity_id'
   OR length(COALESCE(p_dependency->>'record_id','')) NOT BETWEEN 1 AND 256
  THEN RETURN false; END IF;
  RETURN EXISTS(SELECT 1 FROM crm.effective_actual e
   JOIN crm.customer c ON c.id=e.customer_id AND c.workspace_id=e.workspace_id
   LEFT JOIN crm.opportunity o ON o.id=e.opportunity_id AND o.workspace_id=e.workspace_id
   WHERE e.workspace_id=common.current_workspace_id() AND e.id=p_dependency->>'record_id'
    AND e.kind=p_dependency->>'kind' AND e.version_no=(p_dependency->>'record_version')::integer
    AND e.customer_id=(p_dependency->>'customer_id')::uuid
    AND e.opportunity_id IS NOT DISTINCT FROM (p_dependency->>'opportunity_id')::uuid
    AND c.deleted_at IS NULL AND (e.opportunity_id IS NULL OR (o.id IS NOT NULL AND o.deleted_at IS NULL))
    AND security.authorization_customer('actual.read',e.customer_id)
    AND security.authorization_customer('work_report.read',e.customer_id)
    AND (e.opportunity_id IS NULL OR (security.authorization_opportunity('actual.read',e.opportunity_id)
     AND security.authorization_opportunity('work_report.read',e.opportunity_id))));
 END IF;
 ident=(p_dependency->>'record_id')::uuid;
 IF ident IS NULL THEN RETURN false; END IF;
 IF typ='review_subject' THEN
  subject_type=p_dependency->>'scope_type';
  IF subject_type NOT IN ('person','team') OR subject_type IS NULL
   OR jsonb_typeof(p_dependency->'required_permissions') IS DISTINCT FROM 'array'
  THEN RETURN false; END IF;
  IF NOT (p_dependency->'required_permissions' ? 'work_report.read')
   OR jsonb_array_length(p_dependency->'required_permissions')=0
  THEN RETURN false; END IF;
  FOR permission IN SELECT jsonb_array_elements_text(p_dependency->'required_permissions') LOOP
   IF permission NOT IN ('work_report.read','customer.read','opportunity.read','visit.read',
     'task.read','risk.read','actual.read','target.read') OR permission IS NULL
    OR NOT COALESCE(security.authorization_subject(permission,subject_type,
      CASE WHEN subject_type='person' THEN ident END,CASE WHEN subject_type='team' THEN ident END),false)
   THEN RETURN false; END IF;
  END LOOP;
  RETURN true;
 ELSIF typ='review_target' THEN
  RETURN EXISTS(SELECT 1 FROM crm.sales_target t WHERE t.id=ident AND t.workspace_id=common.current_workspace_id()
   AND security.target_scope_read(t.scope_type,t.user_ref_id,t.team_id,t.department_code)
   AND security.authorization_subject('work_report.read',t.scope_type,t.user_ref_id,t.team_id));
 ELSIF typ='review_objective' THEN
  RETURN EXISTS(SELECT 1 FROM crm.review_objective o WHERE o.id=ident AND o.workspace_id=common.current_workspace_id()
   AND o.status IN ('active','archived')
   AND security.target_scope_read(o.scope_type,o.user_ref_id,o.team_id,o.department_code)
   AND security.authorization_subject('work_report.read',o.scope_type,o.user_ref_id,o.team_id));
 ELSIF typ='review_key_result' THEN
  RETURN EXISTS(SELECT 1 FROM crm.review_key_result k JOIN crm.review_objective o
    ON o.id=k.objective_id AND o.workspace_id=k.workspace_id
   WHERE k.id=ident AND k.workspace_id=common.current_workspace_id()
    AND k.status IN ('active','archived') AND o.status IN ('active','archived')
    AND security.target_scope_read(o.scope_type,o.user_ref_id,o.team_id,o.department_code)
    AND security.authorization_subject('work_report.read',o.scope_type,o.user_ref_id,o.team_id)
    AND (k.target_id IS NULL OR EXISTS(SELECT 1 FROM crm.sales_target t
     WHERE t.id=k.target_id AND t.workspace_id=k.workspace_id
      AND security.target_scope_read(t.scope_type,t.user_ref_id,t.team_id,t.department_code)
      AND security.authorization_subject('work_report.read',t.scope_type,t.user_ref_id,t.team_id))));
 END IF;
 RETURN false;
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN RETURN false;
END $$;
REVOKE ALL ON FUNCTION security.work_report_review_access(jsonb) FROM PUBLIC;
DO $initial_acl$
DECLARE entry record;
BEGIN
 FOR entry IN SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='security.work_report_review_access(jsonb)'::regprocedure AND a.grantee<>p.proowner
 LOOP EXECUTE format('REVOKE ALL ON FUNCTION security.work_report_review_access(jsonb) FROM %s',
  CASE WHEN entry.grantee=0 THEN 'PUBLIC' ELSE quote_ident(entry.rolname) END); END LOOP;
END $initial_acl$;

CREATE OR REPLACE FUNCTION agent.work_report_source_manifest(p_payload jsonb) RETURNS jsonb
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT COALESCE(jsonb_agg(t.item ORDER BY t.kind,t.id,t.item),'[]'::jsonb)
 FROM (SELECT DISTINCT s->>'source_type' AS kind,((s->>'record_id')::uuid)::text AS id,
   jsonb_build_object('source_type',s->>'source_type','record_id',(s->>'record_id')::uuid)
     || CASE WHEN s->>'source_type'='actual'
        THEN jsonb_build_object('actual_lineage',s->'actual_lineage') ELSE '{}'::jsonb END AS item
   FROM jsonb_array_elements(COALESCE(p_payload->'sources','[]'::jsonb)) s
   UNION
   SELECT 'actual_period',(COALESCE(d->>'opportunity_id',d->>'customer_id')::uuid)::text,
     jsonb_build_object('source_type','actual_period','record_id',COALESCE(d->>'opportunity_id',d->>'customer_id')::uuid,
                       'period_dependency',d)
   FROM jsonb_array_elements(COALESCE(p_payload->'sales_week_facts'->'finance'->'dependencies','[]'::jsonb)) d
   UNION
   SELECT 'opportunity_history',((h->>'opportunity_id')::uuid)::text,
     jsonb_build_object('source_type','opportunity_history','record_id',(h->>'opportunity_id')::uuid,
                       'history_guard',h)
   FROM jsonb_array_elements(COALESCE(p_payload->'sales_week_facts'->'history_guards','[]'::jsonb)) h
   UNION
   SELECT m->>'source_type',((m->>'record_id')::uuid)::text,m
   FROM jsonb_array_elements(COALESCE(p_payload->'meeting_dependencies','[]'::jsonb)
      || COALESCE(p_payload->'overview_dependencies','[]'::jsonb)) m
   UNION
   SELECT d->>'source_type',d->>'record_id',d
   FROM jsonb_array_elements(COALESCE(p_payload->'review_dependencies','[]'::jsonb)) d
   UNION
   -- A Review payload without a server-generated scope guard is never readable.
   SELECT 'review_invalid',NULL::text,jsonb_build_object('source_type','review_invalid')
   WHERE (p_payload ? 'review_facts' OR p_payload ? 'review_dependencies') AND (
    jsonb_typeof(p_payload->'review_facts') IS DISTINCT FROM 'object'
    OR p_payload->'review_facts'->>'contract_version' NOT IN ('work_report.review_facts.v1','work_report.review_facts.v2')
    OR (p_payload->'review_facts'->>'contract_version'='work_report.review_facts.v2' AND (
       jsonb_typeof(p_payload->'review_facts'->'scope') IS DISTINCT FROM 'object'
       OR p_payload->'review_facts'->'scope'->>'contract_version' IS DISTINCT FROM 'review.scope.v2'
       OR jsonb_typeof(p_payload->'review_facts'->'scope'->'subject_ids') IS DISTINCT FROM 'array'
       OR jsonb_array_length(COALESCE(p_payload->'review_facts'->'scope'->'subject_ids','[]'::jsonb))=0
       OR length(COALESCE(p_payload->'review_facts'->'scope'->>'scope_key',''))<1
       OR length(COALESCE(p_payload->'review_facts'->'scope'->>'scope_revision',''))<1
       OR jsonb_array_length(COALESCE(p_payload->'review_dependencies','[]'::jsonb)) < jsonb_array_length(p_payload->'review_facts'->'scope'->'subject_ids')
    ))
    OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(p_payload->'review_dependencies','[]'::jsonb)) d
      WHERE d->>'source_type'='review_subject'))) t;
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
   WHEN 'opportunity_history' THEN (s->>'record_id')::uuid=(s->'history_guard'->>'opportunity_id')::uuid
     AND security.work_report_history_access(s->'history_guard')
   WHEN 'meeting_claim' THEN EXISTS(SELECT 1 FROM crm.customer_ownership_event e
     JOIN crm.customer_claim_request r ON r.id=e.claim_request_id AND r.workspace_id=e.workspace_id
     WHERE e.id=(s->>'record_id')::uuid AND e.workspace_id=common.current_workspace_id()
       AND e.customer_id=(s->>'customer_id')::uuid AND e.event_type='approved' AND r.status='approved'
       AND r.customer_id=e.customer_id AND r.applicant_user_ref_id=e.owner_user_ref_id
       AND security.authorization_customer('customer.read',e.customer_id)
       AND security.authorization_customer('work_report.read',e.customer_id))
   WHEN 'meeting_customer' THEN EXISTS(SELECT 1 FROM crm.customer c
     WHERE c.id=(s->>'record_id')::uuid AND c.workspace_id=common.current_workspace_id()
       AND c.deleted_at IS NULL AND security.authorization_customer('customer.read',c.id)
       AND security.authorization_customer('work_report.read',c.id))
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
   WHEN 'review_subject' THEN security.work_report_review_access(s)
   WHEN 'review_target' THEN security.work_report_review_access(s)
   WHEN 'review_objective' THEN security.work_report_review_access(s)
   WHEN 'review_key_result' THEN security.work_report_review_access(s)
   WHEN 'review_actual' THEN security.work_report_review_access(s)
   ELSE false END,false));
$$;

CREATE OR REPLACE FUNCTION agent.protect_work_report() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE s agent.work_report_snapshot;
BEGIN
 IF TG_TABLE_NAME='work_report_document' AND TG_OP='INSERT' THEN
  SELECT * INTO s FROM agent.work_report_snapshot WHERE id=NEW.snapshot_id AND workspace_id=NEW.workspace_id AND status='ready';
  IF s.id IS NULL OR s.owner_user_ref_id<>NEW.owner_user_ref_id OR s.snapshot_hash<>NEW.snapshot_hash THEN
   RAISE insufficient_privilege; END IF;
  NEW.source_manifest=s.source_manifest; NEW.selected_sources=s.selection->'sources';
  NEW.context=jsonb_build_object('period',s.period,'scope',s.scope,'coverage',s.payload->'coverage','metrics',s.payload->'metrics');
  IF s.payload ? 'meeting_facts' THEN NEW.context=NEW.context||jsonb_build_object('meeting_facts',s.payload->'meeting_facts'); END IF;
  IF s.payload ? 'overview_facts' THEN
   NEW.context=NEW.context||jsonb_build_object('overview_facts',s.payload->'overview_facts'->'facts');
  END IF;
  IF s.payload ? 'review_facts' THEN
   NEW.context=NEW.context||jsonb_build_object('review_facts',s.payload->'review_facts',
    'review_dependencies',s.payload->'review_dependencies');
  END IF;
  RETURN NEW;
 END IF;
 IF (NEW.id,NEW.workspace_id,NEW.owner_user_ref_id,NEW.created_at)
  IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.owner_user_ref_id,OLD.created_at) THEN
  RAISE EXCEPTION '报告标识、租户和作者不可修改' USING ERRCODE='22023'; END IF;
 IF TG_TABLE_NAME='work_report_snapshot' THEN
  IF OLD.status<>'preparing' OR (NEW.selection,NEW.period,NEW.scope) IS DISTINCT FROM (OLD.selection,OLD.period,OLD.scope) THEN
   RAISE EXCEPTION '事实快照不可覆盖' USING ERRCODE='22023'; END IF;
 ELSIF TG_TABLE_NAME='work_report_document' THEN
  IF (NEW.snapshot_id,NEW.snapshot_hash,NEW.context,NEW.source_manifest,NEW.selected_sources)
   IS DISTINCT FROM (OLD.snapshot_id,OLD.snapshot_hash,OLD.context,OLD.source_manifest,OLD.selected_sources) THEN
   RAISE EXCEPTION '报告的事实快照不可替换' USING ERRCODE='22023'; END IF;
  IF (NEW.shared_revision_id,NEW.audience) IS DISTINCT FROM (OLD.shared_revision_id,OLD.audience)
   AND NOT security.authorization_has('work_report.share') THEN RAISE insufficient_privilege; END IF;
  NEW.version_no=OLD.version_no+1; NEW.updated_at=clock_timestamp();
 ELSIF TG_TABLE_NAME='work_report_run' THEN
  IF (NEW.snapshot_id,NEW.request,NEW.generation_input) IS DISTINCT FROM (OLD.snapshot_id,OLD.request,OLD.generation_input)
   OR OLD.status IN ('succeeded','failed','cancelled') THEN
   RAISE EXCEPTION '作业输入与终态不可覆盖' USING ERRCODE='22023'; END IF;
  NEW.version_no=OLD.version_no+1;
 ELSE
  NEW.version_no=OLD.version_no+1; NEW.updated_at=clock_timestamp();
 END IF;
 RETURN NEW;
END $$;
-- Match only principals that already execute the existing report access gate.
-- No new table SELECT, DML, role membership or RLS policy is introduced.
DO $acl$
DECLARE runtime record;
BEGIN
 FOR runtime IN SELECT r.rolname FROM pg_roles r WHERE NOT r.rolsuper AND NOT r.rolbypassrls
  AND r.rolname NOT LIKE 'pg\_%' ESCAPE '\'
  AND has_function_privilege(r.oid,'security.work_report_source_access(jsonb,jsonb)','EXECUTE')
  AND has_table_privilege(r.oid,'agent.work_report_snapshot','SELECT')
 LOOP EXECUTE format('GRANT EXECUTE ON FUNCTION security.work_report_review_access(jsonb) TO %I',runtime.rolname); END LOOP;
END $acl$;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_review_freeze;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; runtime record;
BEGIN
 total:=security.reconcile_runtime_grants_before_review_freeze();
 FOR runtime IN SELECT r.rolname FROM pg_roles r WHERE NOT r.rolsuper AND NOT r.rolbypassrls
  AND r.rolname NOT LIKE 'pg\_%' ESCAPE '\'
 LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION security.work_report_review_access(jsonb),security.reconcile_runtime_grants_before_review_freeze(),security.reconcile_runtime_grants() FROM %I',runtime.rolname);
  IF has_function_privilege(runtime.rolname,'security.work_report_source_access(jsonb,jsonb)','EXECUTE')
   AND has_table_privilege(runtime.rolname,'agent.work_report_snapshot','SELECT') THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION security.work_report_review_access(jsonb) TO %I',runtime.rolname);
   total:=total+1;
  END IF;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_review_freeze() FROM PUBLIC;
INSERT INTO ops.schema_migration(version,description)
 VALUES('V393','Freeze Review report facts v1/v2 and current subject, target, KR and actual access dependencies');
COMMIT;
