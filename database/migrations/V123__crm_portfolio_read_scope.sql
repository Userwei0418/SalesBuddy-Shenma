BEGIN;

-- Only identity/confirmed ownership are projected. This is not the company-wide
-- claim directory and never exposes business bodies or changes their RLS.
-- Resolve role/team authority once, rather than re-reading it for every archive.
CREATE FUNCTION security.customer_portfolio_scope()
RETURNS TABLE(id uuid,workspace_id uuid,owner_team_id uuid,claimant_id uuid)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id(); actor uuid:=common.current_user_ref_id();
 role_code text:=common.current_role_code(); managed_teams uuid[];
BEGIN
 IF ws IS NULL OR actor IS NULL OR NOT security.has_active_role(role_code) THEN RETURN; END IF;
 IF security.management_actor() OR role_code='manager' THEN
  RETURN QUERY SELECT c.id,c.workspace_id,c.owner_team_id,
   CASE WHEN own.state='claimed' THEN own.owner_user_ref_id END
  FROM crm.customer c LEFT JOIN crm.customer_ownership own ON own.customer_id=c.id AND own.workspace_id=c.workspace_id
  WHERE c.workspace_id=ws AND c.deleted_at IS NULL;
 ELSIF role_code='sales' THEN
  -- Portfolio sales scope has always been current confirmed ownership, even
  -- when historical participation permits reading another customer's history.
  RETURN QUERY SELECT c.id,c.workspace_id,c.owner_team_id,own.owner_user_ref_id
  FROM crm.customer_ownership own JOIN crm.customer c ON c.id=own.customer_id AND c.workspace_id=own.workspace_id
  WHERE c.workspace_id=ws AND c.deleted_at IS NULL AND own.state='claimed' AND own.owner_user_ref_id=actor;
 ELSIF role_code='supervisor' THEN
  SELECT COALESCE(array_agg(t.id),'{}'::uuid[]) INTO managed_teams FROM platform.team t
   WHERE t.workspace_id=ws AND security.supervises_team(t.id);
  RETURN QUERY SELECT c.id,c.workspace_id,c.owner_team_id,
   CASE WHEN own.state='claimed' THEN own.owner_user_ref_id END
  FROM crm.customer c LEFT JOIN crm.customer_ownership own ON own.customer_id=c.id AND own.workspace_id=c.workspace_id
  WHERE c.workspace_id=ws AND c.deleted_at IS NULL AND (
   (own.state='claimed' AND own.owner_user_ref_id=actor) OR c.owner_team_id=ANY(managed_teams) OR c.id IN (
    SELECT m.customer_id FROM crm.customer_sales_member m JOIN platform.team_membership tm
     ON tm.user_ref_id=m.user_ref_id AND tm.workspace_id=m.workspace_id
    WHERE m.workspace_id=ws AND tm.team_id=ANY(managed_teams)
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to));
 ELSIF security.is_fde_actor() THEN
  RETURN QUERY SELECT c.id,c.workspace_id,c.owner_team_id,NULL::uuid
  FROM crm.customer c WHERE c.workspace_id=ws AND c.deleted_at IS NULL AND c.id IN (
   SELECT o.customer_id FROM crm.opportunity o WHERE o.workspace_id=ws AND o.deleted_at IS NULL
    AND security.fde_opportunity_in_scope(o.id));
 END IF;
END $$;
COMMENT ON FUNCTION security.customer_portfolio_scope() IS
 '经营客户资产汇总的只读ID集合：当前公司、有效岗位、原客户访问范围；销售仅确认认领者；不开放正文与认领审批详情';
REVOKE ALL ON FUNCTION security.customer_portfolio_scope() FROM PUBLIC,salegent_feishu_worker;

ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v122;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_v122();
 FOR r IN SELECT rolname FROM pg_roles
  WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
   AND rolname NOT LIKE 'pg\_%' ESCAPE '\'
   AND has_schema_privilege(oid,'security','USAGE')
   AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE')
 LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.customer_portfolio_scope() TO %I',r.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_v122() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V123','客户资产集合授权与全量档案查询优化');
COMMIT;
