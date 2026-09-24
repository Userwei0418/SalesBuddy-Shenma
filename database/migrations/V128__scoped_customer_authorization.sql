BEGIN;

CREATE FUNCTION security.authorization_customer(p_permission text,p_customer uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE((SELECT security.authorization_allows(p_permission,c.workspace_id,
  CASE WHEN own.state='claimed' AND own.owner_user_ref_id=common.current_user_ref_id() OR EXISTS(
   SELECT 1 FROM crm.customer_sales_member m WHERE m.customer_id=c.id AND m.workspace_id=c.workspace_id
    AND m.user_ref_id=common.current_user_ref_id()) THEN common.current_user_ref_id() ELSE c.owner_user_ref_id END,
  ARRAY[c.owner_team_id] || ARRAY(SELECT DISTINCT tm.team_id FROM crm.customer_sales_member m
   JOIN platform.team_membership tm ON tm.user_ref_id=m.user_ref_id AND tm.workspace_id=m.workspace_id
   JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
   WHERE m.customer_id=c.id AND m.workspace_id=c.workspace_id AND t.status='active' AND t.deleted_at IS NULL
    AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to)
  || ARRAY(SELECT DISTINCT tm.team_id FROM crm.opportunity o JOIN crm.opportunity_participant p
    ON p.opportunity_id=o.id AND p.workspace_id=o.workspace_id
   JOIN platform.team_membership tm ON tm.user_ref_id=p.user_ref_id AND tm.workspace_id=p.workspace_id
   JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
   WHERE o.customer_id=c.id AND o.workspace_id=c.workspace_id AND o.deleted_at IS NULL
    AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
    AND security.fde_user_is_active(p.user_ref_id)
    AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to AND t.status='active' AND t.deleted_at IS NULL),
  EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.customer_id=c.id AND o.workspace_id=c.workspace_id AND o.deleted_at IS NULL
   AND (o.owner_user_ref_id=common.current_user_ref_id() OR EXISTS(SELECT 1 FROM crm.opportunity_participant p
    WHERE p.opportunity_id=o.id AND p.workspace_id=o.workspace_id AND p.user_ref_id=common.current_user_ref_id()
     AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to))))
 FROM crm.customer c LEFT JOIN crm.customer_ownership own ON own.customer_id=c.id AND own.workspace_id=c.workspace_id
 WHERE c.id=p_customer AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL),false);
$$;
CREATE OR REPLACE FUNCTION security.has_customer_access(p_customer_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_customer('customer.read',p_customer_id);
$$;
CREATE OR REPLACE FUNCTION security.has_customer_write_access(p_customer_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_customer('customer.update',p_customer_id);
$$;
CREATE OR REPLACE FUNCTION security.customer_reference(p_customer_id uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',c.id,'name',c.name,'customer_type_code',c.customer_type_code,'owner_team_id',c.owner_team_id)
 FROM crm.customer c WHERE c.id=p_customer_id AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
 AND security.authorization_allows('customer.reference',c.workspace_id,c.owner_user_ref_id,ARRAY[c.owner_team_id],
  security.authorization_customer('customer.read',c.id));
$$;
-- General customer creation creates an unclaimed directory record. Ownership is
-- still granted through claim approval; a permission grant is not a claim.
DO $$ DECLARE p record; BEGIN
 FOR p IN SELECT policyname FROM pg_policies WHERE schemaname='crm' AND tablename='customer' LOOP
  EXECUTE format('DROP POLICY %I ON crm.customer',p.policyname);
 END LOOP;
END $$;
CREATE POLICY permission_read ON crm.customer FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND deleted_at IS NULL AND (
 security.authorization_allows('customer.read',workspace_id,owner_user_ref_id,ARRAY[owner_team_id],false)
 OR security.authorization_customer('customer.read',id)));
CREATE POLICY permission_insert ON crm.customer FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND created_by_user_ref_id=common.current_user_ref_id()
 AND security.authorization_allows('customer.create',workspace_id,created_by_user_ref_id,ARRAY[owner_team_id],false));
CREATE POLICY permission_update ON crm.customer FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND security.authorization_customer('customer.update',id)) WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.authorization_customer('customer.update',id));

REVOKE ALL ON FUNCTION security.authorization_customer(text,uuid) FROM PUBLIC,salegent_feishu_worker;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v127;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_v127();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
 AND rolname NOT LIKE 'pg\_%' ESCAPE '\' AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.authorization_customer(text,uuid) TO %I',r.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_v127() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V128','Scoped configurable customer permissions');
COMMIT;
