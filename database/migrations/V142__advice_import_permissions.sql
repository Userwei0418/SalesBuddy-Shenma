BEGIN;

CREATE FUNCTION security.authorization_advice_subject(p_permission text,p_kind text,p_subject uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT CASE p_kind WHEN 'customer' THEN security.authorization_customer(p_permission,p_subject)
 WHEN 'opportunity' THEN security.authorization_opportunity(p_permission,p_subject)
 WHEN 'visit' THEN security.authorization_visit(p_permission,p_subject) ELSE false END;
$$;
CREATE FUNCTION security.authorization_advice(p_permission text,p_advice uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM insight.business_advice a WHERE a.id=p_advice
 AND a.workspace_id=common.current_workspace_id() AND a.actor_user_ref_id=common.current_user_ref_id()
 AND a.identity_snapshot->>'permission_version'=security.authorization_snapshot()->>'permission_version'
 AND security.authorization_advice_subject(p_permission,a.subject_kind,a.subject_id)
 AND security.authorization_advice_subject('advice.'||a.subject_kind,a.subject_kind,a.subject_id));
$$;
DO $$ DECLARE p record; BEGIN
 FOR p IN SELECT * FROM pg_policies WHERE schemaname='insight'
 AND tablename IN ('business_advice','business_suggestion') LOOP
 EXECUTE format('DROP POLICY %I ON insight.%I',p.policyname,p.tablename); END LOOP;
END $$;
-- INSERT ... RETURNING evaluates SELECT policy before a STABLE table lookup
-- can observe the inserted tuple; validate the tuple's actual fields directly.
CREATE POLICY permission_read ON insight.business_advice FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND actor_user_ref_id=common.current_user_ref_id()
 AND identity_snapshot->>'permission_version'=security.authorization_snapshot()->>'permission_version'
 AND security.authorization_advice_subject('advice.read',subject_kind,subject_id)
 AND security.authorization_advice_subject('advice.'||subject_kind,subject_kind,subject_id));
CREATE POLICY permission_insert ON insight.business_advice FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND actor_user_ref_id=common.current_user_ref_id()
 AND identity_snapshot->>'permission_version'=security.authorization_snapshot()->>'permission_version'
 AND security.authorization_advice_subject('advice.request',subject_kind,subject_id)
 AND security.authorization_advice_subject('advice.'||subject_kind,subject_kind,subject_id));
CREATE POLICY permission_update ON insight.business_advice FOR UPDATE USING(
 security.authorization_advice('advice.request',id)) WITH CHECK(security.authorization_advice('advice.request',id));
CREATE POLICY permission_read ON insight.business_suggestion FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.authorization_advice('advice.read',advice_id));
CREATE POLICY permission_insert ON insight.business_suggestion FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.authorization_advice('advice.request',advice_id));
CREATE POLICY permission_update ON insight.business_suggestion FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND security.authorization_advice('advice.decide',advice_id))
 WITH CHECK(workspace_id=common.current_workspace_id() AND security.authorization_advice('advice.decide',advice_id));

-- An explicit collaboration-management grant replaces the old FDE veto. The
-- target must still be an active FDE in this tenant (existing identity trigger).
ALTER POLICY participant_insert ON crm.opportunity_participant WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.can_manage_fde_members(opportunity_id));
ALTER POLICY participant_update ON crm.opportunity_participant USING(
 workspace_id=common.current_workspace_id() AND security.can_manage_fde_members(opportunity_id))
 WITH CHECK(workspace_id=common.current_workspace_id() AND security.can_manage_fde_members(opportunity_id));

CREATE FUNCTION security.authorization_import(p_permission text,p_import uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM activity.visit_import i WHERE i.id=p_import
 AND i.workspace_id=common.current_workspace_id() AND (
  (i.created_by_user_ref_id=common.current_user_ref_id() AND security.authorization_allows(
   p_permission,i.workspace_id,i.created_by_user_ref_id,ARRAY[]::uuid[],true))
  OR (p_permission IN ('visit.read','visit.download_original') AND EXISTS(
   SELECT 1 FROM activity.visit v WHERE v.source_import_id=i.id AND v.workspace_id=i.workspace_id
   AND v.status IN ('confirmed','archived') AND v.deleted_at IS NULL
   AND security.authorization_visit(p_permission,v.id)))));
$$;
DO $$ DECLARE p record; BEGIN
 FOR p IN SELECT * FROM pg_policies WHERE schemaname='activity' AND tablename='visit_import' LOOP
 EXECUTE format('DROP POLICY %I ON activity.visit_import',p.policyname); END LOOP;
END $$;
CREATE POLICY permission_read ON activity.visit_import FOR SELECT USING(
 security.authorization_import('visit.read',id) OR security.authorization_import('visit.upload',id)
 OR security.authorization_import('visit.download_original',id));
CREATE POLICY permission_insert ON activity.visit_import FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND created_by_user_ref_id=common.current_user_ref_id()
 AND security.authorization_has('visit.upload'));
CREATE POLICY permission_update ON activity.visit_import FOR UPDATE USING(
 created_by_user_ref_id=common.current_user_ref_id() AND security.authorization_import('visit.upload',id))
 WITH CHECK(created_by_user_ref_id=common.current_user_ref_id() AND security.authorization_import('visit.upload',id));

ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v140;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_v140();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
 AND rolname NOT LIKE 'pg\_%' ESCAPE '\' AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.authorization_advice_subject(text,text,uuid),security.authorization_advice(text,uuid),security.authorization_import(text,uuid) TO %I',r.rolname);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_v140(),
 security.authorization_advice_subject(text,text,uuid),security.authorization_advice(text,uuid),security.authorization_import(text,uuid)
 FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V142','Scoped advice actions, materials and explicitly granted FDE collaboration');
COMMIT;
