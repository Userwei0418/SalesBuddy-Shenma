-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V203__restore_sales_business_read_scope.sql.
-- Original SHA-256: 8d8bff692768af14f709e5c38e072179175d200c85266dd816d5536034818161. See shenma_whole_121_manifest.json.
-- Restore the Web 1.1.2 business-read compatibility rule.
--
-- V202 originally allowed a sales account with a self grant to read the
-- team it belongs to. A later edit removed `sales` from the adapter while
-- keeping the Web team scope selector. That makes a normal salesperson's
-- team read fail with 403 even though the account is allowed to see its own
-- team. The adapter is read-only; create, update, claim and export checks
-- remain unchanged.

CREATE OR REPLACE FUNCTION security.authorization_business_read(
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
   -- A supervisor is appointed to a team; scope starts there and expands
   -- downward. It does not include a parent team's sibling branches.
   SELECT team_id FROM supervisor_roots
   UNION
   SELECT child.id FROM platform.team child JOIN supervisor_teams parent ON parent.team_id=child.parent_team_id
   WHERE child.workspace_id=common.current_workspace_id() AND child.status='active' AND child.deleted_at IS NULL
    AND clock_timestamp()>=child.valid_from AND clock_timestamp()<child.valid_to
 ), allowed AS (
   -- A sales self grant authorizes read access to the member's own team.
   -- This adapter never widens write, claim or export permissions.
   SELECT g.source_code FROM grants g WHERE g.effect='allow' AND g.source_code IN ('sales','supervisor','manager')
    AND g.scope_code IN ('self','teams')
 )
 SELECT p_permission IN ('battle_map.read','opportunity.read','customer.read','visit.read')
  AND p_workspace=common.current_workspace_id() AND NOT EXISTS (SELECT 1 FROM grants WHERE effect='deny')
  AND EXISTS (SELECT 1 FROM allowed g WHERE
    (g.source_code='sales' AND EXISTS (
      SELECT 1 FROM platform.team_membership tm JOIN platform.role_binding rb
       ON rb.workspace_id=tm.workspace_id AND rb.user_ref_id=tm.user_ref_id AND rb.role_code='sales'
      WHERE tm.workspace_id=common.current_workspace_id() AND tm.user_ref_id=common.current_user_ref_id()
       AND tm.membership_role='sales' AND p_teams && ARRAY[tm.team_id]
       AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
       AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
    )) OR (g.source_code='supervisor' AND EXISTS (
      SELECT 1 FROM supervisor_teams WHERE p_teams && ARRAY[team_id]
    )) OR (g.source_code='manager' AND EXISTS (
      SELECT 1 FROM platform.role_binding rb WHERE rb.workspace_id=common.current_workspace_id()
       AND rb.user_ref_id=common.current_user_ref_id() AND rb.role_code='manager'
       AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
    )))
$$;

-- Team selectors and business repositories call these two functions before
-- the adapter. Keep the restored sales read rule effective for both paths;
-- otherwise the page can still reject the same team while the direct read
-- adapter returns true.
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

INSERT INTO ops.schema_migration(version,description)
VALUES('V351','Restore sales business read scope for Web 1.1.2 compatibility');
