BEGIN;
CREATE OR REPLACE FUNCTION security.authorization_opportunity_direct(p_permission text, p_opportunity uuid)
 RETURNS boolean
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 SELECT COALESCE((SELECT security.authorization_allows(p_permission,o.workspace_id,o.owner_user_ref_id,
  ARRAY[o.owner_team_id] || ARRAY(
   SELECT DISTINCT tm.team_id FROM crm.opportunity_participant p
   JOIN platform.team_membership tm ON tm.user_ref_id=p.user_ref_id AND tm.workspace_id=p.workspace_id
   JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
   WHERE p.workspace_id=o.workspace_id AND (p.opportunity_id=o.id OR (false AND EXISTS(
    SELECT 1 FROM crm.opportunity related WHERE related.id=p.opportunity_id AND related.customer_id=o.customer_id
     AND related.workspace_id=o.workspace_id AND related.deleted_at IS NULL)))
    AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
    AND security.fde_user_is_active(p.user_ref_id)
    AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
    AND t.status='active' AND t.deleted_at IS NULL),
  o.owner_user_ref_id=common.current_user_ref_id() OR EXISTS(
   SELECT 1 FROM crm.opportunity_participant p JOIN platform.user_ref u ON u.id=p.user_ref_id AND u.workspace_id=p.workspace_id
   WHERE p.workspace_id=o.workspace_id AND p.user_ref_id=common.current_user_ref_id()
   AND u.status='active' AND u.deleted_at IS NULL
   AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
   AND (p.opportunity_id=o.id OR (false AND EXISTS(
    SELECT 1 FROM crm.opportunity related WHERE related.id=p.opportunity_id AND related.customer_id=o.customer_id
     AND related.workspace_id=o.workspace_id AND related.deleted_at IS NULL)))))
 FROM crm.opportunity o WHERE o.id=p_opportunity AND o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL),false);
$function$;

-- Scope checks must use the same participant teams before and after an update.
-- The API does not support moving opportunity ownership between people/teams.
ALTER POLICY permission_update ON crm.opportunity WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.authorization_opportunity('opportunity.update',id)
 AND security.customer_reference(customer_id) IS NOT NULL);
CREATE FUNCTION crm.guard_opportunity_permission_transition() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) THEN RETURN NEW; END IF;
 IF (NEW.workspace_id,NEW.customer_id,NEW.owner_user_ref_id,NEW.owner_team_id,NEW.created_by_user_ref_id)
 IS DISTINCT FROM (OLD.workspace_id,OLD.customer_id,OLD.owner_user_ref_id,OLD.owner_team_id,OLD.created_by_user_ref_id) THEN
  RAISE EXCEPTION '商机归属不可通过普通修改变更' USING ERRCODE='42501'; END IF;
 IF NEW.status IS DISTINCT FROM OLD.status AND NOT security.authorization_opportunity(
  CASE WHEN NEW.status='open' THEN 'opportunity.reopen' ELSE 'opportunity.close' END,OLD.id) THEN RAISE insufficient_privilege; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_opportunity_permission BEFORE UPDATE ON crm.opportunity
 FOR EACH ROW EXECUTE FUNCTION crm.guard_opportunity_permission_transition();
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v136;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_v136();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
 AND rolname NOT LIKE 'pg\_%' ESCAPE '\' AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.authorization_opportunity_direct(text,uuid) TO %I',r.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_v136(),
 security.authorization_opportunity_direct(text,uuid) FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V137','Union-scoped opportunity lists and independent status transition guards');
COMMIT;
