-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V193__inference_audit_completion.sql.
-- Original SHA-256: 1af1b03d675afea0e8701841aebcd402db91c3d458da3848b2d6527f6a051b4b. See shenma_whole_121_manifest.json.
BEGIN;

-- The API intentionally cannot SELECT private inference receipts. A direct
-- UPDATE ... WHERE id/status therefore fails even with UPDATE permission.
-- Keep that read boundary and expose only an owned running -> terminal receipt.
CREATE FUNCTION security.finish_inference_operation(
 p_operation uuid,p_status text,p_trace jsonb,p_events jsonb,p_error_code text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id(); actor uuid:=common.current_user_ref_id();
 role_code text:=common.current_role_code(); affected integer;
BEGIN
 IF p_status IS NULL OR p_status NOT IN ('accepted','failed','cancelled','reconciliation_required')
  OR jsonb_typeof(p_trace) IS DISTINCT FROM 'object'
  OR jsonb_typeof(p_events) IS DISTINCT FROM 'array'
  OR octet_length(p_trace::text)>131072 OR octet_length(p_events::text)>131072
  OR length(p_error_code)>200 THEN
  RAISE EXCEPTION 'INVALID_INFERENCE_COMPLETION' USING ERRCODE='22023';
 END IF;
 -- The successful start captured this trusted-service identity. Completion is
 -- accounting, not renewed authorization: revocation/tenant suspension during a
 -- call must still allow its failed/cancelled receipt to finish. No cached grant
 -- JSON is consulted, caller-supplied identity is not accepted, and no business
 -- write or private receipt data is exposed by this function.
 IF ws IS NULL OR actor IS NULL OR role_code IS NULL THEN RETURN false; END IF;
 UPDATE agent.inference_operation SET status=p_status,trace=p_trace,events=p_events,
  error_code=p_error_code,completed_at=clock_timestamp()
 WHERE id=p_operation AND workspace_id=ws AND actor_user_ref_id=actor
  AND actor_role_code=role_code AND status='running' AND completed_at IS NULL;
 GET DIAGNOSTICS affected=ROW_COUNT;
 RETURN affected=1;
END $$;
REVOKE ALL ON FUNCTION security.finish_inference_operation(uuid,text,jsonb,jsonb,text)
 FROM PUBLIC,salegent_feishu_worker;
COMMENT ON FUNCTION security.finish_inference_operation(uuid,text,jsonb,jsonb,text)
 IS 'Finish one owned running inference receipt; no private reads, identity edits, business writes or terminal rewrites';

ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_inference_finish;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; runtime record; columns text;
BEGIN
 total:=security.reconcile_runtime_grants_before_inference_finish();
 SELECT string_agg(quote_ident(attname),',' ORDER BY attnum) INTO columns
  FROM pg_attribute WHERE attrelid='agent.inference_operation'::regclass AND attnum>0 AND NOT attisdropped;
 FOR runtime IN SELECT r.rolname FROM pg_roles r
  WHERE NOT r.rolsuper AND NOT r.rolbypassrls AND left(r.rolname,3)<>'pg_' AND r.rolname<>'salegent_feishu_worker'
   AND r.oid<>(SELECT relowner FROM pg_class WHERE oid='agent.inference_operation'::regclass)
   AND has_schema_privilege(r.oid,'agent','USAGE') AND has_schema_privilege(r.oid,'security','USAGE')
   AND has_table_privilege(r.oid,'agent.inference_operation','INSERT')
 LOOP
  EXECUTE format('REVOKE UPDATE ON agent.inference_operation FROM %I',runtime.rolname);
  EXECUTE format('REVOKE UPDATE(%s) ON agent.inference_operation FROM %I',columns,runtime.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.finish_inference_operation(uuid,text,jsonb,jsonb,text) TO %I',runtime.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_inference_finish()
 FROM PUBLIC,salegent_feishu_worker;
-- CREATE/RENAME can preserve prior role ACLs. Reconciliation is maintenance-only.
DO $$ DECLARE signature text; grantee record; BEGIN
 FOREACH signature IN ARRAY ARRAY['security.finish_inference_operation(uuid,text,jsonb,jsonb,text)',
  'security.reconcile_runtime_grants()',
  'security.reconcile_runtime_grants_before_inference_finish()'] LOOP
  FOR grantee IN SELECT DISTINCT r.rolname FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
   JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=signature::regprocedure AND a.grantee<>p.proowner LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',signature,grantee.rolname);
  END LOOP;
 END LOOP;
END $$;
-- First application changes only this migration's narrow permission contract.
-- Existing installations may already have reviewed canonical runtime ACLs;
-- replaying historical reconciliation here would restore deliberately omitted
-- permissions. The wrapper above remains available for explicit maintenance.
DO $$ DECLARE runtime record; columns text; BEGIN
 SELECT string_agg(quote_ident(attname),',' ORDER BY attnum) INTO columns
  FROM pg_attribute WHERE attrelid='agent.inference_operation'::regclass AND attnum>0 AND NOT attisdropped;
 FOR runtime IN SELECT r.rolname FROM pg_roles r
  WHERE NOT r.rolsuper AND NOT r.rolbypassrls AND left(r.rolname,3)<>'pg_' AND r.rolname<>'salegent_feishu_worker'
   AND r.oid<>(SELECT relowner FROM pg_class WHERE oid='agent.inference_operation'::regclass)
   AND has_schema_privilege(r.oid,'agent','USAGE') AND has_schema_privilege(r.oid,'security','USAGE')
   AND has_table_privilege(r.oid,'agent.inference_operation','INSERT')
 LOOP
  EXECUTE format('REVOKE UPDATE ON agent.inference_operation FROM %I',runtime.rolname);
  EXECUTE format('REVOKE UPDATE(%s) ON agent.inference_operation FROM %I',columns,runtime.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.finish_inference_operation(uuid,text,jsonb,jsonb,text) TO %I',runtime.rolname);
 END LOOP;
END $$;
INSERT INTO ops.schema_migration(version,description)
 VALUES('V341','Owned inference receipt completion without runtime direct update or extra private reads');
COMMIT;
