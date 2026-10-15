-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V200__opportunity_actual_basis_and_finance_sync.sql.
-- Original SHA-256: c1e9df204a0a05900952f72bd5900a40472c9d316392a8a3d520cd0a266ddfef. See shenma_whole_121_manifest.json.
BEGIN;

-- An explicit choice of evidence, never an extra amount or rewritten ledger.
CREATE TABLE crm.opportunity_actual_basis (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 opportunity_id uuid NOT NULL,
 year integer NOT NULL CHECK (year BETWEEN 2000 AND 2100),
 quarter integer NOT NULL CHECK (quarter BETWEEN 1 AND 4),
 kind text NOT NULL CHECK (kind IN ('recognized','collection')),
 source text NOT NULL CHECK (source IN ('entries','historical')),
 selected_history_id uuid,
 source_fingerprint text NOT NULL CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
 reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 1000),
 decided_by_user_ref_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 version_no integer NOT NULL DEFAULT 1 CHECK (version_no>0),
 UNIQUE(workspace_id,opportunity_id,year,quarter,kind),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id),
 FOREIGN KEY(decided_by_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(selected_history_id,workspace_id) REFERENCES crm.opportunity_period_actual_snapshot(id,workspace_id),
 CHECK ((source='entries' AND selected_history_id IS NULL) OR (source='historical' AND selected_history_id IS NOT NULL))
);
ALTER TABLE crm.opportunity_actual_basis ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.opportunity_actual_basis FORCE ROW LEVEL SECURITY;
CREATE POLICY active_tenant_guard ON crm.opportunity_actual_basis AS RESTRICTIVE FOR ALL TO PUBLIC
 USING (security.workspace_is_active(common.current_workspace_id()))
 WITH CHECK (security.workspace_is_active(common.current_workspace_id()));
CREATE POLICY permission_read ON crm.opportunity_actual_basis FOR SELECT TO PUBLIC
 USING (workspace_id=common.current_workspace_id()
  AND security.authorization_opportunity('actual.read',opportunity_id)
  AND EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=opportunity_actual_basis.opportunity_id AND o.workspace_id=opportunity_actual_basis.workspace_id
   AND security.authorization_customer('actual.read',o.customer_id)));
CREATE POLICY permission_create ON crm.opportunity_actual_basis FOR INSERT TO PUBLIC
 WITH CHECK (workspace_id=common.current_workspace_id() AND security.management_actor()
  AND security.authorization_has('access.console') AND decided_by_user_ref_id=common.current_user_ref_id()
  AND security.authorization_opportunity('actual.create',opportunity_id)
  AND EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=opportunity_actual_basis.opportunity_id AND o.workspace_id=opportunity_actual_basis.workspace_id
   AND security.authorization_customer('actual.create',o.customer_id)));
CREATE POLICY permission_update ON crm.opportunity_actual_basis FOR UPDATE TO PUBLIC
 USING (workspace_id=common.current_workspace_id() AND security.management_actor()
  AND security.authorization_has('access.console')
  AND security.authorization_opportunity('actual.create',opportunity_id)
  AND EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=opportunity_actual_basis.opportunity_id AND o.workspace_id=opportunity_actual_basis.workspace_id
   AND security.authorization_customer('actual.create',o.customer_id)))
 WITH CHECK (workspace_id=common.current_workspace_id() AND security.management_actor()
  AND security.authorization_has('access.console') AND decided_by_user_ref_id=common.current_user_ref_id()
  AND security.authorization_opportunity('actual.create',opportunity_id)
  AND EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=opportunity_actual_basis.opportunity_id AND o.workspace_id=opportunity_actual_basis.workspace_id
   AND security.authorization_customer('actual.create',o.customer_id)));

CREATE FUNCTION crm.guard_opportunity_actual_basis() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='UPDATE' THEN
  IF (NEW.id,NEW.workspace_id,NEW.opportunity_id,NEW.year,NEW.quarter,NEW.kind,NEW.created_at)
    IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.opportunity_id,OLD.year,OLD.quarter,OLD.kind,OLD.created_at) THEN
   RAISE EXCEPTION 'ACTUAL_BASIS_IDENTITY_IMMUTABLE' USING ERRCODE='23514';
  END IF;
  IF (to_jsonb(NEW)-ARRAY['updated_at','version_no'])=(to_jsonb(OLD)-ARRAY['updated_at','version_no']) THEN RETURN NULL; END IF;
  IF NEW.version_no<>OLD.version_no+1 THEN
   RAISE EXCEPTION 'ACTUAL_BASIS_VERSION_CONFLICT' USING ERRCODE='40001';
  END IF;
 ELSE
  IF NEW.version_no<>1 THEN RAISE EXCEPTION 'ACTUAL_BASIS_INITIAL_VERSION' USING ERRCODE='23514'; END IF;
  NEW.created_at:=clock_timestamp();
 END IF;
 IF NEW.decided_by_user_ref_id IS DISTINCT FROM common.current_user_ref_id() THEN
  RAISE EXCEPTION 'ACTUAL_BASIS_ACTOR_MISMATCH' USING ERRCODE='42501';
 END IF;
 IF NEW.source='historical' AND NOT EXISTS(SELECT 1 FROM crm.opportunity_period_actual_snapshot h
  WHERE h.id=NEW.selected_history_id AND h.workspace_id=NEW.workspace_id AND h.opportunity_id=NEW.opportunity_id
   AND h.year=NEW.year AND h.quarter=NEW.quarter AND h.kind=NEW.kind) THEN
  RAISE EXCEPTION 'ACTUAL_BASIS_HISTORY_BUCKET_MISMATCH' USING ERRCODE='23514';
 END IF;
 NEW.reason:=btrim(NEW.reason);
 NEW.updated_at:=clock_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER opportunity_actual_basis_guard BEFORE INSERT OR UPDATE ON crm.opportunity_actual_basis
 FOR EACH ROW EXECUTE FUNCTION crm.guard_opportunity_actual_basis();
CREATE TRIGGER opportunity_actual_basis_audit AFTER INSERT OR UPDATE OR DELETE ON crm.opportunity_actual_basis
 FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row();

-- Keep the public entry point's OID/owner/ACL and all earlier source behavior.
DO $$ DECLARE original_owner name; BEGIN
 SELECT pg_get_userbyid(proowner) INTO original_owner FROM pg_proc
 WHERE oid='ops.feishu_source(uuid,text,uuid)'::regprocedure;
 EXECUTE replace(pg_get_functiondef('ops.feishu_source(uuid,text,uuid)'::regprocedure),
  'CREATE OR REPLACE FUNCTION ops.feishu_source(',
  'CREATE OR REPLACE FUNCTION ops.feishu_source_before_opportunity_finance(');
 EXECUTE format('ALTER FUNCTION ops.feishu_source_before_opportunity_finance(uuid,text,uuid) OWNER TO %I',original_owner);
 -- The existing trusted source owner may differ from this migration owner.
 EXECUTE format('GRANT SELECT ON crm.opportunity_actual_basis TO %I',original_owner);
END $$;
CREATE OR REPLACE FUNCTION ops.feishu_source(p_connection uuid,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb; extra jsonb; ws uuid;
BEGIN
 result:=ops.feishu_source_before_opportunity_finance(p_connection,p_kind,p_id);
 IF p_kind<>'opportunity' OR result->>'excluded'='true' OR result->>'deleted'='true' THEN RETURN result; END IF;
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 SELECT jsonb_build_object('workspace_id',ws,'finance_company_code',w.external_workspace_id,
  'finance_actuals',COALESCE((SELECT jsonb_agg(jsonb_build_object(
   'id',a.id,'opportunity_id',a.opportunity_id,'kind',a.kind,'amount',a.amount::text,
   'occurred_on',a.occurred_on,'voided_at',a.voided_at,'void_reason',a.void_reason,'source_ref',a.source_ref,
   'created_at',a.created_at,'updated_at',COALESCE(a.voided_at,a.created_at)) ORDER BY a.id)
   FROM crm.customer_actual a WHERE a.workspace_id=ws AND a.opportunity_id=p_id),'[]'::jsonb),
  'finance_history',COALESCE((SELECT jsonb_agg(jsonb_build_object(
   'id',h.id,'opportunity_id',h.opportunity_id,'year',h.year,'quarter',h.quarter,'kind',h.kind,
   'raw_amount',h.raw_amount::text,'source_unit',h.source_unit,'tax_basis',h.tax_basis,'source_field',h.source_field,
   'source_record_id',h.source_record_id,'import_batch_id',h.import_batch_id) ORDER BY h.id)
   FROM crm.opportunity_period_actual_snapshot h WHERE h.workspace_id=ws AND h.opportunity_id=p_id),'[]'::jsonb),
  'finance_decisions',COALESCE((SELECT jsonb_agg(jsonb_build_object(
   'id',d.id,'year',d.year,'quarter',d.quarter,'kind',d.kind,'source',d.source,'selected_history_id',d.selected_history_id,
   'source_fingerprint',d.source_fingerprint,'reason',d.reason,'decided_by_user_ref_id',d.decided_by_user_ref_id,
   'created_at',d.created_at,'updated_at',d.updated_at,'version_no',d.version_no) ORDER BY d.id)
   FROM crm.opportunity_actual_basis d WHERE d.workspace_id=ws AND d.opportunity_id=p_id),'[]'::jsonb))
 INTO extra FROM platform.workspace w JOIN crm.opportunity o ON o.workspace_id=w.id AND o.id=p_id WHERE w.id=ws;
 RETURN result||COALESCE(extra,'{}'::jsonb);
END $$;

-- Only dependent opportunity projections, never a company refresh or a parent
-- business UPDATE. No pending/running dedupe: each committed change survives.
CREATE FUNCTION ops.capture_opportunity_finance_dependency() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE current_row jsonb; previous_row jsonb; parent record; connection uuid; origin text;
BEGIN
 IF TG_TABLE_SCHEMA<>'crm' OR TG_TABLE_NAME NOT IN
  ('customer_actual','opportunity_period_actual_snapshot','opportunity_actual_basis') THEN
  RAISE EXCEPTION 'UNSUPPORTED_FINANCE_DEPENDENCY' USING ERRCODE='22023';
 END IF;
 current_row:=CASE WHEN TG_OP='DELETE' THEN '{}'::jsonb ELSE to_jsonb(NEW) END;
 previous_row:=CASE WHEN TG_OP='INSERT' THEN '{}'::jsonb ELSE to_jsonb(OLD) END;
 IF TG_OP='UPDATE' AND current_row-ARRAY['updated_at','version_no']=previous_row-ARRAY['updated_at','version_no'] THEN RETURN NULL; END IF;
 origin:=CASE WHEN TG_TABLE_NAME='opportunity_period_actual_snapshot'
  OR COALESCE(current_setting('app.feishu_historical_import',true),'')='on' THEN 'reconcile' ELSE 'business' END;
 FOR parent IN SELECT DISTINCT (r->>'workspace_id')::uuid ws,(r->>'opportunity_id')::uuid id
  FROM (VALUES(current_row),(previous_row)) t(r)
  WHERE r->>'workspace_id' IS NOT NULL AND r->>'opportunity_id' IS NOT NULL ORDER BY ws,id LOOP
  -- Serialize against the API's parent-row lock when confirming an evidence
  -- fingerprint. Do not rewrite the parent or generate dependent child loops.
  PERFORM 1 FROM crm.opportunity o WHERE o.workspace_id=parent.ws AND o.id=parent.id FOR NO KEY UPDATE;
  SELECT id INTO connection FROM config.feishu_connection WHERE workspace_id=parent.ws;
  IF connection IS NOT NULL THEN
   INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,first_formal_create,historical,queue_origin)
    VALUES(connection,parent.ws,'opportunity',parent.id,false,true,origin);
  END IF;
 END LOOP;
 RETURN NULL;
END $$;
CREATE TRIGGER feishu_finance_parent AFTER INSERT OR UPDATE OR DELETE ON crm.customer_actual
 FOR EACH ROW EXECUTE FUNCTION ops.capture_opportunity_finance_dependency();
CREATE TRIGGER feishu_finance_parent AFTER INSERT OR UPDATE OR DELETE ON crm.opportunity_period_actual_snapshot
 FOR EACH ROW EXECUTE FUNCTION ops.capture_opportunity_finance_dependency();
CREATE TRIGGER feishu_finance_parent AFTER INSERT OR UPDATE OR DELETE ON crm.opportunity_actual_basis
 FOR EACH ROW EXECUTE FUNCTION ops.capture_opportunity_finance_dependency();

-- Late-created API identities inherit only this new table's matching existing
-- actual permissions. RLS still enforces real management identity and scopes.
CREATE FUNCTION security.grant_opportunity_actual_basis_runtime() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE runtime record; total integer:=0;
BEGIN
 FOR runtime IN SELECT r.oid,r.rolname FROM pg_roles r
  WHERE NOT r.rolsuper AND NOT r.rolbypassrls AND left(r.rolname,3)<>'pg_'
   AND r.rolname<>'salegent_feishu_worker'
   AND NOT pg_has_role(r.oid,'salegent_feishu_worker','MEMBER')
   AND r.oid<>(SELECT relowner FROM pg_class WHERE oid='crm.opportunity_actual_basis'::regclass)
   AND has_schema_privilege(r.oid,'crm','USAGE') AND has_schema_privilege(r.oid,'security','USAGE')
   AND has_table_privilege(r.oid,'crm.customer_actual','SELECT') AND has_table_privilege(r.oid,'crm.opportunity','SELECT') LOOP
  EXECUTE format('GRANT SELECT ON crm.opportunity_actual_basis TO %I',runtime.rolname);
  IF has_table_privilege(runtime.oid,'crm.customer_actual','INSERT') THEN
   EXECUTE format('GRANT INSERT,UPDATE ON crm.opportunity_actual_basis TO %I',runtime.rolname);
  END IF;
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_opportunity_actual_basis;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer;
BEGIN
 total:=security.reconcile_runtime_grants_before_opportunity_actual_basis();
 RETURN total+security.grant_opportunity_actual_basis_runtime();
END $$;
REVOKE ALL ON TABLE crm.opportunity_actual_basis FROM PUBLIC;
-- Default privileges may grant new objects broadly. Remove every non-owner
-- table grant before narrowly applying runtime rights, retaining trusted source.
DO $$ DECLARE grantee record; source_owner name; BEGIN
 SELECT pg_get_userbyid(proowner) INTO source_owner FROM pg_proc WHERE oid='ops.feishu_source(uuid,text,uuid)'::regprocedure;
 FOR grantee IN SELECT DISTINCT r.rolname FROM pg_class c
  CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
  JOIN pg_roles r ON r.oid=a.grantee WHERE c.oid='crm.opportunity_actual_basis'::regclass AND a.grantee<>c.relowner LOOP
  EXECUTE format('REVOKE ALL ON crm.opportunity_actual_basis FROM %I',grantee.rolname);
 END LOOP;
 EXECUTE format('GRANT SELECT ON crm.opportunity_actual_basis TO %I',source_owner);
END $$;
DO $$ DECLARE signature text; grantee record; BEGIN
 FOREACH signature IN ARRAY ARRAY['crm.guard_opportunity_actual_basis()',
  'ops.feishu_source_before_opportunity_finance(uuid,text,uuid)','ops.capture_opportunity_finance_dependency()',
  'security.grant_opportunity_actual_basis_runtime()','security.reconcile_runtime_grants()',
  'security.reconcile_runtime_grants_before_opportunity_actual_basis()'] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',signature);
  FOR grantee IN SELECT DISTINCT r.rolname FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
   JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=signature::regprocedure AND a.grantee<>p.proowner LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',signature,grantee.rolname);
  END LOOP;
 END LOOP;
END $$;
-- A trusted migration owner may replace a coordinator owned by an earlier
-- migration identity. Allow only the new coordinator's definer to call that
-- private prior chain; no runtime role gains access to reconciliation.
DO $$ DECLARE coordinator name; BEGIN
 SELECT pg_get_userbyid(proowner) INTO coordinator FROM pg_proc
  WHERE oid='security.reconcile_runtime_grants()'::regprocedure;
 IF NOT has_function_privilege(coordinator,'security.reconcile_runtime_grants_before_opportunity_actual_basis()','EXECUTE') THEN
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.reconcile_runtime_grants_before_opportunity_actual_basis() TO %I',coordinator);
 END IF;
END $$;
-- Do not replay historic runtime reconciliation during the initial upgrade.
SELECT security.grant_opportunity_actual_basis_runtime();
INSERT INTO ops.schema_migration(version,description)
 VALUES('V348','Opportunity actual-source evidence decisions and targeted financial projection dependencies');
COMMIT;
