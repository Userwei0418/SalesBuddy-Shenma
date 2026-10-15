BEGIN;
-- Legacy permissions must still pass the new module gate. New work_report
-- permissions follow the upstream scoped role migration; no blanket admin grant.
INSERT INTO control.permission_module(permission_code,module_code)
 SELECT code,CASE WHEN code='weekly_report.generate' THEN 'ai' ELSE 'core' END
 FROM config.permission_catalog WHERE code LIKE 'weekly_report.%' OR code='access.business_web'
 ON CONFLICT DO NOTHING;
-- These workspaces previously used the deployment's unified model service.
-- The initial bridge has no customer-specific secret material and no new tenant
-- policy override. Future policy updates are not replayed by this migration.
INSERT INTO control.tenant_ai_service(workspace_id,shared_enabled)
 SELECT workspace_id,true FROM ops.shenma_121_legacy_workspace
 ON CONFLICT(workspace_id) DO UPDATE SET shared_enabled=true;

-- V393 retained the Review provenance guards but replaced the two V386
-- primary-key point lookups with EXISTS again. Restore exactly those query
-- forms here: invoker RLS, every authorization predicate, and unknown-source
-- denial are unchanged. Both source IDs are unique primary keys (0/1 rows).
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
   WHEN 'review_subject' THEN security.work_report_review_access(s)
   WHEN 'review_target' THEN security.work_report_review_access(s)
   WHEN 'review_objective' THEN security.work_report_review_access(s)
   WHEN 'review_key_result' THEN security.work_report_review_access(s)
   WHEN 'review_actual' THEN security.work_report_review_access(s)
   ELSE false END,false));
$$;

-- Preserve the immutable applicant snapshot while allowing the new reviewer
-- preview to prepare reviewed_payload before completing the same transaction.
CREATE OR REPLACE FUNCTION security.protect_customer_creation_request() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE insufficient_privilege; END IF;
 IF (to_jsonb(NEW)-ARRAY['status','reviewed_at','reviewer_user_ref_id','decision_reason','created_customer_id','version_no','reviewed_payload'])
    IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','reviewed_at','reviewer_user_ref_id','decision_reason','created_customer_id','version_no','reviewed_payload'])
    OR OLD.status<>'pending' THEN
  RAISE EXCEPTION '申请原始内容和已处理结果不可修改' USING ERRCODE='22023'; END IF;
 IF NEW.status='pending' THEN
  IF (to_jsonb(NEW)-'reviewed_payload') IS DISTINCT FROM (to_jsonb(OLD)-'reviewed_payload')
    OR security.authorization_has('customer.create_review') IS NOT TRUE THEN
   RAISE insufficient_privilege; END IF;
 ELSIF NEW.version_no<>OLD.version_no+1 THEN
  RAISE EXCEPTION '申请版本冲突' USING ERRCODE='22023';
 END IF;
 RETURN NEW;
END $$;

-- Keep the final upstream projection (including finance/task improvements),
-- then restore customer-specific weekly projection and synthetic exclusions.
ALTER FUNCTION ops.feishu_source(uuid,text,uuid) RENAME TO feishu_source_internal_121;
CREATE FUNCTION ops.feishu_source(p_connection uuid,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 IF ws IS NULL THEN RAISE insufficient_privilege; END IF;
 IF p_kind='weekly_report' THEN
  IF EXISTS(SELECT 1 FROM insight.weekly_report WHERE id=p_id AND workspace_id=ws) THEN
   RETURN ops.feishu_weekly_source(p_connection,p_id);
  END IF;
  RETURN ops.feishu_work_report_source(p_connection,p_id);
 END IF;
 IF p_kind IN ('customer','opportunity','visit','task')
  AND security.synthetic_trial_marker(ws,p_kind,p_id) IS NOT NULL THEN
  RETURN jsonb_build_object('id',p_id,'excluded',true,'reason','synthetic_trial');
 END IF;
 RETURN ops.feishu_source_internal_121(p_connection,p_kind,p_id);
END $$;
REVOKE ALL ON FUNCTION ops.feishu_source(uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.feishu_source(uuid,text,uuid) TO salegent_feishu_worker;
-- Renaming preserves ACLs. Worker callers must not bypass the final synthetic
-- wrapper by directly invoking an older internal source projection.
DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='ops' AND p.proname LIKE 'feishu_source_%'
 LOOP EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,salegent_feishu_worker',f.signature); END LOOP;
END $$;


CREATE OR REPLACE FUNCTION ops.capture_feishu_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r jsonb; prior jsonb; ws uuid; cid uuid; kind text:=TG_ARGV[0]; oid uuid;
 fresh boolean:=false; live boolean:=false; origin text; previous_connection uuid;
BEGIN
 IF NOT ((TG_TABLE_SCHEMA||'.'||TG_TABLE_NAME,kind) IN (
  ('crm.customer','customer'),('crm.opportunity','opportunity'),('activity.visit','visit'),
  ('crm.partner','partner'),('workflow.task','task'),('crm.opportunity_demo_scenes','demo_scene'),
  ('crm.customer_actual','actual'),('crm.sales_target','target'),('platform.user_ref','member'),
  ('crm.contact','contact'),('crm.opportunity_forecast','forecast'))) THEN
  RAISE EXCEPTION 'Unsupported Feishu direct capture relation' USING ERRCODE='0A000';
 END IF;
 r:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 prior:=CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
 IF TG_OP='UPDATE' AND r=prior THEN RETURN NULL; END IF;
 ws:=(r->>'workspace_id')::uuid; oid:=(r->>'id')::uuid;
 -- Preserve the customer synthetic-create boundary while keeping all upstream capture semantics.
 IF TG_OP='INSERT' AND kind IN ('customer','opportunity','visit','task')
   AND security.synthetic_trial_marker(ws,kind,oid) IS NOT NULL THEN RETURN NULL; END IF;
 origin:=CASE WHEN COALESCE(current_setting('app.feishu_historical_import',true),'')='on'
  OR (TG_OP='INSERT' AND COALESCE(r->'import_meta'->>'import_type','')='crm_history')
  THEN 'reconcile' ELSE 'business' END;
 -- A controlled identity/tenant move must also remove the former projection.
 IF TG_OP='UPDATE' AND (r->>'workspace_id',r->>'id') IS DISTINCT FROM (prior->>'workspace_id',prior->>'id') THEN
  SELECT id INTO previous_connection FROM config.feishu_connection WHERE workspace_id=(prior->>'workspace_id')::uuid;
  IF previous_connection IS NOT NULL AND (kind<>'visit' OR prior->>'archived_at' IS NOT NULL) THEN
   INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,first_formal_create,historical,queue_origin)
    VALUES(previous_connection,(prior->>'workspace_id')::uuid,kind,(prior->>'id')::uuid,false,true,origin);
  END IF;
 END IF;
 SELECT id,enabled AND COALESCE((settings->'notification'->>'enabled')::boolean,false) AND (kind<>'task' OR (
  COALESCE((settings#>>'{mappings,task,enabled}')::boolean,false)
  AND COALESCE(settings#>'{notification,on_create}','[]'::jsonb) ? 'task'))
  INTO cid,live FROM config.feishu_connection WHERE workspace_id=ws;
 IF cid IS NULL THEN RETURN NULL; END IF;
 IF kind='task' THEN
  live:=COALESCE(live,false) AND COALESCE(r->>'source_code','')='meeting_import'
   AND COALESCE(common.current_role_code(),'')='administrator'
   AND COALESCE(current_setting('app.task_group_notification_origin',true),'')='admin_weekly_dispatch';
 END IF;
 IF kind='visit' THEN
  IF r->>'archived_at' IS NULL THEN RETURN NULL; END IF;
  fresh:=TG_OP='INSERT' OR prior->>'archived_at' IS NULL;
 ELSE fresh:=TG_OP='INSERT'; END IF;
 INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,first_formal_create,historical,queue_origin)
 VALUES(cid,ws,kind,oid,fresh AND TG_OP<>'DELETE',NOT live
  OR COALESCE(r->'import_meta'->>'import_type','')='crm_history'
  OR COALESCE(prior->'import_meta'->>'import_type','')='crm_history'
  OR COALESCE(current_setting('app.feishu_historical_import',true),'')='on',origin);
 RETURN NULL;
END $$;

-- Server-created frozen manifest includes meeting/overview/review dependencies.
-- Every business root is checked; unknown kinds and malformed parents fail closed.
-- Caller must independently recheck work_report_source_access and owner/grants.
CREATE FUNCTION security.work_report_feishu_source_safe(
 p_workspace uuid,p_manifest jsonb,p_context jsonb,p_payload jsonb
) RETURNS boolean LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s jsonb; kind text; ident uuid; cid uuid; oid uuid; found_row boolean;
BEGIN
 IF p_workspace IS NULL
  OR jsonb_typeof(p_manifest) IS DISTINCT FROM 'array'
  OR jsonb_typeof(p_context) IS DISTINCT FROM 'object'
  OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
  OR p_manifest IS DISTINCT FROM agent.work_report_source_manifest(p_payload)
 THEN RETURN false; END IF;
 -- This private worker helper proves provenance without an actor session.
 -- Publish separately enforces current source access; frozen manifest must match.
 FOR s IN SELECT value FROM jsonb_array_elements(p_manifest) LOOP
  cid:=NULL; oid:=NULL; kind:=s->>'source_type'; found_row:=false;
  IF jsonb_typeof(s) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
  IF kind='actual_period' THEN
   SELECT e.customer_id,e.opportunity_id INTO cid,oid FROM crm.effective_actual e
    WHERE e.workspace_id=p_workspace AND e.id=s->'period_dependency'->>'effective_fact_id'
     AND e.customer_id=(s->'period_dependency'->>'customer_id')::uuid
     AND e.opportunity_id IS NOT DISTINCT FROM (s->'period_dependency'->>'opportunity_id')::uuid;
   found_row:=FOUND;
  ELSIF kind IN ('actual','review_actual') THEN
   SELECT e.customer_id,e.opportunity_id INTO cid,oid FROM crm.effective_actual e
    WHERE e.workspace_id=p_workspace AND e.id=s->>'record_id'
     AND (kind='review_actual' OR e.id=s->'actual_lineage'->>'effective_fact_id')
     AND (kind<>'review_actual' OR (e.customer_id=(s->>'customer_id')::uuid
       AND e.opportunity_id IS NOT DISTINCT FROM (s->>'opportunity_id')::uuid
       AND e.kind=s->>'kind' AND e.version_no=(s->>'record_version')::integer));
   found_row:=FOUND;
  ELSE
   ident:=(s->>'record_id')::uuid;
   IF ident IS NULL THEN RETURN false; END IF;
   CASE kind
   WHEN 'visit' THEN
    SELECT v.customer_id,v.opportunity_id INTO cid,oid FROM activity.visit v
     WHERE v.id=ident AND v.workspace_id=p_workspace AND v.deleted_at IS NULL;
    found_row:=FOUND;
    IF security.synthetic_trial_marker(p_workspace,'visit',ident) IS NOT NULL THEN RETURN false; END IF;
   WHEN 'opportunity','opportunity_history' THEN
    SELECT o.customer_id,o.id INTO cid,oid FROM crm.opportunity o
     WHERE o.id=ident AND o.workspace_id=p_workspace AND o.deleted_at IS NULL;
    found_row:=FOUND;
   WHEN 'meeting_customer' THEN
    SELECT c.id,NULL::uuid INTO cid,oid FROM crm.customer c
     WHERE c.id=ident AND c.workspace_id=p_workspace AND c.deleted_at IS NULL;
    found_row:=FOUND;
   WHEN 'meeting_claim' THEN
    SELECT e.customer_id,NULL::uuid INTO cid,oid FROM crm.customer_ownership_event e
     JOIN crm.customer_claim_request r ON r.id=e.claim_request_id AND r.workspace_id=e.workspace_id
     WHERE e.id=ident AND e.workspace_id=p_workspace AND e.customer_id=(s->>'customer_id')::uuid
      AND e.event_type='approved' AND r.status='approved' AND r.customer_id=e.customer_id
      AND r.applicant_user_ref_id=e.owner_user_ref_id;
    found_row:=FOUND;
   WHEN 'meeting_state' THEN
    SELECT o.customer_id,e.opportunity_id INTO cid,oid FROM crm.opportunity_state_event e
     JOIN crm.opportunity o ON o.id=e.opportunity_id AND o.workspace_id=e.workspace_id
     WHERE e.id=ident AND e.workspace_id=p_workspace AND e.opportunity_id=(s->>'opportunity_id')::uuid;
    found_row:=FOUND;
   WHEN 'meeting_forecast' THEN
    SELECT o.customer_id,e.opportunity_id INTO cid,oid FROM crm.opportunity_forecast_event e
     JOIN crm.opportunity o ON o.id=e.opportunity_id AND o.workspace_id=e.workspace_id
     WHERE e.id=ident AND e.workspace_id=p_workspace AND e.opportunity_id=(s->>'opportunity_id')::uuid;
    found_row:=FOUND;
   WHEN 'meeting_member' THEN
    SELECT EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.id=ident AND u.workspace_id=p_workspace
      AND u.deleted_at IS NULL) INTO found_row;
   WHEN 'review_subject' THEN
    CASE s->>'scope_type'
     WHEN 'person' THEN SELECT EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.id=ident
       AND u.workspace_id=p_workspace AND u.deleted_at IS NULL) INTO found_row;
     WHEN 'team' THEN SELECT EXISTS(SELECT 1 FROM platform.team t WHERE t.id=ident
       AND t.workspace_id=p_workspace AND t.deleted_at IS NULL) INTO found_row;
     ELSE RETURN false;
    END CASE;
   WHEN 'review_target' THEN
    SELECT EXISTS(SELECT 1 FROM crm.sales_target t WHERE t.id=ident
      AND t.workspace_id=p_workspace) INTO found_row;
   WHEN 'review_objective' THEN
    SELECT EXISTS(SELECT 1 FROM crm.review_objective o WHERE o.id=ident
      AND o.workspace_id=p_workspace AND o.status IN ('active','archived')) INTO found_row;
   WHEN 'review_key_result' THEN
    SELECT EXISTS(SELECT 1 FROM crm.review_key_result k JOIN crm.review_objective o
      ON o.id=k.objective_id AND o.workspace_id=k.workspace_id
      WHERE k.id=ident AND k.workspace_id=p_workspace AND k.status IN ('active','archived')
       AND o.status IN ('active','archived') AND (k.target_id IS NULL OR EXISTS(
        SELECT 1 FROM crm.sales_target t WHERE t.id=k.target_id AND t.workspace_id=p_workspace))) INTO found_row;
   WHEN 'task' THEN
    SELECT t.customer_id,t.opportunity_id INTO cid,oid FROM workflow.task t
     WHERE t.id=ident AND t.workspace_id=p_workspace AND t.deleted_at IS NULL;
    found_row:=FOUND;
    IF security.synthetic_trial_marker(p_workspace,'task',ident) IS NOT NULL THEN RETURN false; END IF;
   WHEN 'risk' THEN
    SELECT r.customer_id,r.opportunity_id INTO cid,oid FROM insight.risk r
     WHERE r.id=ident AND r.workspace_id=p_workspace AND r.deleted_at IS NULL;
    found_row:=FOUND;
   ELSE RETURN false;
   END CASE;
  END IF;
  IF NOT found_row THEN RETURN false; END IF;
  IF cid IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM crm.customer c
    WHERE c.workspace_id=p_workspace AND c.id=cid AND c.deleted_at IS NULL)
    OR security.synthetic_trial_marker(p_workspace,'customer',cid) IS NOT NULL)
   THEN RETURN false; END IF;
  IF oid IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM crm.opportunity o
    WHERE o.workspace_id=p_workspace AND o.id=oid AND o.deleted_at IS NULL)
    OR security.synthetic_trial_marker(p_workspace,'opportunity',oid) IS NOT NULL)
   THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN RETURN false;
END $$;
REVOKE ALL ON FUNCTION security.work_report_feishu_source_safe(uuid,jsonb,jsonb,jsonb) FROM PUBLIC;

-- Freeze once per document. Explicit submission is required; generation and
-- editing never queue a report. A revision replay returns the existing event.
CREATE TABLE insight.work_report_feishu_publication (
 document_id uuid PRIMARY KEY,
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 author_id uuid NOT NULL,
 revision_id uuid NOT NULL,
 document_version integer NOT NULL,
 event_id uuid NOT NULL UNIQUE,
 snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object'),
 published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(document_id,workspace_id) REFERENCES agent.work_report_document(id,workspace_id),
 FOREIGN KEY(revision_id,document_id,workspace_id) REFERENCES agent.work_report_revision(id,document_id,workspace_id),
 FOREIGN KEY(author_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
ALTER TABLE insight.work_report_feishu_publication ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.work_report_feishu_publication FORCE ROW LEVEL SECURITY;
REVOKE ALL ON insight.work_report_feishu_publication FROM PUBLIC,salegent_feishu_worker;
CREATE TRIGGER work_report_feishu_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
 ON insight.work_report_feishu_publication FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();

CREATE FUNCTION security.publish_work_report_feishu(p_document uuid,p_expected_version integer,p_revision uuid,p_markdown text)
 RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d agent.work_report_document%ROWTYPE; s agent.work_report_snapshot%ROWTYPE;
 r agent.work_report_revision%ROWTYPE; c config.feishu_connection%ROWTYPE;
 pub insight.work_report_feishu_publication%ROWTYPE; eid uuid; frozen jsonb;
BEGIN
 IF security.authorization_has('work_report.read') IS NOT TRUE
  OR security.authorization_has('work_report.write') IS NOT TRUE
  OR security.authorization_has('work_report.share') IS NOT TRUE THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO d FROM agent.work_report_document WHERE id=p_document
  AND workspace_id=common.current_workspace_id() AND owner_user_ref_id=common.current_user_ref_id() FOR UPDATE;
 IF NOT FOUND OR security.work_report_source_access(d.source_manifest,d.selected_sources) IS NOT TRUE THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO pub FROM insight.work_report_feishu_publication WHERE document_id=d.id AND workspace_id=d.workspace_id;
 IF FOUND THEN
  IF pub.revision_id=p_revision THEN RETURN pub.event_id; END IF;
  RAISE EXCEPTION 'WORK_REPORT_FEISHU_ALREADY_PUBLISHED' USING ERRCODE='22023';
 END IF;
 IF d.version_no<>p_expected_version OR d.latest_revision_id<>p_revision THEN
  RAISE EXCEPTION 'WORK_REPORT_FEISHU_VERSION_CONFLICT' USING ERRCODE='22023'; END IF;
 SELECT * INTO r FROM agent.work_report_revision WHERE id=p_revision AND document_id=d.id AND workspace_id=d.workspace_id;
 SELECT * INTO s FROM agent.work_report_snapshot WHERE id=d.snapshot_id AND workspace_id=d.workspace_id AND snapshot_hash=d.snapshot_hash;
 IF r.id IS NULL OR s.id IS NULL OR s.status<>'ready' OR NULLIF(btrim(p_markdown),'') IS NULL THEN
  RAISE EXCEPTION 'WORK_REPORT_FEISHU_NOT_READY' USING ERRCODE='22023'; END IF;
 IF security.work_report_feishu_source_safe(d.workspace_id,d.source_manifest,d.context,s.payload) IS NOT TRUE THEN
  RAISE EXCEPTION 'WORK_REPORT_FEISHU_SOURCE_EXCLUDED' USING ERRCODE='22023'; END IF;
 SELECT * INTO c FROM config.feishu_connection WHERE workspace_id=d.workspace_id FOR SHARE;
 IF NOT FOUND OR NOT c.enabled OR c.validated_revision IS DISTINCT FROM c.revision
   OR c.settings->>'direction'<>'system_to_base'
   OR COALESCE((c.settings->'mappings'->'weekly_report'->>'enabled')::boolean,false) IS NOT TRUE
   OR COALESCE((c.settings->'notification'->>'enabled')::boolean,false) IS NOT TRUE
   OR c.settings->'notification'->>'routing_mode'<>'single_group'
   OR NULLIF(c.settings->'notification'->>'default_chat_id','') IS NULL
   OR NOT (COALESCE(c.settings->'notification'->'on_create','[]'::jsonb) ? 'weekly_report') THEN
  RAISE EXCEPTION 'WORK_REPORT_FEISHU_NOT_CONFIGURED' USING ERRCODE='22023'; END IF;
 eid:=gen_random_uuid();
 frozen:=jsonb_build_object('id',d.id,'author_id',d.owner_user_ref_id,'title',COALESCE(r.content->>'title','工作汇报'),
  'body_markdown',p_markdown,'report_week',((s.period->>'start')::timestamptz AT TIME ZONE 'Asia/Shanghai')::date,
  'report_week_end',((s.period->>'end_exclusive')::timestamptz AT TIME ZONE 'Asia/Shanghai')::date-1,
  'source_cutoff_at',s.period->>'cutoff','snapshot_at',s.created_at,
  'context',d.context,'source_manifest',d.source_manifest,'payload',s.payload,'revision_id',r.id,
  'draft_version',r.revision_no,'status','succeeded','result_status','ready',
  'created_at',d.created_at,'updated_at',d.updated_at,'generated_at',r.created_at,'published_at',clock_timestamp());
 INSERT INTO insight.work_report_feishu_publication(document_id,workspace_id,author_id,revision_id,document_version,event_id,snapshot)
 VALUES(d.id,d.workspace_id,d.owner_user_ref_id,r.id,d.version_no,eid,frozen);
 INSERT INTO ops.feishu_event(id,connection_id,workspace_id,object_kind,object_id,first_formal_create)
 VALUES(eid,c.id,d.workspace_id,'weekly_report',d.id,true);
 RETURN eid;
END $$;

CREATE FUNCTION security.work_report_feishu_status(p_document uuid) RETURNS jsonb
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d agent.work_report_document%ROWTYPE; pub insight.work_report_feishu_publication%ROWTYPE; result jsonb; blocked text;
BEGIN
 IF security.authorization_has('work_report.read') IS NOT TRUE THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO d FROM agent.work_report_document WHERE id=p_document AND workspace_id=common.current_workspace_id()
  AND owner_user_ref_id=common.current_user_ref_id();
 IF NOT FOUND OR security.work_report_source_access(d.source_manifest,d.selected_sources) IS NOT TRUE THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO pub FROM insight.work_report_feishu_publication WHERE document_id=d.id AND workspace_id=d.workspace_id;
 IF NOT FOUND THEN
  SELECT CASE WHEN security.work_report_feishu_source_safe(d.workspace_id,d.source_manifest,d.context,s.payload)
   THEN NULL ELSE 'source_excluded' END INTO blocked
  FROM agent.work_report_snapshot s WHERE s.id=d.snapshot_id AND s.workspace_id=d.workspace_id;
  IF blocked IS NULL AND (security.authorization_has('work_report.write') IS NOT TRUE
   OR security.authorization_has('work_report.share') IS NOT TRUE) THEN blocked:='permission_denied'; END IF;
  IF blocked IS NULL AND NOT EXISTS(SELECT 1 FROM config.feishu_connection c
   WHERE c.workspace_id=d.workspace_id AND c.enabled AND c.validated_revision=c.revision
    AND c.settings->>'direction'='system_to_base'
    AND COALESCE((c.settings#>>'{mappings,weekly_report,enabled}')::boolean,false)
    AND COALESCE((c.settings#>>'{notification,enabled}')::boolean,false)
    AND c.settings#>>'{notification,routing_mode}'='single_group'
    AND NULLIF(c.settings#>>'{notification,default_chat_id}','') IS NOT NULL
    AND COALESCE(c.settings#>'{notification,on_create}','[]'::jsonb) ? 'weekly_report')
   THEN blocked:='feishu_not_configured'; END IF;
  RETURN jsonb_build_object('status','not_published','can_publish',blocked IS NULL,'blocked_reason',blocked,
   'event_id',NULL,'revision_id',NULL,'record_id',NULL,'message_id',NULL,'error_code',NULL);
 END IF;
 SELECT jsonb_build_object('status',CASE WHEN f.status='sent' THEN 'sent' WHEN f.status='unknown' THEN 'unknown'
  WHEN e.status='dead_letter' OR f.status='failed' THEN 'failed' ELSE 'pending' END,
  'can_publish',false,'blocked_reason','already_published','record_id',(SELECT record_id FROM ops.feishu_record_map WHERE connection_id=e.connection_id AND object_kind='weekly_report' AND object_id=d.id),'event_id',e.id,'requested_at',pub.published_at,'revision_id',pub.revision_id,'document_version',pub.document_version,
  'draft_version',pub.snapshot->'draft_version','message_id',f.message_id,'error_code',COALESCE(f.error_code,e.error_code),'sent_at',f.sent_at)
 INTO result FROM ops.feishu_event e LEFT JOIN ops.feishu_delivery f ON f.event_id=e.id WHERE e.id=pub.event_id ORDER BY f.created_at DESC LIMIT 1;
 RETURN COALESCE(result,jsonb_build_object('status','failed','error_code','WORK_REPORT_FEISHU_EVENT_MISSING'));
END $$;

CREATE FUNCTION ops.feishu_work_report_source(p_connection uuid,p_id uuid) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; pub insight.work_report_feishu_publication%ROWTYPE; raw jsonb;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 IF ws IS NULL THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO pub FROM insight.work_report_feishu_publication WHERE document_id=p_id AND workspace_id=ws;
 IF NOT FOUND THEN RETURN jsonb_build_object('id',p_id,'deleted',true); END IF;
 raw:=pub.snapshot;
 IF security.work_report_feishu_source_safe(ws,raw->'source_manifest',raw->'context',raw->'payload') IS NOT TRUE THEN
  RETURN jsonb_build_object('id',p_id,'excluded',true,'reason','synthetic_trial_or_unverifiable_source'); END IF;
 -- Never expose raw source/context to the worker; only frozen public prose and
 -- small metadata bound to this company's explicit publication.
 RETURN (raw-ARRAY['source_manifest','context','payload'])||jsonb_build_object(
  'author_name',(SELECT display_name FROM platform.user_ref WHERE workspace_id=ws AND id=pub.author_id),
  'author_account',(SELECT account_code FROM platform.user_ref WHERE workspace_id=ws AND id=pub.author_id),
  'stats_summary','工作汇报（按已确认版本发布）','record_count',NULL::integer,
  'customer_count',NULL::integer,'opportunity_count',NULL::integer,'detail_url',NULL::text,
  'feishu_push_status',COALESCE((SELECT CASE status WHEN 'sent' THEN '已推送' WHEN 'unknown' THEN '待核对' WHEN 'failed' THEN '推送失败' ELSE '排队中' END FROM ops.feishu_delivery WHERE event_id=pub.event_id ORDER BY created_at DESC LIMIT 1),'排队中'));
END $$;
-- Only ops.feishu_source is a worker-callable SECURITY DEFINER entrypoint.
-- The final wrapper runs both old/new projections as its owner; allowing direct
-- helper calls would fail the worker startup allowlist and bypass that boundary.
REVOKE ALL ON FUNCTION security.publish_work_report_feishu(uuid,integer,uuid,text),security.work_report_feishu_status(uuid),ops.feishu_work_report_source(uuid,uuid),ops.feishu_weekly_source(uuid,uuid) FROM PUBLIC,salegent_feishu_worker;

ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_shenma_121_base;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
 LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r record; reconciled integer;
BEGIN
 reconciled:=security.reconcile_runtime_grants_shenma_121_base();
 FOR r IN SELECT DISTINCT role.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles role ON role.oid=a.grantee
  WHERE n.nspname='platform' AND c.relname='user_ref' AND a.privilege_type='SELECT' AND a.grantee<>c.relowner
   AND NOT pg_has_role(role.oid,'salegent_feishu_worker','member')
 LOOP
  -- Exact additive API + business-worker policy from internal e40a8029 / V250.
  -- These services share the existing Shenma runtime role. Require its existing
  -- application capabilities as well as SELECT(user_ref); a reporting-only role
  -- must not acquire write/worker privileges through table-read inference.
  IF NOT has_function_privilege(r.rolname,'security.submit_customer_creation_request(jsonb)','EXECUTE')
    OR NOT has_table_privilege(r.rolname,'crm.customer','INSERT') THEN
   CONTINUE;
  END IF;
  EXECUTE format('GRANT EXECUTE ON FUNCTION
   agent.work_report_source_manifest(jsonb),
   ops.claim_exact_competency_review(text,integer,uuid,uuid,uuid,uuid,integer,text,jsonb,text,uuid,integer),
   ops.claim_exact_visit_agent_run(text,integer,uuid,uuid,uuid,uuid,text,text),
   ops.claim_exact_visit_import(text,integer,uuid,uuid,uuid,uuid,text),
   ops.claim_exact_work_report(text,integer,uuid,uuid,uuid,uuid,text,text,text,integer,timestamp with time zone,timestamp with time zone),
   security.company_customer_directory_search(text,integer,integer,text,text,text[],text),
   security.partner_enterprise_reference(uuid),
   security.set_customer_business_identity(uuid,text[],uuid),
   security.work_report_audience_matches(jsonb),
   security.work_report_history_access(jsonb),
   security.work_report_review_access(jsonb),
   security.work_report_source_access(jsonb,jsonb) TO %I',r.rolname);
  EXECUTE format('GRANT SELECT,INSERT,UPDATE ON
   agent.work_report_document,agent.work_report_run,agent.work_report_scheme,agent.work_report_snapshot TO %I',r.rolname);
  EXECUTE format('GRANT SELECT,INSERT ON
   agent.work_report_event,agent.work_report_revision,agent.work_report_scheme_revision TO %I',r.rolname);
  EXECUTE format('GRANT SELECT(version,applied_at) ON ops.schema_migration TO %I',r.rolname);
  EXECUTE format('GRANT DELETE ON activity.visit_field_value,crm.opportunity,crm.opportunity_forecast TO %I',r.rolname);
  EXECUTE format('GRANT UPDATE,DELETE ON crm.opportunity_quote_reference TO %I',r.rolname);
  EXECUTE format('GRANT UPDATE ON crm.opportunity_related_partner,platform.workspace TO %I',r.rolname);
  -- Shenma's approved-request boundary is intentionally stricter than upstream:
  -- only the reviewed payload is mutable; creation/decision require SECDEF APIs.
  EXECUTE format('REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON crm.customer_creation_request FROM %I',r.rolname);
  EXECUTE format('GRANT SELECT,UPDATE(reviewed_payload) ON crm.customer_creation_request TO %I',r.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.publish_work_report_feishu(uuid,integer,uuid,text),security.work_report_feishu_status(uuid) TO %I',r.rolname);
  EXECUTE format('REVOKE ALL ON insight.work_report_feishu_publication FROM %I',r.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION ops.feishu_source(uuid,text,uuid),ops.feishu_source_internal_121(uuid,text,uuid),ops.feishu_work_report_source(uuid,uuid) FROM %I',r.rolname);
 END LOOP;
 RETURN reconciled;
END $$;
DO $$ DECLARE permission record; function_owner name; BEGIN
 SELECT r.rolname INTO function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
  WHERE p.oid='security.reconcile_runtime_grants_shenma_121_base()'::regprocedure;
 REVOKE ALL ON FUNCTION security.reconcile_runtime_grants() FROM PUBLIC;
 EXECUTE format('ALTER FUNCTION security.reconcile_runtime_grants() OWNER TO %I',function_owner);
 FOR permission IN SELECT a.grantee,a.is_grantable,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='security.reconcile_runtime_grants_shenma_121_base()'::regprocedure AND a.privilege_type='EXECUTE'
 LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.reconcile_runtime_grants() TO %s%s',
   CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END,
   CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END);
 END LOOP;
END $$;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V399','Whole 1.2.1 with retained Shenma quota, legacy weekly, synthetic guards and explicit report publication');
COMMIT;
