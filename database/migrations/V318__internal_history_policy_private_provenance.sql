-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V170__history_policy_private_provenance.sql.
-- Original SHA-256: 5cbf713b11059640ab7f8bb029238f52a52d8d068e9612395a10de40bbb0ea6f. See shenma_whole_121_manifest.json.
BEGIN;
-- RLS checks all referenced relations even for an ordinary (non-import) INSERT.
-- Keep the private import ledger behind a boolean, tenant/permission-bound
-- definer function rather than granting every runtime login access to that ledger.
CREATE FUNCTION security.authorization_history_provenance(
 p_kind text,p_target uuid,p_record text,p_batch text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_history_target(p_kind,p_target)
 AND EXISTS(SELECT 1 FROM ops.crm_import_record r
  WHERE r.workspace_id=common.current_workspace_id()
   AND r.object_kind=p_kind AND r.target_id=p_target
   AND r.id::text=p_record AND r.batch_id::text=p_batch
   AND r.status IN ('approved','applied','unchanged'));
$$;
ALTER POLICY permission_history_insert ON crm.opportunity WITH CHECK(
 workspace_id=common.current_workspace_id()
 AND created_by_user_ref_id=common.current_user_ref_id()
 AND import_meta->>'import_type'='crm_history'
 AND security.authorization_history_provenance('opportunity',id,
  import_meta->>'import_record_id',import_meta->>'import_batch_id'));
ALTER POLICY visit_historical_multi_insert ON activity.visit WITH CHECK(
 workspace_id=common.current_workspace_id()
 AND import_meta->>'import_type'='crm_history'
 AND security.authorization_history_provenance('visit',id,
  import_meta->>'import_record_id',import_meta->>'import_batch_id'));
ALTER POLICY period_snapshot_import ON crm.opportunity_period_actual_snapshot WITH CHECK(
 workspace_id=common.current_workspace_id()
 AND security.authorization_history_provenance('period_actual_snapshot',id,
  source_record_id::text,import_batch_id::text));
ALTER FUNCTION security.reconcile_runtime_grants()
 RENAME TO reconcile_runtime_grants_before_history_provenance;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_before_history_provenance();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper
 AND rolname NOT LIKE 'pg\_%' ESCAPE '\'
 AND NOT pg_has_role(oid,'salegent_feishu_worker','member')
 AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.authorization_history_target(text,uuid)','EXECUTE') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants_before_history_provenance() FROM %I',r.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.authorization_history_provenance(text,uuid,text,text) TO %I',r.rolname);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.authorization_history_provenance(text,uuid,text,text),
 security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_history_provenance()
 FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description)
 VALUES('V318','Keep historical provenance checks private for restricted runtime inserts');
COMMIT;
