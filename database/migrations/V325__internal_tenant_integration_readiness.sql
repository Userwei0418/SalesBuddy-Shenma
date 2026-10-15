-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V177__tenant_integration_readiness.sql.
-- Original SHA-256: ca9a6d92b2e8d26a75745103d1503b228e1dcde1c86899f21de4e44077af5a55. See shenma_whole_121_manifest.json.
BEGIN;
-- Configuration can be saved before external synchronization is licensed.
-- Control/initialization and the worker still require the feishu module.
UPDATE control.permission_module SET module_code='core'
 WHERE permission_code IN ('feishu.read','feishu.configure') AND module_code='feishu';

-- Only server-owned presence booleans enter this projection; no provider secret,
-- URL, model name or customer content is returned or persisted here.
CREATE FUNCTION control.configuration_status(p_session text,p_workspace uuid,p_instance jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE report jsonb; facts jsonb; checks jsonb:='[]'; missing jsonb; ready boolean;
 ai boolean; visits boolean; sync boolean; allowed boolean; needed boolean; enabled boolean; f jsonb; mappings_ok boolean;
BEGIN
 report:=control.configuration_status(p_session,p_workspace);
 SELECT COALESCE(bool_or((m->>'effective')::boolean) FILTER(WHERE m->>'code'='ai'),false),
        COALESCE(bool_or((m->>'effective')::boolean) FILTER(WHERE m->>'code'='visit'),false),
        COALESCE(bool_or((m->>'effective')::boolean) FILTER(WHERE m->>'code'='feishu'),false)
 INTO ai,visits,sync FROM jsonb_array_elements(report->'modules') m;
 allowed:=report->'ai_service'->'shared_enabled'='true'::jsonb;
 FOR facts IN SELECT * FROM jsonb_array_elements(report->'model_connections') LOOP
  missing:='[]';
  IF facts->>'mode'='disabled' THEN missing:='["purpose_disabled"]';
  ELSIF facts->>'mode'='invalid' THEN missing:='["invalid_configuration"]';
  ELSIF facts->'service_authorized' IS DISTINCT FROM 'true'::jsonb THEN missing:='["source_not_authorized"]';
  ELSIF facts->'fields_present'='true'::jsonb AND facts->'credential_saved'='true'::jsonb THEN NULL;
  ELSIF facts->>'source' IN ('instance_default','tenant_legacy') AND allowed
    AND p_instance->(facts->>'purpose')='true'::jsonb THEN NULL;
  ELSE
   IF facts->>'source'='instance_default' THEN missing:='["shared_service_not_configured"]';
   ELSE
    IF facts->'fields_present' IS DISTINCT FROM 'true'::jsonb THEN missing:=missing||'"fields_missing"'::jsonb; END IF;
    IF facts->'credential_saved' IS DISTINCT FROM 'true'::jsonb THEN missing:=missing||'"credential_missing"'::jsonb; END IF;
   END IF;
  END IF;
  ready:=jsonb_array_length(missing)=0;
  needed:=facts->>'purpose'='text' AND ai;
  enabled:=CASE facts->>'purpose' WHEN 'text' THEN ai WHEN 'asr' THEN ai AND visits ELSE false END;
  checks:=checks||jsonb_build_array(jsonb_build_object('code',facts->>'purpose','enabled',enabled,
    'required',needed,'configuration_ready',ready,'issues',missing));
 END LOOP;
 SELECT COALESCE(bool_and(COALESCE(length(btrim(m.value->>'table_id'))>0
   AND length(btrim(m.value->>'id_field_id'))>0
   AND length(btrim(m.value->'fields'->>'record_status'))>0,false)),false)
 INTO mappings_ok FROM config.feishu_connection fc
 CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(fc.settings->'mappings')='object'
   THEN fc.settings->'mappings' ELSE '{}'::jsonb END) m
 WHERE fc.workspace_id=p_workspace AND m.value->'enabled'='true'::jsonb;
 f:=(report->'feishu')||jsonb_build_object('mapping_fields_present',mappings_ok);
 report:=jsonb_set(report,'{feishu}',f);missing:='[]';
 IF f->'configured' IS DISTINCT FROM 'true'::jsonb THEN missing:='["connection_missing"]';
 ELSE
  IF f->'fields_present' IS DISTINCT FROM 'true'::jsonb THEN missing:=missing||'"fields_missing"'::jsonb; END IF;
  IF f->'credential_saved' IS DISTINCT FROM 'true'::jsonb THEN missing:=missing||'"credential_missing"'::jsonb; END IF;
  IF f->'direction_supported' IS DISTINCT FROM 'true'::jsonb THEN missing:=missing||'"direction_unsupported"'::jsonb; END IF;
  IF COALESCE((f->>'enabled_mappings')::integer,0)=0 THEN missing:=missing||'"mapping_missing"'::jsonb; END IF;
  IF (f->>'enabled_mappings')::integer>0 AND NOT mappings_ok THEN missing:=missing||'"mapping_fields_missing"'::jsonb; END IF;
 END IF;
 checks:=checks||jsonb_build_array(jsonb_build_object('code','feishu','enabled',sync,'required',sync,
  'configuration_ready',jsonb_array_length(missing)=0,'issues',missing));
 RETURN report||jsonb_build_object('configuration_readiness',jsonb_build_object('kind','static_configuration',
  'required_ready',NOT EXISTS(SELECT 1 FROM jsonb_array_elements(checks) c
     WHERE c->'required'='true'::jsonb AND c->'configuration_ready' IS DISTINCT FROM 'true'::jsonb),
  'checks',checks));
END $$;

ALTER FUNCTION control.transition(text,uuid,integer,text,text) RENAME TO transition_before_integration_readiness;
CREATE FUNCTION control.transition(p_session text,p_workspace uuid,p_version integer,p_status text,p_reason text,p_instance jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb; checks jsonb; c jsonb;
BEGIN
 -- Original transition locks the workspace, checks sensitive MFA/CAS/identity,
 -- and writes audit. A failed readiness check rolls the entire statement back.
 result:=control.transition_before_integration_readiness(p_session,p_workspace,p_version,p_status,p_reason);
 IF p_status='active' THEN
  checks:=control.configuration_status(p_session,p_workspace,p_instance)->'configuration_readiness'->'checks';
  FOR c IN SELECT * FROM jsonb_array_elements(checks) LOOP
   IF c->'required'='true'::jsonb AND c->'configuration_ready' IS DISTINCT FROM 'true'::jsonb THEN
    RAISE EXCEPTION '%',CASE c->>'code' WHEN 'text' THEN 'AI_CONFIGURATION_REQUIRED' ELSE 'FEISHU_CONFIGURATION_REQUIRED' END USING ERRCODE='P7409';
   END IF;
  END LOOP;
 END IF;
 RETURN result;
END $$;
-- The earlier signature remains fail-closed for callers without instance facts.
CREATE FUNCTION control.transition(p_session text,p_workspace uuid,p_version integer,p_status text,p_reason text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT control.transition(p_session,p_workspace,p_version,p_status,p_reason,NULL::jsonb);
$$;

ALTER FUNCTION control.save_modules(text,uuid,integer,jsonb,text) RENAME TO save_modules_before_integration_readiness;
CREATE FUNCTION control.save_modules(p_session text,p_workspace uuid,p_version integer,p_modules jsonb,p_reason text,p_instance jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE old jsonb; result jsonb; checks jsonb; c jsonb; was_enabled boolean;
BEGIN
 PERFORM control.require_sensitive(p_session);
 PERFORM 1 FROM platform.workspace WHERE id=p_workspace AND deleted_at IS NULL FOR UPDATE;
 old:=control.configuration_status(p_session,p_workspace,p_instance)->'configuration_readiness'->'checks';
 result:=control.save_modules_before_integration_readiness(p_session,p_workspace,p_version,p_modules,p_reason);
 checks:=control.configuration_status(p_session,p_workspace,p_instance)->'configuration_readiness'->'checks';
 FOR c IN SELECT * FROM jsonb_array_elements(checks) LOOP
  SELECT COALESCE((x->>'required')::boolean,false) INTO was_enabled
   FROM jsonb_array_elements(old) x WHERE x->>'code'=c->>'code';
  -- Always permit disabling/revoking. Unchanged legacy modules do not block
  -- unrelated edits; a newly enabled or renewed integration must be configured.
  IF c->'required'='true'::jsonb AND was_enabled IS DISTINCT FROM true
     AND c->'configuration_ready' IS DISTINCT FROM 'true'::jsonb THEN
   RAISE EXCEPTION '%',CASE c->>'code' WHEN 'text' THEN 'AI_CONFIGURATION_REQUIRED' ELSE 'FEISHU_CONFIGURATION_REQUIRED' END USING ERRCODE='P7409';
  END IF;
 END LOOP;
 RETURN result;
END $$;
CREATE FUNCTION control.save_modules(p_session text,p_workspace uuid,p_version integer,p_modules jsonb,p_reason text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT control.save_modules(p_session,p_workspace,p_version,p_modules,p_reason,NULL::jsonb);
$$;
REVOKE ALL ON FUNCTION control.configuration_status(text,uuid,jsonb),control.transition(text,uuid,integer,text,text,jsonb),
 control.transition(text,uuid,integer,text,text),control.transition_before_integration_readiness(text,uuid,integer,text,text),
 control.save_modules(text,uuid,integer,jsonb,text,jsonb),control.save_modules(text,uuid,integer,jsonb,text),
 control.save_modules_before_integration_readiness(text,uuid,integer,jsonb,text) FROM PUBLIC,salegent_feishu_worker;

ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_integration_readiness;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record; f record;
BEGIN
 total:=security.reconcile_runtime_grants_before_integration_readiness();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION control.transition_before_integration_readiness(text,uuid,integer,text,text),control.save_modules_before_integration_readiness(text,uuid,integer,jsonb,text) FROM %I',r.rolname);
  IF has_schema_privilege(r.rolname,'control','USAGE') AND has_function_privilege(r.rolname,'control.tenants(text)','EXECUTE') THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION control.configuration_status(text,uuid,jsonb),control.transition(text,uuid,integer,text,text,jsonb),control.save_modules(text,uuid,integer,jsonb,text,jsonb) TO %I',r.rolname);
  END IF;
 END LOOP;
 FOR f IN SELECT p.oid::regprocedure AS signature,pg_get_userbyid(p.proowner) AS owner_name FROM pg_proc p
  WHERE p.oid IN ('control.transition_before_integration_readiness(text,uuid,integer,text,text)'::regprocedure,
   'control.save_modules_before_integration_readiness(text,uuid,integer,jsonb,text)'::regprocedure) LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',f.signature,f.owner_name);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_integration_readiness() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V325','Tenant integration configuration readiness without blocking base activation');
COMMIT;
