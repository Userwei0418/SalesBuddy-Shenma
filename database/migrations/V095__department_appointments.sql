BEGIN;

-- Department appointments use the existing effective-dated membership and binding
-- tables. Supervising one department never elevates a membership in another.
CREATE OR REPLACE FUNCTION security.supervises_team(p_team_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT common.current_role_code()='supervisor' AND security.has_active_role('supervisor') AND EXISTS(
  SELECT 1 FROM platform.team_membership m JOIN platform.team t ON t.id=m.team_id AND t.workspace_id=m.workspace_id
  WHERE m.workspace_id=common.current_workspace_id() AND m.user_ref_id=common.current_user_ref_id()
   AND m.team_id=p_team_id AND m.membership_role='supervisor'
   AND clock_timestamp()>=m.valid_from AND clock_timestamp()<m.valid_to
   AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to);
$$;

-- Stable primary-first ordering keeps default ownership and displayed department aligned.
CREATE FUNCTION security.account_team_scope(p_workspace uuid,p_user uuid,p_role text)
RETURNS TABLE(team_id text,team_name text,is_primary boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT t.id::text,t.name,bool_or(m.is_primary)
 FROM platform.team_membership m JOIN platform.team t ON t.id=m.team_id AND t.workspace_id=m.workspace_id
 JOIN platform.role_binding b ON b.workspace_id=m.workspace_id AND b.user_ref_id=m.user_ref_id AND b.role_code=p_role
 WHERE m.workspace_id=p_workspace AND m.user_ref_id=p_user
  AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
  AND clock_timestamp()>=m.valid_from AND clock_timestamp()<m.valid_to
  AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
  AND (p_role IN ('manager','operations','administrator') OR (
   (m.membership_role=p_role OR (p_role='fde' AND m.membership_role='fde_lead'))
   AND (p_role NOT IN ('fde','fde_lead') OR b.team_id IS NULL OR b.team_id=m.team_id)))
 GROUP BY t.id,t.name;
$$;

CREATE OR REPLACE FUNCTION security.resolve_account_actor(p_workspace_external_id text, p_account_code text, p_role text DEFAULT NULL) RETURNS TABLE(workspace_id text, user_id text, account_code text, display_name text, role_code text, data_scope_code text, team_ids text[], team_names text[])
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'platform', 'security'
    AS $$
  SELECT
    w.id::text,
    u.id::text,
    u.account_code,
    u.display_name,
    rb.role_code,
    rb.data_scope_code,
    COALESCE((SELECT array_agg(d.team_id ORDER BY d.is_primary DESC,d.team_id)
      FROM security.account_team_scope(w.id,u.id,rb.role_code) d),ARRAY[]::text[]),
    COALESCE((SELECT array_agg(d.team_name ORDER BY d.is_primary DESC,d.team_id)
      FROM security.account_team_scope(w.id,u.id,rb.role_code) d),ARRAY[]::text[])
  FROM platform.workspace w
  JOIN platform.user_ref u
    ON u.workspace_id = w.id AND u.deleted_at IS NULL AND u.status = 'active'
  JOIN platform.role_binding rb
    ON rb.workspace_id = w.id AND rb.user_ref_id = u.id
   AND clock_timestamp() >= rb.valid_from AND clock_timestamp() < rb.valid_to
  LEFT JOIN platform.team_membership tm
    ON tm.workspace_id = w.id AND tm.user_ref_id = u.id
   AND clock_timestamp() >= tm.valid_from AND clock_timestamp() < tm.valid_to
   AND (rb.role_code IN ('manager','operations','administrator') OR (
     (tm.membership_role=rb.role_code OR (rb.role_code='fde' AND tm.membership_role='fde_lead'))
     AND (rb.role_code NOT IN ('fde','fde_lead') OR rb.team_id IS NULL OR rb.team_id=tm.team_id)))
  LEFT JOIN platform.team t ON t.id = tm.team_id AND t.deleted_at IS NULL AND t.status='active'
   AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
  WHERE w.external_workspace_id = p_workspace_external_id
    AND w.status = 'active'
    AND u.account_code = upper(btrim(p_account_code))
    AND (p_role IS NULL OR rb.role_code=p_role)
    AND (rb.role_code IN ('manager','operations','administrator') OR t.id IS NOT NULL)
  GROUP BY w.id, u.id, u.account_code, u.display_name,
           rb.role_code, rb.data_scope_code
  ORDER BY CASE rb.role_code WHEN 'administrator' THEN 1 WHEN 'operations' THEN 2 WHEN 'manager' THEN 3 WHEN 'supervisor' THEN 4 WHEN 'fde_lead' THEN 5 WHEN 'fde' THEN 6 ELSE 7 END, rb.data_scope_code
  LIMIT 1
$$;



CREATE OR REPLACE FUNCTION security.rotate_auth_session(p_old_hash text, p_new_hash text, p_new_expires_at timestamp with time zone) RETURNS TABLE(session_id text, workspace_id text, user_id text, account_code text, display_name text, role_code text, data_scope_code text, team_ids text[], team_names text[])
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'platform', 'security'
    AS $$
BEGIN
  RETURN QUERY
  WITH rotated AS (
    UPDATE platform.auth_session s
       SET refresh_token_hash = p_new_hash,
           expires_at = p_new_expires_at,
           last_seen_at = clock_timestamp(),
           updated_at = clock_timestamp()
     WHERE s.refresh_token_hash = p_old_hash
       AND s.status = 'active'
       AND s.expires_at > clock_timestamp()
    RETURNING s.id, s.workspace_id, s.user_ref_id, s.active_role
  )
  SELECT
    rotated.id::text,
    u.workspace_id::text,
    u.id::text,
    u.account_code,
    u.display_name,
    rb.role_code,
    rb.data_scope_code,
    COALESCE((SELECT array_agg(d.team_id ORDER BY d.is_primary DESC,d.team_id)
      FROM security.account_team_scope(u.workspace_id,u.id,rb.role_code) d),ARRAY[]::text[]),
    COALESCE((SELECT array_agg(d.team_name ORDER BY d.is_primary DESC,d.team_id)
      FROM security.account_team_scope(u.workspace_id,u.id,rb.role_code) d),ARRAY[]::text[])
  FROM rotated
  JOIN platform.user_ref u ON u.id = rotated.user_ref_id AND u.status='active' AND u.deleted_at IS NULL
  JOIN platform.role_binding rb
    ON rb.workspace_id = u.workspace_id AND rb.user_ref_id = u.id AND (rotated.active_role IS NULL OR rb.role_code=rotated.active_role)
   AND clock_timestamp() >= rb.valid_from AND clock_timestamp() < rb.valid_to
  LEFT JOIN platform.team_membership tm
    ON tm.workspace_id = u.workspace_id AND tm.user_ref_id = u.id
   AND clock_timestamp() >= tm.valid_from AND clock_timestamp() < tm.valid_to
   AND (rb.role_code IN ('manager','operations','administrator') OR (
     (tm.membership_role=rb.role_code OR (rb.role_code='fde' AND tm.membership_role='fde_lead'))
     AND (rb.role_code NOT IN ('fde','fde_lead') OR rb.team_id IS NULL OR rb.team_id=tm.team_id)))
  LEFT JOIN platform.team t ON t.id = tm.team_id AND t.deleted_at IS NULL AND t.status='active'
   AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
  WHERE rb.role_code IN ('manager','operations','administrator') OR t.id IS NOT NULL
  GROUP BY rotated.id, u.workspace_id, u.id, u.account_code, u.display_name,
           rb.role_code, rb.data_scope_code
  ORDER BY rb.role_code LIMIT 1;
END;
$$;




-- Returns a display title only; never used for authorization or role selection.
CREATE FUNCTION security.account_supervisor_title(p_workspace uuid,p_user uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT CASE WHEN COALESCE(bool_and(COALESCE(t.attributes->>'kind','general')='product_sales'),false)
   THEN '产品销售主管' ELSE '销售主管' END
 FROM platform.role_binding b JOIN platform.team_membership m ON m.workspace_id=b.workspace_id
  AND m.user_ref_id=b.user_ref_id AND m.membership_role='supervisor'
 JOIN platform.team t ON t.id=m.team_id AND t.workspace_id=m.workspace_id
 WHERE b.workspace_id=p_workspace AND b.user_ref_id=p_user AND b.role_code='supervisor'
  AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
  AND clock_timestamp()>=m.valid_from AND clock_timestamp()<m.valid_to
  AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to;
$$;
INSERT INTO ops.schema_migration(version,description) VALUES('V095','逐部门任职、代管标记与主管身份范围');
COMMIT;
