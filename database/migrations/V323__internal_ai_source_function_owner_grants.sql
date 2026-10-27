-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V175__ai_source_function_owner_grants.sql.
-- Original SHA-256: 225748ecd7d142ed5dd372387602a68941a67a30ff481e96130031aaecf481f2. See shenma_whole_121_manifest.json.
BEGIN;
-- Reconciliation must retain execution by a non-superuser migration owner.
-- Runtime roles remain unable to call these internal authority helpers.
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_ai_source_owners;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; f record;
BEGIN
 total:=security.reconcile_runtime_grants_before_ai_source_owners();
 FOR f IN SELECT p.oid::regprocedure AS signature,pg_get_userbyid(p.proowner) AS owner_name
  FROM pg_proc p WHERE p.oid IN (
   'security.assert_ai_service_source(uuid)'::regprocedure,
   'control.initialize_ai_service()'::regprocedure,
   'control.configuration_status_before_ai_service(text,uuid)'::regprocedure) LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',f.signature,f.owner_name);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_ai_source_owners()
 FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description)
 VALUES('V323','Retain private AI source helper execution by its migration owner');
COMMIT;
