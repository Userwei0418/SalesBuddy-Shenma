-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V157__capability_snapshot_query.sql.
-- Original SHA-256: 4d57fd2d354659d2ebde3172af7964a0cbda7b020436ed6f23f9360a850b9a1c. See shenma_whole_121_manifest.json.
BEGIN;
-- Calculate module availability once per authorization snapshot, not once per grant.
CREATE OR REPLACE FUNCTION security.authorization_grants_for(p_workspace uuid, p_user uuid)
 RETURNS TABLE(permission_code text, scope_code text, team_ids uuid[], source_code text, source_id text, effect text)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 WITH available_permissions AS MATERIALIZED (
  SELECT p.permission_code FROM control.permission_module p LEFT JOIN control.tenant_module t
   ON t.workspace_id=p_workspace AND t.module_code=p.module_code
  GROUP BY p.permission_code HAVING bool_and(t.enabled IS TRUE AND (t.expires_at IS NULL OR t.expires_at>clock_timestamp()))
 ), person AS (
  SELECT u.id FROM platform.user_ref u JOIN platform.workspace w ON w.id=u.workspace_id
  WHERE u.id=p_user AND u.workspace_id=p_workspace AND u.status='active' AND u.deleted_at IS NULL
   AND w.status='active' AND w.deleted_at IS NULL
   AND EXISTS(SELECT 1 FROM platform.role_binding identity_role WHERE identity_role.workspace_id=p_workspace
    AND identity_role.user_ref_id=p_user AND clock_timestamp()>=identity_role.valid_from AND clock_timestamp()<identity_role.valid_to
    AND (identity_role.role_code IN ('manager','operations','administrator') OR EXISTS(
     SELECT 1 FROM security.account_team_scope(p_workspace,p_user,identity_role.role_code))))
 ), appointments AS (
  SELECT DISTINCT b.role_code,b.data_scope_code,
   ARRAY(SELECT s.team_id::uuid FROM security.account_team_scope(p_workspace,p_user,b.role_code) s) teams
  FROM platform.role_binding b JOIN person p ON p.id=b.user_ref_id
  WHERE b.workspace_id=p_workspace AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
   AND (b.role_code IN ('manager','operations','administrator') OR EXISTS(
     SELECT 1 FROM security.account_team_scope(p_workspace,p_user,b.role_code)))
 ), sources AS (
  SELECT r.id,r.version_no,r.builtin_role_code source_code,
   CASE a.data_scope_code WHEN 'team' THEN 'teams' ELSE a.data_scope_code END scope_code,a.teams
  FROM appointments a JOIN config.permission_role r ON r.workspace_id=p_workspace
   AND r.builtin_role_code=a.role_code AND r.status='active' AND (r.expires_at IS NULL OR r.expires_at>clock_timestamp())
  UNION ALL
  SELECT r.id,r.version_no,'assigned_role',a.scope_code,a.team_ids
  FROM config.account_permission_role a JOIN person p ON p.id=a.user_ref_id
  JOIN config.permission_role r ON r.id=a.role_id AND r.workspace_id=a.workspace_id AND r.status='active' AND (r.expires_at IS NULL OR r.expires_at>clock_timestamp())
  WHERE a.workspace_id=p_workspace AND (a.expires_at IS NULL OR a.expires_at>clock_timestamp())
 ), combined AS (
  SELECT g.permission_code,CASE g.scope_code WHEN 'inherit' THEN s.scope_code ELSE g.scope_code END scope_code,
   CASE WHEN g.scope_code='teams' THEN g.team_ids WHEN g.scope_code='inherit' AND s.scope_code='teams' THEN s.teams ELSE '{}'::uuid[] END teams,
   s.source_code,s.id::text source_id,'allow'::text effect
  FROM sources s JOIN config.permission_role_grant g ON g.role_id=s.id AND g.workspace_id=p_workspace
  WHERE NOT (s.source_code IN ('fde','fde_lead') AND s.version_no=1 AND g.permission_code IN
   ('visit.create','visit.supplement','visit.upload','visit.retry_import','visit.download_original','visit.structure','visit.quality_review'))
   OR security.authorization_legacy_fde_visit(p_workspace,p_user,s.source_code)
  UNION ALL
  SELECT o.permission_code,o.scope_code,o.team_ids,'account_override',p_user::text,o.effect
  FROM config.account_permission_override o JOIN person p ON p.id=o.user_ref_id WHERE o.workspace_id=p_workspace AND (o.expires_at IS NULL OR o.expires_at>clock_timestamp())
 ), valid AS (
  SELECT c.*,ARRAY(SELECT t.id FROM platform.team t WHERE t.id=ANY(c.teams) AND t.workspace_id=p_workspace
    AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to ORDER BY t.id) active_teams
  FROM combined c JOIN config.permission_catalog d ON d.code=c.permission_code
  WHERE c.effect='deny' OR c.scope_code=ANY(d.scopes)
 )
 SELECT v.permission_code,v.scope_code,v.active_teams,v.source_code,v.source_id,v.effect FROM valid v
 WHERE (v.scope_code<>'teams' OR cardinality(v.active_teams)>0 OR v.effect='deny')
 AND (v.effect='deny' OR v.permission_code IN (SELECT permission_code FROM available_permissions));
$function$
;
INSERT INTO ops.schema_migration(version,description) VALUES('V305','Batch tenant capability availability during authorization');
COMMIT;
