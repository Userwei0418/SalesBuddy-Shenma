BEGIN;
-- Separate the existing reviewed history executor from ordinary opportunity
-- creation. A flag alone cannot authorize a write: a current grant and an
-- approved record with the exact target in this tenant are both required.
CREATE FUNCTION security.authorization_history_target(p_kind text,p_target uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT current_setting('app.feishu_historical_import',true)='on'
 AND security.management_actor() AND security.authorization_has('history.import')
 AND EXISTS(SELECT 1 FROM ops.crm_import_record r JOIN ops.crm_import_batch b
  ON b.id=r.batch_id AND b.workspace_id=r.workspace_id
  WHERE r.workspace_id=common.current_workspace_id() AND r.object_kind=p_kind AND r.target_id=p_target
   AND r.status IN ('approved','applied','unchanged') AND b.status IN ('approved','applying','applied'));
$$;
DO $$ DECLARE relation text; BEGIN
 FOREACH relation IN ARRAY ARRAY['ops.crm_import_batch','ops.crm_import_record','ops.crm_import_binding'] LOOP
  EXECUTE format('CREATE POLICY permission_import ON %s AS RESTRICTIVE USING (security.authorization_has(''history.import'')) WITH CHECK (security.authorization_has(''history.import''))',relation);
 END LOOP;
END $$;
-- Keep reviewed-version/source checks in the existing verifier, before any write.
ALTER FUNCTION security.check_crm_import_target(uuid,uuid) RENAME TO check_crm_import_target_v121;
CREATE FUNCTION security.check_crm_import_target(p_record uuid,p_target uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT security.authorization_has('history.import') THEN RAISE insufficient_privilege; END IF;
 RETURN security.check_crm_import_target_v121(p_record,p_target);
END $$;
CREATE POLICY permission_history_insert ON crm.opportunity FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND created_by_user_ref_id=common.current_user_ref_id()
 AND import_meta->>'import_type'='crm_history' AND security.authorization_history_target('opportunity',id)
 AND EXISTS(SELECT 1 FROM ops.crm_import_record r WHERE r.workspace_id=crm.opportunity.workspace_id
  AND r.target_id=crm.opportunity.id AND r.object_kind='opportunity'
  AND r.id::text=import_meta->>'import_record_id' AND r.batch_id::text=import_meta->>'import_batch_id'
  AND r.status IN ('approved','applied','unchanged')));
CREATE POLICY permission_history_update ON crm.opportunity FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND security.authorization_history_target('opportunity',id)) WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.authorization_history_target('opportunity',id));
CREATE OR REPLACE FUNCTION security.authorization_opportunity_mutation(p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_opportunity('opportunity.update',p_opportunity) OR EXISTS(
  SELECT 1 FROM crm.opportunity o WHERE o.id=p_opportunity AND o.workspace_id=common.current_workspace_id()
  AND o.created_by_user_ref_id=common.current_user_ref_id() AND pg_xact_status(o.xmin::text::xid8)='in progress' AND o.created_at>=transaction_timestamp()
  AND security.authorization_allows('opportunity.create',o.workspace_id,o.owner_user_ref_id,ARRAY[o.owner_team_id],
   o.owner_user_ref_id=common.current_user_ref_id())) OR security.authorization_history_target('opportunity',p_opportunity);
$$;
-- Historical follow-up bridges use the approved visit identity, not a new
-- recording by the importing operator. Immutable original author guards remain.
CREATE OR REPLACE FUNCTION security.can_write_visit(p_visit uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_visit('visit.supplement',p_visit) OR security.authorization_visit_created(p_visit) OR EXISTS(SELECT 1 FROM activity.visit v WHERE v.id=p_visit AND v.workspace_id=common.current_workspace_id()
  AND v.deleted_at IS NULL AND v.status IN ('draft','pending_confirm','confirmed')
  AND v.created_by_user_ref_id=common.current_user_ref_id() AND v.recorder_user_ref_id=common.current_user_ref_id()
  AND (v.confirmed_by_user_ref_id IS NULL OR v.confirmed_by_user_ref_id=common.current_user_ref_id())
  AND security.authorization_visit_target('visit.create',v.customer_id,v.opportunity_id,v.recorder_user_ref_id,v.recorder_team_id))
 OR security.authorization_history_target('visit',p_visit);
$$;
CREATE POLICY permission_history_update ON activity.visit FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND security.authorization_history_target('visit',id)) WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.authorization_history_target('visit',id));
ALTER POLICY visit_historical_multi_insert ON activity.visit WITH CHECK(
 workspace_id=common.current_workspace_id() AND import_meta->>'import_type'='crm_history'
 AND security.authorization_history_target('visit',id)
 AND EXISTS(SELECT 1 FROM ops.crm_import_record r WHERE r.workspace_id=activity.visit.workspace_id
  AND r.target_id=activity.visit.id AND r.object_kind='visit'
  AND r.id::text=import_meta->>'import_record_id' AND r.batch_id::text=import_meta->>'import_batch_id'
  AND r.status IN ('approved','applied','unchanged')));
ALTER POLICY period_snapshot_import ON crm.opportunity_period_actual_snapshot WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.authorization_history_target('period_actual_snapshot',id)
 AND EXISTS(SELECT 1 FROM ops.crm_import_record r WHERE r.workspace_id=opportunity_period_actual_snapshot.workspace_id
  AND r.id=opportunity_period_actual_snapshot.source_record_id AND r.batch_id=opportunity_period_actual_snapshot.import_batch_id AND r.target_id=opportunity_period_actual_snapshot.id
  AND r.object_kind='period_actual_snapshot' AND r.status IN ('approved','applied','unchanged')));
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v144;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_v144();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
 AND rolname NOT LIKE 'pg\_%' ESCAPE '\' AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.authorization_history_target(text,uuid),security.authorization_opportunity_mutation(uuid),security.check_crm_import_target(uuid,uuid) TO %I',r.rolname);
  IF r.rolname<>current_user THEN
   EXECUTE format('REVOKE ALL ON FUNCTION security.check_crm_import_target_v121(uuid,uuid) FROM %I',r.rolname);
  END IF;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.authorization_history_target(text,uuid),security.authorization_opportunity_mutation(uuid),
 security.check_crm_import_target(uuid,uuid),security.check_crm_import_target_v121(uuid,uuid),
 security.reconcile_runtime_grants(),security.reconcile_runtime_grants_v144() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V145','Authorize approved historical imports separately from live opportunity creation');
COMMIT;
