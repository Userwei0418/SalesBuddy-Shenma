-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V235__work_report_meeting_facts.sql.
-- Original SHA-256: 9f9d6453d45327f820004341588d01b3c0032a5dc684676e6417c3d3eb0dd048. See shenma_whole_121_manifest.json.
BEGIN;
SET LOCAL check_function_bodies=on;
-- Meeting facts are a separate extension. Old weekly event projections/manifests
-- retain their shape; old state events are NOT backfilled as known money history.
ALTER TABLE crm.opportunity_state_event
 ADD COLUMN meeting_state_captured boolean NOT NULL DEFAULT false,
 ADD COLUMN meeting_before jsonb,
 ADD COLUMN meeting_after jsonb,
 ADD CONSTRAINT opportunity_meeting_image CHECK(NOT meeting_state_captured OR COALESCE(
  jsonb_typeof(meeting_after)='object' AND ((mutation_kind='insert' AND meeting_before IS NULL)
   OR (mutation_kind='update' AND jsonb_typeof(meeting_before)='object')),false));
CREATE OR REPLACE FUNCTION crm.capture_opportunity_state_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $history$
DECLARE previous record; event_id uuid:=gen_random_uuid(); native boolean:=false; complete boolean:=false;
 origin uuid; trusted_at timestamptz; origin_kind text:='unknown';
 historical boolean:=COALESCE(current_setting('app.feishu_historical_import',true),'')='on';
 source_kind text; stamp timestamptz; marker text:=current_setting('app.native_opportunity_create',true);
BEGIN
 IF TG_OP='INSERT' THEN
  native:=marker=NEW.id::text AND NOT historical AND NEW.version_no=1 AND NEW.source_code='manual'
   AND NEW.workspace_id=common.current_workspace_id() AND NEW.created_by_user_ref_id=common.current_user_ref_id()
   AND NEW.external_opportunity_id IS NULL AND COALESCE(NEW.import_meta,'{}'::jsonb)='{}'::jsonb
   AND NEW.created_at>=transaction_timestamp() AND NEW.created_at<=clock_timestamp();
  native:=COALESCE(native,false);
  complete:=native;
  origin_kind:=CASE WHEN native THEN 'native_creation' ELSE 'unknown' END;
  origin:=event_id; stamp:=CASE WHEN native THEN NEW.created_at ELSE clock_timestamp() END;
  trusted_at:=stamp;
 ELSE
  SELECT * INTO previous FROM crm.opportunity_state_event e
   WHERE e.workspace_id=OLD.workspace_id AND e.opportunity_id=OLD.id ORDER BY e.after_version DESC LIMIT 1;
  stamp:=NEW.updated_at;
  IF FOUND THEN
   origin:=previous.origin_event_id; trusted_at:=previous.trustworthy_from; origin_kind:=previous.history_origin;
   complete:=previous.complete_from_origin AND NOT historical
    AND previous.after_version=OLD.version_no AND NEW.version_no=OLD.version_no+1
    AND (previous.after_probability,previous.after_status,previous.after_stage)
      IS NOT DISTINCT FROM (OLD.probability,OLD.status,OLD.stage_code)
    AND previous.opportunity_created_at=OLD.created_at AND NEW.created_at=OLD.created_at
    AND NEW.workspace_id=OLD.workspace_id AND NEW.id=OLD.id AND stamp>=previous.occurred_at;
  ELSE
   origin:=event_id; trusted_at:=stamp; complete:=false;
  END IF;
 END IF;
 source_kind:=CASE WHEN historical THEN 'historical_import' WHEN native THEN 'native_application'
  ELSE 'unverified_write' END;
 INSERT INTO crm.opportunity_state_event(id,workspace_id,opportunity_id,occurred_at,mutation_kind,
  before_version,after_version,before_probability,after_probability,before_status,after_status,before_stage,after_stage,
  source_kind,source_ref,source_version,history_origin,origin_event_id,trustworthy_from,complete_from_origin,
  opportunity_created_at,meeting_state_captured,meeting_before,meeting_after)
 VALUES(event_id,NEW.workspace_id,NEW.id,stamp,lower(TG_OP),
  CASE WHEN TG_OP='UPDATE' THEN OLD.version_no END,NEW.version_no,
  CASE WHEN TG_OP='UPDATE' THEN OLD.probability END,NEW.probability,
  CASE WHEN TG_OP='UPDATE' THEN OLD.status END,NEW.status,
  CASE WHEN TG_OP='UPDATE' THEN OLD.stage_code END,NEW.stage_code,
  source_kind,CASE WHEN historical THEN NEW.external_opportunity_id ELSE NEW.created_by_user_ref_id::text END,
  NEW.version_no,origin_kind,origin,trusted_at,complete,NEW.created_at,true,
  CASE WHEN TG_OP='UPDATE' THEN jsonb_build_object('amount',OLD.amount::text,'currency',OLD.currency,'status',OLD.status,'owner_id',OLD.owner_user_ref_id,'team_id',OLD.owner_team_id) END,
  jsonb_build_object('amount',NEW.amount::text,'currency',NEW.currency,'status',NEW.status,'owner_id',NEW.owner_user_ref_id,'team_id',NEW.owner_team_id));
 -- Consume the exact native admission once; it cannot label another insertion.
 IF marker=NEW.id::text THEN PERFORM set_config('app.native_opportunity_create','',true); END IF;
 RETURN NEW;
END $history$;

CREATE TABLE crm.opportunity_forecast_event (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL,
 event_no bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 opportunity_id uuid NOT NULL, forecast_id uuid NOT NULL,
 year integer NOT NULL CHECK(year BETWEEN 2000 AND 2100), quarter integer NOT NULL CHECK(quarter BETWEEN 1 AND 4),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 mutation_kind text NOT NULL CHECK(mutation_kind IN ('INSERT','UPDATE','DELETE')),
 before_state jsonb, after_state jsonb,
 CHECK(COALESCE((mutation_kind='INSERT' AND before_state IS NULL AND jsonb_typeof(after_state)='object')
    OR (mutation_kind='UPDATE' AND jsonb_typeof(before_state)='object' AND jsonb_typeof(after_state)='object')
    OR (mutation_kind='DELETE' AND jsonb_typeof(before_state)='object' AND after_state IS NULL),false)),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id)
);
CREATE INDEX opportunity_forecast_event_scope ON crm.opportunity_forecast_event
 (workspace_id,opportunity_id,recorded_at,id);
ALTER TABLE crm.opportunity_forecast_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.opportunity_forecast_event FORCE ROW LEVEL SECURITY;
CREATE POLICY opportunity_forecast_event_read ON crm.opportunity_forecast_event FOR SELECT USING(
 workspace_id=common.current_workspace_id()
 AND security.authorization_opportunity('opportunity.read',opportunity_id)
 AND security.authorization_opportunity('work_report.read',opportunity_id));
CREATE POLICY opportunity_forecast_event_append ON crm.opportunity_forecast_event FOR INSERT WITH CHECK(
 pg_trigger_depth()>0 AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class
  WHERE oid='crm.opportunity_forecast_event'::regclass));
CREATE TRIGGER opportunity_forecast_event_append_only BEFORE UPDATE OR DELETE OR TRUNCATE
 ON crm.opportunity_forecast_event FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();
CREATE FUNCTION crm.capture_opportunity_forecast_event() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $forecast$
DECLARE r crm.opportunity_forecast; before_image jsonb; after_image jsonb;
BEGIN
 IF TG_OP='UPDATE' AND (NEW.id,NEW.workspace_id,NEW.opportunity_id,NEW.year,NEW.quarter)
   IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.opportunity_id,OLD.year,OLD.quarter)
 THEN RAISE EXCEPTION 'forecast history identity cannot change' USING ERRCODE='22023'; END IF;
 IF TG_OP='DELETE' THEN r:=OLD; ELSE r:=NEW; END IF;
 IF TG_OP<>'INSERT' THEN before_image:=jsonb_build_object(
  'recognized_amount',OLD.recognized_amount::text,'collection_amount',OLD.collection_amount::text); END IF;
 IF TG_OP<>'DELETE' THEN after_image:=jsonb_build_object(
  'recognized_amount',NEW.recognized_amount::text,'collection_amount',NEW.collection_amount::text); END IF;
 INSERT INTO crm.opportunity_forecast_event(workspace_id,opportunity_id,forecast_id,year,quarter,
  mutation_kind,before_state,after_state)
 VALUES(r.workspace_id,r.opportunity_id,r.id,r.year,r.quarter,TG_OP,before_image,after_image);
 RETURN r;
END $forecast$;
CREATE TRIGGER opportunity_forecast_meeting_history AFTER INSERT OR UPDATE OR DELETE
 ON crm.opportunity_forecast FOR EACH ROW EXECUTE FUNCTION crm.capture_opportunity_forecast_event();
-- Remove ambient installation defaults, then grant only SELECT to existing
-- opportunity-history readers. Trigger invocation needs no runtime EXECUTE grant.
DO $acl$
DECLARE item record; grant_row record;
BEGIN
 FOR grant_row IN SELECT DISTINCT a.grantee,r.rolname,c.oid,c.relkind FROM pg_class c
  CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE c.oid IN ('crm.opportunity_forecast_event'::regclass,
    pg_get_serial_sequence('crm.opportunity_forecast_event','event_no')::regclass) AND a.grantee<>c.relowner
 LOOP EXECUTE format('REVOKE ALL ON %s %s FROM %s CASCADE',
  CASE WHEN grant_row.relkind='S' THEN 'SEQUENCE' ELSE 'TABLE' END,grant_row.oid::regclass,
  CASE WHEN grant_row.grantee=0 THEN 'PUBLIC' ELSE quote_ident(grant_row.rolname) END); END LOOP;
 FOR grant_row IN SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='crm.capture_opportunity_forecast_event()'::regprocedure AND a.grantee<>p.proowner
 LOOP EXECUTE format('REVOKE ALL ON FUNCTION crm.capture_opportunity_forecast_event() FROM %s CASCADE',
  CASE WHEN grant_row.grantee=0 THEN 'PUBLIC' ELSE quote_ident(grant_row.rolname) END); END LOOP;
 FOR item IN SELECT r.rolname FROM pg_roles r WHERE NOT r.rolsuper AND NOT r.rolbypassrls
  AND r.oid<>(SELECT relowner FROM pg_class WHERE oid='crm.opportunity_forecast_event'::regclass)
  AND r.rolname NOT LIKE 'pg\_%' ESCAPE '\'
  AND NOT pg_has_role(r.oid,'salegent_feishu_worker','member')
  AND has_table_privilege(r.oid,'crm.opportunity_state_event','SELECT')
 LOOP EXECUTE format('GRANT SELECT ON crm.opportunity_forecast_event TO %I',item.rolname); END LOOP;
END $acl$;
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
   FROM jsonb_array_elements(COALESCE(p_payload->'sales_week_facts'->'history_guards','[]'::jsonb)) h
   UNION
   SELECT m->>'source_type',(m->>'record_id')::uuid,m
   FROM jsonb_array_elements(COALESCE(p_payload->'meeting_dependencies','[]'::jsonb)) m) t;
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


ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_pre_meeting;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $reconcile$
DECLARE total integer; runtime record;
BEGIN
 total:=security.reconcile_runtime_grants_pre_meeting();
 FOR runtime IN SELECT r.rolname FROM pg_roles r WHERE NOT r.rolsuper AND NOT r.rolbypassrls
  AND r.oid<>(SELECT relowner FROM pg_class WHERE oid='crm.opportunity_forecast_event'::regclass)
  AND r.rolname NOT LIKE 'pg\_%' ESCAPE '\'
  AND NOT pg_has_role(r.oid,'salegent_feishu_worker','member')
  AND has_table_privilege(r.oid,'crm.opportunity_state_event','SELECT')
 LOOP
  EXECUTE format('REVOKE ALL ON crm.opportunity_forecast_event FROM %I',runtime.rolname);
  EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM %I',
   pg_get_serial_sequence('crm.opportunity_forecast_event','event_no')::regclass,runtime.rolname);
  EXECUTE format('GRANT SELECT ON crm.opportunity_forecast_event TO %I',runtime.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION crm.capture_opportunity_forecast_event() FROM %I',runtime.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants_pre_meeting() FROM %I',runtime.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $reconcile$;
DO $wrapper_acl$
DECLARE entry record;
BEGIN
 FOR entry IN SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='security.reconcile_runtime_grants()'::regprocedure AND a.grantee<>p.proowner
 LOOP EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants() FROM %s CASCADE',
  CASE WHEN entry.grantee=0 THEN 'PUBLIC' ELSE quote_ident(entry.rolname) END); END LOOP;
END $wrapper_acl$;
-- The $acl$ block above already grants the new objects to existing readers.
-- Do not replay the historical chain during this incremental migration: its
-- legacy grants can widen an installation's reviewed service policy (V126).
-- Keep the wrapper for the deploy orchestrator's explicit reconciliation pass
-- and maintenance reconciliation after later runtime-role provisioning.

INSERT INTO ops.schema_migration(version,description) VALUES('V383','Meeting money and forecast history, immutable facts dependencies and private drafts');
COMMIT;
