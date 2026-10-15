-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V184__api_target_scope_write_acl.sql.
-- Original SHA-256: 1a1ceb952c4b7f34f8dd11c1642f1e5b4a9de9cb572a7e4ec9efaaddbaffc0e4. See shenma_whole_121_manifest.json.
BEGIN;

-- Console target selectors and action flags evaluate this existing boolean
-- authorization helper directly. Expose only its four-argument API contract;
-- the helper still evaluates the caller's tenant, feature and subject scopes.
REVOKE ALL ON FUNCTION security.target_scope_write(text,uuid,uuid,text)
 FROM PUBLIC,salegent_feishu_worker;

ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_target_scope_write;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record; is_api boolean;
BEGIN
 total:=security.reconcile_runtime_grants_before_target_scope_write();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'security','USAGE') LOOP
  is_api:=has_function_privilege(r.rolname,'security.save_account_authorization(uuid,jsonb)','EXECUTE');
  EXECUTE format('REVOKE ALL ON FUNCTION security.target_scope_write(text,uuid,uuid,text),security.reconcile_runtime_grants_before_target_scope_write(),security.reconcile_runtime_grants() FROM %I',r.rolname);
  IF is_api THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION security.target_scope_write(text,uuid,uuid,text) TO %I',r.rolname);
  END IF;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_target_scope_write()
 FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V332','API-only execution of scoped target write checks');
COMMIT;
