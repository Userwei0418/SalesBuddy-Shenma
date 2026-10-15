-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V155__platform_runtime_acl.sql.
-- Original SHA-256: 0a91bc9685bade1ad8ad0f86f4065aca9d7bf9c8753917b8610b50507a23ede0. See shenma_whole_121_manifest.json.
BEGIN;
-- Broad bootstrap grants must not accidentally turn the tenant API into a
-- cross-tenant sync worker. Reapply this boundary after every grant reconciliation.
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_internal_v145;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_internal_v145();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND NOT rolbypassrls
  AND NOT pg_has_role(oid,'salegent_feishu_worker','member')
  AND has_schema_privilege(oid,'security','USAGE') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION security.feishu_lock_lease(uuid,uuid),security.reconcile_runtime_grants_internal_v145(),security.reconcile_runtime_grants() FROM %I',r.rolname);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_internal_v145()
 FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V303','Platform and tenant lifecycle runtime boundaries');
COMMIT;
