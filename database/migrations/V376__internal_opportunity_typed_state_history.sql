-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V228__opportunity_typed_state_history.sql.
-- Original SHA-256: a7de5bab4fd65d26171f704188d7a69e73f253e350acb3f8fc46ab7a2de83526. See shenma_whole_121_manifest.json.
BEGIN;
-- SOURCE ONLY. Existing rows are deliberately not backfilled as native history.
CREATE TABLE crm.opportunity_state_event (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL,
 opportunity_id uuid NOT NULL,
 occurred_at timestamptz NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 mutation_kind text NOT NULL CHECK(mutation_kind IN ('insert','update')),
 before_version integer,
 after_version integer NOT NULL CHECK(after_version>0),
 before_probability numeric(5,2), after_probability numeric(5,2),
 before_status text, after_status text NOT NULL,
 before_stage text, after_stage text NOT NULL,
 source_kind text NOT NULL CHECK(source_kind IN ('native_application','historical_import','unverified_write')),
 source_ref text, source_version integer NOT NULL,
 history_origin text NOT NULL CHECK(history_origin IN ('native_creation','unknown')),
 origin_event_id uuid NOT NULL,
 trustworthy_from timestamptz NOT NULL,
 complete_from_origin boolean NOT NULL,
 opportunity_created_at timestamptz NOT NULL,
 UNIQUE(opportunity_id,after_version),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id),
 CHECK(before_version IS NULL OR before_version>0),
 CHECK(before_probability IS NULL OR before_probability BETWEEN 0 AND 100),
 CHECK(after_probability IS NULL OR after_probability BETWEEN 0 AND 100),
 CHECK(after_status IN ('open','won','lost','cancelled')),
 CHECK(before_status IS NULL OR before_status IN ('open','won','lost','cancelled')),
 CHECK(NOT complete_from_origin OR history_origin='native_creation')
);
CREATE INDEX opportunity_state_event_scope ON crm.opportunity_state_event(workspace_id,opportunity_id,after_version);
ALTER TABLE crm.opportunity_state_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.opportunity_state_event FORCE ROW LEVEL SECURITY;
CREATE POLICY opportunity_state_event_read ON crm.opportunity_state_event FOR SELECT USING(
 workspace_id=common.current_workspace_id()
 AND security.authorization_opportunity('opportunity.read',opportunity_id)
 AND security.authorization_opportunity('work_report.read',opportunity_id));
-- FORCE RLS also covers a non-BYPASS table owner. Only a real trigger running
-- as this table's owner may append/read its predecessor; runtime SELECT roles
-- cannot satisfy this policy or acquire direct write privileges.
CREATE POLICY opportunity_state_event_trigger_read ON crm.opportunity_state_event FOR SELECT USING(
 pg_trigger_depth()>0 AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class
  WHERE oid='crm.opportunity_state_event'::regclass));
CREATE POLICY opportunity_state_event_trigger_insert ON crm.opportunity_state_event FOR INSERT WITH CHECK(
 pg_trigger_depth()>0 AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class
  WHERE oid='crm.opportunity_state_event'::regclass));
CREATE TRIGGER opportunity_state_event_append_only BEFORE UPDATE OR DELETE OR TRUNCATE
 ON crm.opportunity_state_event FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();

-- AFTER sees the actual final version assigned by common.touch_updated_at.
-- The exact pre-existing BEFORE UPDATE trigger must not be assumed from a name.
DO $version_guard$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='crm.opportunity'::regclass
  AND tgname='trg_opportunity_touch' AND tgfoid='common.touch_updated_at()'::regprocedure
  AND tgtype=19 AND tgenabled IN ('O','A') AND NOT tgisinternal AND tgnargs=0 AND tgqual IS NULL)
 THEN RAISE EXCEPTION 'V228_OPPORTUNITY_VERSION_TRIGGER_MISMATCH'; END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='common.touch_updated_at()'::regprocedure
  AND md5(regexp_replace(prosrc,'\s+','','g'))='5156dbe8ed87f8e8cdb78d980b758abc' AND NOT prosecdef)
 THEN RAISE EXCEPTION 'V228_OPPORTUNITY_VERSION_FUNCTION_MISMATCH'; END IF;
END $version_guard$;

CREATE FUNCTION crm.capture_opportunity_state_event() RETURNS trigger
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
  opportunity_created_at)
 VALUES(event_id,NEW.workspace_id,NEW.id,stamp,lower(TG_OP),
  CASE WHEN TG_OP='UPDATE' THEN OLD.version_no END,NEW.version_no,
  CASE WHEN TG_OP='UPDATE' THEN OLD.probability END,NEW.probability,
  CASE WHEN TG_OP='UPDATE' THEN OLD.status END,NEW.status,
  CASE WHEN TG_OP='UPDATE' THEN OLD.stage_code END,NEW.stage_code,
  source_kind,CASE WHEN historical THEN NEW.external_opportunity_id ELSE NEW.created_by_user_ref_id::text END,
  NEW.version_no,origin_kind,origin,trusted_at,complete,NEW.created_at);
 -- Consume the exact native admission once; it cannot label another insertion.
 IF marker=NEW.id::text THEN PERFORM set_config('app.native_opportunity_create','',true); END IF;
 RETURN NEW;
END $history$;
CREATE TRIGGER aa_opportunity_typed_history AFTER INSERT OR UPDATE ON crm.opportunity
 FOR EACH ROW EXECUTE FUNCTION crm.capture_opportunity_state_event();
REVOKE ALL ON FUNCTION crm.capture_opportunity_state_event() FROM PUBLIC,salegent_feishu_worker;

-- Immutable event IDs plus count/head/version fence all source rows at the SQL
-- RLS boundary. Python also verifies the canonical SHA over the complete chain.
CREATE FUNCTION security.work_report_history_access(p_guard jsonb) RETURNS boolean
 LANGUAGE sql STABLE SET search_path=pg_catalog AS $history_access$
 SELECT COALESCE(jsonb_typeof(p_guard)='object'
 AND p_guard ?& ARRAY['opportunity_id','current_version','event_count','head_event_id',
                     'history_sha256','first_event_id','first_event_version']
 AND p_guard-ARRAY['opportunity_id','current_version','event_count','head_event_id',
                   'history_sha256','first_event_id','first_event_version']='{}'::jsonb
 AND p_guard->>'history_sha256' ~ '^[0-9a-f]{64}$'
 AND security.authorization_opportunity('opportunity.read',(p_guard->>'opportunity_id')::uuid)
 AND security.authorization_opportunity('work_report.read',(p_guard->>'opportunity_id')::uuid)
 AND EXISTS(SELECT 1 FROM crm.opportunity o
   WHERE o.workspace_id=common.current_workspace_id() AND o.id=(p_guard->>'opportunity_id')::uuid
    AND o.deleted_at IS NULL AND o.version_no=(p_guard->>'current_version')::integer)
 AND (SELECT count(*) FROM crm.opportunity_state_event e
   WHERE e.workspace_id=common.current_workspace_id() AND e.opportunity_id=(p_guard->>'opportunity_id')::uuid)
    =(p_guard->>'event_count')::integer
 AND (SELECT e.id FROM crm.opportunity_state_event e
   WHERE e.workspace_id=common.current_workspace_id() AND e.opportunity_id=(p_guard->>'opportunity_id')::uuid
   ORDER BY e.after_version DESC LIMIT 1) IS NOT DISTINCT FROM (p_guard->>'head_event_id')::uuid
 AND ((p_guard->>'first_event_id' IS NULL AND p_guard->>'first_event_version' IS NULL)
  OR EXISTS(SELECT 1 FROM crm.opportunity_state_event e
   WHERE e.workspace_id=common.current_workspace_id() AND e.opportunity_id=(p_guard->>'opportunity_id')::uuid
    AND e.id=(p_guard->>'first_event_id')::uuid AND e.after_version=(p_guard->>'first_event_version')::integer
    AND e.after_probability>=30 AND e.complete_from_origin AND e.history_origin='native_creation')),false);
$history_access$;
REVOKE ALL ON FUNCTION security.work_report_history_access(jsonb) FROM PUBLIC,salegent_feishu_worker;

-- Exact V227 source-access body with only the history branch appended.
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
   ELSE false END,false));
$$;

-- New history is readable through current opportunity/report RLS only. No API
-- or worker INSERT/UPDATE/DELETE/TRUNCATE is granted; only the definer trigger writes.
DO $initial_acl$
DECLARE entry record; fn record;
BEGIN
 FOR entry IN SELECT DISTINCT a.grantee,r.rolname FROM pg_class c
  CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE c.oid='crm.opportunity_state_event'::regclass AND a.grantee<>c.relowner
 LOOP
  EXECUTE format('REVOKE ALL ON crm.opportunity_state_event FROM %s CASCADE',
   CASE WHEN entry.grantee=0 THEN 'PUBLIC' ELSE quote_ident(entry.rolname) END);
 END LOOP;
 FOR fn IN SELECT oid,proowner,proacl FROM pg_proc WHERE oid IN (
  'crm.capture_opportunity_state_event()'::regprocedure,'security.work_report_history_access(jsonb)'::regprocedure)
 LOOP
  FOR entry IN SELECT DISTINCT a.grantee,r.rolname
   FROM aclexplode(COALESCE(fn.proacl,acldefault('f',fn.proowner))) a
   LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE a.grantee<>fn.proowner
  LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s CASCADE',fn.oid::regprocedure,
    CASE WHEN entry.grantee=0 THEN 'PUBLIC' ELSE quote_ident(entry.rolname) END);
  END LOOP;
 END LOOP;
END $initial_acl$;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_pre_opportunity_history;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $reconcile$
DECLARE total integer; runtime record;
BEGIN
 total:=security.reconcile_runtime_grants_pre_opportunity_history();
 FOR runtime IN SELECT r.rolname FROM pg_roles r
  WHERE r.rolname NOT LIKE 'pg\_%' ESCAPE '\' AND NOT r.rolsuper AND NOT r.rolbypassrls
   AND NOT pg_has_role(r.oid,'salegent_feishu_worker','member')
   AND has_schema_privilege(r.oid,'crm','USAGE')
   AND has_table_privilege(r.oid,'crm.opportunity','SELECT')
   AND has_function_privilege(r.oid,'security.authorization_opportunity(text,uuid)','EXECUTE')
 LOOP
  EXECUTE format('REVOKE ALL ON crm.opportunity_state_event FROM %I',runtime.rolname);
  EXECUTE format('GRANT SELECT ON crm.opportunity_state_event TO %I',runtime.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION crm.capture_opportunity_state_event() FROM %I',runtime.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants_pre_opportunity_history() FROM %I',runtime.rolname);
  IF has_function_privilege(runtime.rolname,'security.work_report_source_access(jsonb,jsonb)','EXECUTE') THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION security.work_report_history_access(jsonb) TO %I',runtime.rolname);
  END IF;
  total:=total+1;
 END LOOP;
 RETURN total;
END $reconcile$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_pre_opportunity_history()
 FROM PUBLIC,salegent_feishu_worker;
-- A wrapper must not inherit ambient EXECUTE defaults either. Preserve the
-- renamed pre-existing function's explicit owner/operator grants; clear only
-- this new wrapper's non-owner installation defaults.
DO $wrapper_acl$
DECLARE entry record;
BEGIN
 FOR entry IN SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='security.reconcile_runtime_grants()'::regprocedure AND a.grantee<>p.proowner
 LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants() FROM %s CASCADE',
   CASE WHEN entry.grantee=0 THEN 'PUBLIC' ELSE quote_ident(entry.rolname) END);
 END LOOP;
END $wrapper_acl$;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description)
 VALUES('V376','Immutable typed opportunity probability/status history with trusted native origins');
COMMIT;
