-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V176__model_credential_source_projection.sql.
-- Original SHA-256: 45650e8a67f041ff80c7c31d03ea88ee5037420ff11005b272d47a23cef6fd6d. See shenma_whole_121_manifest.json.
BEGIN;
-- Sales may read the published runtime binding, but must not read administrator
-- test receipts. Expose only a current-company credential-origin boolean.
CREATE FUNCTION security.model_credential_owned(p_purpose text,p_version integer) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT common.current_user_ref_id() IS NOT NULL
 AND security.workspace_is_active(common.current_workspace_id())
 AND EXISTS(SELECT 1 FROM config.model_api_release r
  JOIN config.model_api_test t ON t.workspace_id=r.workspace_id AND t.id=r.test_id
  WHERE r.workspace_id=common.current_workspace_id() AND r.purpose=p_purpose AND r.version_no=p_version
   AND r.config_snapshot->>'mode'='custom' AND octet_length(r.api_key_ciphertext)>0 AND t.source_guard IS NULL);
$$;
REVOKE ALL ON FUNCTION security.model_credential_owned(text,integer) FROM PUBLIC,salegent_feishu_worker;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_model_source_projection;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_before_model_source_projection();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper
  AND has_schema_privilege(oid,'security','USAGE')
  AND has_table_privilege(oid,'config.model_api_release','SELECT') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.model_credential_owned(text,integer) TO %I',r.rolname);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_model_source_projection()
 FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description)
 VALUES('V324','Project company credential origin without exposing admin test records to sales');
COMMIT;
