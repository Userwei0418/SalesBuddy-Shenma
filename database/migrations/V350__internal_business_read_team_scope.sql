-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V202__business_read_team_scope.sql.
-- Original SHA-256: 18a511ff3d17a76ac0e06a950b848612347a77a56b0012f59cd84f0ac9a0b31c. See shenma_whole_121_manifest.json.
BEGIN;

-- The map and opportunity read views use the same business scope. Keep this
-- adapter deliberately narrower than the general authorization grant: it only
-- widens the map/opportunity/customer/follow-up read paths and never changes
-- create, update, claim or export.
CREATE FUNCTION security.authorization_business_read(
 p_permission text,p_workspace uuid,p_owner uuid,p_teams uuid[] DEFAULT '{}',p_assigned boolean DEFAULT false)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH RECURSIVE grants AS MATERIALIZED (
   SELECT * FROM security.authorization_current_grants() WHERE permission_code=p_permission
 ), supervisor_roots AS MATERIALIZED (
   SELECT tm.team_id FROM platform.team_membership tm
   JOIN platform.role_binding rb ON rb.workspace_id=tm.workspace_id
    AND rb.user_ref_id=tm.user_ref_id AND rb.role_code='supervisor'
   JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
   WHERE tm.workspace_id=common.current_workspace_id() AND tm.user_ref_id=common.current_user_ref_id()
    AND tm.membership_role='supervisor' AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
    AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
    AND t.status='active' AND t.deleted_at IS NULL
    AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
 ), supervisor_teams AS (
   -- A supervisor is appointed to a team; scope starts at that team and
   -- expands downward. Do not walk to the top-level root first, otherwise an
   -- appointment to a nested team would incorrectly include sibling branches.
   SELECT team_id FROM supervisor_roots
   UNION
   SELECT child.id FROM platform.team child JOIN supervisor_teams parent ON parent.team_id=child.parent_team_id
   WHERE child.workspace_id=common.current_workspace_id() AND child.status='active' AND child.deleted_at IS NULL
    AND clock_timestamp()>=child.valid_from AND clock_timestamp()<child.valid_to
 ), allowed AS (
   -- Ordinary sales remain self-scoped. Team/department reads are only an
   -- adapter for management roles; adding sales here would make a normal
   -- salesperson see peer-owned opportunities and customer facts.
   SELECT g.source_code FROM grants g WHERE g.effect='allow' AND g.source_code IN ('supervisor','manager')
    AND g.scope_code IN ('self','teams')
 )
 SELECT p_permission IN ('battle_map.read','opportunity.read','customer.read','visit.read')
  AND p_workspace=common.current_workspace_id() AND NOT EXISTS (SELECT 1 FROM grants WHERE effect='deny')
  AND EXISTS (SELECT 1 FROM allowed g WHERE
    (g.source_code='supervisor' AND EXISTS (
      SELECT 1 FROM supervisor_teams WHERE p_teams && ARRAY[team_id]
    )) OR (g.source_code='manager' AND EXISTS (
      SELECT 1 FROM platform.role_binding rb WHERE rb.workspace_id=common.current_workspace_id()
       AND rb.user_ref_id=common.current_user_ref_id() AND rb.role_code='manager'
       AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
    )))
$$;

CREATE FUNCTION security.authorization_business_department(p_permission text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH grants AS MATERIALIZED (
   SELECT * FROM security.authorization_current_grants() WHERE permission_code=p_permission
 ), supervisor_roots AS MATERIALIZED (
   SELECT tm.team_id FROM platform.team_membership tm
   JOIN platform.role_binding rb ON rb.workspace_id=tm.workspace_id
    AND rb.user_ref_id=tm.user_ref_id AND rb.role_code='supervisor'
   JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
   WHERE tm.workspace_id=common.current_workspace_id() AND tm.user_ref_id=common.current_user_ref_id()
    AND tm.membership_role='supervisor' AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
    AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
    AND t.status='active' AND t.deleted_at IS NULL
    AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
 )
 SELECT p_permission IN ('battle_map.read','opportunity.read','customer.read','visit.read')
  AND NOT EXISTS (SELECT 1 FROM grants WHERE effect='deny')
  AND (security.authorization_allows(p_permission,common.current_workspace_id(),NULL,'{}',false)
    OR (EXISTS (SELECT 1 FROM grants WHERE effect='allow' AND source_code='manager')
      AND EXISTS (SELECT 1 FROM platform.role_binding rb WHERE rb.workspace_id=common.current_workspace_id()
       AND rb.user_ref_id=common.current_user_ref_id() AND rb.role_code='manager'
       AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to))
    OR (EXISTS (SELECT 1 FROM grants WHERE effect='allow' AND source_code='supervisor'
       AND scope_code IN ('self','teams')) AND EXISTS (SELECT 1 FROM supervisor_roots)))
$$;

-- Keep authorization_allows as the single row gate. The explicit OR is inside
-- its allow check, so an account-level deny still wins over the team adapter.
CREATE OR REPLACE FUNCTION security.authorization_allows(
 p_permission text,p_workspace uuid,p_owner uuid,p_teams uuid[] DEFAULT '{}',p_assigned boolean DEFAULT false)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH grants AS (SELECT * FROM security.authorization_current_grants() WHERE permission_code=p_permission)
 SELECT p_workspace=common.current_workspace_id() AND NOT EXISTS(SELECT 1 FROM grants WHERE effect='deny')
  AND EXISTS(SELECT 1 FROM grants WHERE effect='allow' AND (
    scope_code='workspace' OR (scope_code='self' AND p_owner=common.current_user_ref_id())
    OR (scope_code='assigned' AND p_assigned) OR (scope_code='teams' AND team_ids&&p_teams)
    OR security.authorization_business_read(p_permission,p_workspace,p_owner,p_teams,p_assigned)));
$$;

-- Department/team selectors use the same feature permission. A supervisor's
-- department is their appointed team and all active descendants; manager
-- workspace grants retain their existing behavior.
CREATE OR REPLACE FUNCTION security.authorization_subject(
 p_permission text,p_scope text,p_user uuid,p_team uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE(CASE
  WHEN p_scope='department' AND p_user IS NULL AND p_team IS NULL THEN
   CASE WHEN p_permission IN ('battle_map.read','opportunity.read','customer.read','visit.read')
     THEN security.authorization_business_department(p_permission)
     ELSE security.authorization_allows(p_permission,common.current_workspace_id(),NULL,'{}',false)
   END
  WHEN p_scope='team' AND p_user IS NULL THEN EXISTS(
   SELECT 1 FROM platform.team t WHERE t.id=p_team AND t.workspace_id=common.current_workspace_id()
    AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
    AND security.authorization_allows(p_permission,t.workspace_id,NULL,ARRAY[t.id],false))
  WHEN p_scope='person' AND p_team IS NULL THEN EXISTS(
   SELECT 1 FROM platform.user_ref u WHERE u.id=p_user AND u.workspace_id=common.current_workspace_id()
    AND u.status='active' AND u.deleted_at IS NULL
    AND security.authorization_allows(p_permission,u.workspace_id,u.id,ARRAY(
      SELECT tm.team_id FROM platform.team_membership tm JOIN platform.team t
       ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
      WHERE tm.user_ref_id=u.id AND tm.workspace_id=u.workspace_id AND t.status='active' AND t.deleted_at IS NULL
       AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
    ),u.id=common.current_user_ref_id()))
  ELSE false END,false);
$$;

REVOKE ALL ON FUNCTION security.authorization_business_read(text,uuid,uuid,uuid[],boolean),
 security.authorization_business_department(text) FROM PUBLIC,salegent_feishu_worker;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_internal_v201;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_internal_v201();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
  AND rolname NOT LIKE 'pg\_%' ESCAPE '\' AND has_schema_privilege(oid,'security','USAGE')
  AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.authorization_business_read(text,uuid,uuid,uuid[],boolean), security.authorization_business_department(text) TO %I',r.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_internal_v201() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V350','Business read scope: management teams and supervisor department descendants');
COMMIT;
