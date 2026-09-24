BEGIN;


-- The API installs one server-mapped feature for the lifetime of the request.
-- RLS evaluates that feature's own grants. A dashboard grant can authorize an
-- aggregate without silently granting the customer-detail or opportunity APIs.
-- Mutation and administrative actions cannot select an arbitrary projection.
CREATE FUNCTION security.authorization_read_feature(p_resource text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT CASE WHEN current_setting('app.authorized_feature',true)=ANY(ARRAY[
  'overview.read','battle_map.read','dashboard.read','dashboard.ranking','profile.sales_read','profile.fde_read',
  'profile.fde_activity','demo_scene.read','actual.read','customer.export','opportunity.export',
  'advice.customer','advice.opportunity','advice.visit','risk.auto_review','risk.read','risk.resolve','visit.download_original',
  'agent.chatbi','agent.customer_chatbi','agent.operating_report','agent.today_tasks','agent.personal_risks'
 ]) THEN current_setting('app.authorized_feature',true) ELSE p_resource||'.read' END;
$$;
DROP POLICY permission_read ON crm.customer;
CREATE POLICY permission_read ON crm.customer FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND deleted_at IS NULL AND (
 security.authorization_customer(security.authorization_read_feature('customer'),id)
 OR security.authorization_allows(security.authorization_read_feature('customer'),workspace_id,owner_user_ref_id,ARRAY[owner_team_id],owner_user_ref_id=common.current_user_ref_id())));
DROP POLICY permission_read ON crm.opportunity;
CREATE POLICY permission_read ON crm.opportunity FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND deleted_at IS NULL AND (
 security.authorization_opportunity(security.authorization_read_feature('opportunity'),id)
 OR security.authorization_allows(security.authorization_read_feature('opportunity'),workspace_id,owner_user_ref_id,ARRAY[owner_team_id],owner_user_ref_id=common.current_user_ref_id())));
DROP POLICY permission_read ON workflow.task;
CREATE POLICY permission_read ON workflow.task FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND (
 security.authorization_task(security.authorization_read_feature('task'),id)
 OR security.authorization_allows(security.authorization_read_feature('task'),workspace_id,creator_user_ref_id,ARRAY[creator_team_id],false)));
DROP POLICY permission_read ON activity.visit;
CREATE POLICY permission_read ON activity.visit FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND (
 security.authorization_visit(security.authorization_read_feature('visit'),id)
 OR (created_by_user_ref_id=common.current_user_ref_id() AND recorder_user_ref_id=common.current_user_ref_id()
 AND security.authorization_visit_target(security.authorization_read_feature('visit'),customer_id,opportunity_id,recorder_user_ref_id,recorder_team_id))
 OR (opportunity_id IS NULL AND security.authorization_allows(security.authorization_read_feature('visit'),workspace_id,recorder_user_ref_id,ARRAY[recorder_team_id],false))));

ALTER POLICY permission_read ON workflow.task_assignee USING(workspace_id=common.current_workspace_id() AND security.authorization_task(security.authorization_read_feature('task'),task_id));
ALTER POLICY permission_read ON workflow.task_candidate USING(workspace_id=common.current_workspace_id() AND security.authorization_task(security.authorization_read_feature('task'),task_id));
ALTER POLICY permission_read ON workflow.task_event USING(workspace_id=common.current_workspace_id() AND security.authorization_task(security.authorization_read_feature('task'),task_id));
ALTER POLICY permission_read ON activity.visit_field_value USING(workspace_id=common.current_workspace_id() AND security.authorization_visit(security.authorization_read_feature('visit'),visit_id));
ALTER POLICY permission_read ON activity.visit_contact USING(workspace_id=common.current_workspace_id() AND security.authorization_visit(security.authorization_read_feature('visit'),visit_id));
ALTER POLICY permission_read ON activity.visit_participant USING(workspace_id=common.current_workspace_id() AND security.authorization_visit(security.authorization_read_feature('visit'),visit_id));
ALTER POLICY permission_read ON activity.visit_opportunity USING(workspace_id=common.current_workspace_id() AND security.authorization_visit(security.authorization_read_feature('visit'),visit_id));

-- Related read-only rows (forecasts, product links, attachments, risks) follow
-- the same projected parent. Write predicates deliberately remain unchanged.
DO $$ DECLARE p record; expression text; BEGIN
 FOR p IN SELECT * FROM pg_policies WHERE cmd='SELECT' AND schemaname IN ('crm','activity','insight','workflow')
 AND (qual LIKE '%security.has_opportunity_read_access(%' OR qual LIKE '%security.has_customer_access(%') LOOP
  expression:=replace(replace(p.qual,'security.has_opportunity_read_access(',
   'security.authorization_opportunity(security.authorization_read_feature(''opportunity''),'),
   'security.has_customer_access(','security.authorization_customer(security.authorization_read_feature(''customer''),');
  EXECUTE format('ALTER POLICY %I ON %I.%I USING (%s)',p.policyname,p.schemaname,p.tablename,expression);
 END LOOP;
END $$;


ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v133;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_v133();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
 AND rolname NOT LIKE 'pg\_%' ESCAPE '\' AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.authorization_read_feature(text) TO %I',r.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_v133(),security.authorization_read_feature(text) FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V134','Feature-specific projections without granting unrelated detail access');
COMMIT;
