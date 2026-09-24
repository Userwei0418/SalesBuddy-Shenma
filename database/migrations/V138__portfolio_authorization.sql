BEGIN;
CREATE OR REPLACE FUNCTION security.authorization_portfolio_customer(p_permission text, p_customer uuid)
 RETURNS boolean
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 SELECT COALESCE((SELECT security.authorization_allows(p_permission,c.workspace_id,
  CASE WHEN own.state='claimed' THEN own.owner_user_ref_id END,
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
$function$;
CREATE OR REPLACE FUNCTION security.customer_portfolio_scope()
RETURNS TABLE(id uuid,workspace_id uuid,owner_team_id uuid,claimant_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH grants AS MATERIALIZED (
  SELECT * FROM security.authorization_current_grants() WHERE permission_code='actual.read'
 ), teams AS MATERIALIZED (
  SELECT DISTINCT unnest(team_ids) AS id FROM grants WHERE scope_code='teams' AND effect='allow'
 ), memberships AS MATERIALIZED (
  SELECT tm.user_ref_id,tm.team_id FROM platform.team_membership tm
  JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
  WHERE tm.workspace_id=common.current_workspace_id() AND t.status='active' AND t.deleted_at IS NULL
   AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
 ), projects AS MATERIALIZED (
  SELECT o.customer_id,p.user_ref_id FROM crm.opportunity o
  JOIN crm.opportunity_participant p ON p.opportunity_id=o.id AND p.workspace_id=o.workspace_id
  WHERE o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL
   AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
 ), team_customers AS MATERIALIZED (
  SELECT m.customer_id FROM crm.customer_sales_member m JOIN memberships tm ON tm.user_ref_id=m.user_ref_id
  JOIN teams t ON t.id=tm.team_id WHERE m.workspace_id=common.current_workspace_id()
  UNION
  SELECT p.customer_id FROM projects p JOIN memberships tm ON tm.user_ref_id=p.user_ref_id
  JOIN teams t ON t.id=tm.team_id WHERE security.fde_user_is_active(p.user_ref_id)
 ), assigned_customers AS MATERIALIZED (
  SELECT customer_id FROM projects WHERE user_ref_id=common.current_user_ref_id()
  UNION SELECT customer_id FROM crm.opportunity WHERE workspace_id=common.current_workspace_id()
   AND deleted_at IS NULL AND owner_user_ref_id=common.current_user_ref_id()
 )
 SELECT c.id,c.workspace_id,c.owner_team_id,CASE WHEN own.state='claimed' THEN own.owner_user_ref_id END
 FROM crm.customer c LEFT JOIN crm.customer_ownership own ON own.customer_id=c.id AND own.workspace_id=c.workspace_id
 WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
 AND NOT EXISTS(SELECT 1 FROM grants WHERE effect='deny')
 AND (EXISTS(SELECT 1 FROM grants WHERE effect='allow' AND scope_code='workspace')
  OR (EXISTS(SELECT 1 FROM grants WHERE effect='allow' AND scope_code='self')
    AND own.state='claimed' AND own.owner_user_ref_id=common.current_user_ref_id())
  OR (EXISTS(SELECT 1 FROM grants WHERE effect='allow' AND scope_code='assigned')
    AND c.id IN (SELECT customer_id FROM assigned_customers))
  OR c.owner_team_id IN (SELECT id FROM teams) OR c.id IN (SELECT customer_id FROM team_customers));
$$;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v137;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_v137();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
 AND rolname NOT LIKE 'pg\_%' ESCAPE '\' AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.authorization_portfolio_customer(text,uuid) TO %I',r.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_v137(),
 security.authorization_portfolio_customer(text,uuid) FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V138','Portfolio permission scopes independent of current presentation role');
COMMIT;
