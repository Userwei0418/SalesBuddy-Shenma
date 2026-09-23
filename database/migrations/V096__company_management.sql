BEGIN;
-- Explicit cross-company grants. Runtime roles cannot manufacture or edit grants.
CREATE TABLE security.company_management_grant (
 source_workspace_id uuid NOT NULL,
 source_user_id uuid NOT NULL,
 target_workspace_id uuid NOT NULL,
 target_user_id uuid NOT NULL,
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','revoked')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(source_workspace_id,source_user_id,target_workspace_id),
 FOREIGN KEY(source_user_id,source_workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(target_user_id,target_workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 CHECK(source_workspace_id<>target_workspace_id)
);
ALTER TABLE security.company_management_grant ENABLE ROW LEVEL SECURITY;
ALTER TABLE security.company_management_grant FORCE ROW LEVEL SECURITY;
REVOKE ALL ON security.company_management_grant FROM PUBLIC;

CREATE FUNCTION security.company_management_actor(p_target uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT to_jsonb(a) FROM security.company_management_grant g
 JOIN platform.workspace source ON source.id=g.source_workspace_id AND source.status='active' AND source.deleted_at IS NULL
 JOIN platform.workspace target ON target.id=g.target_workspace_id AND target.status='active' AND target.deleted_at IS NULL
 JOIN platform.user_ref u ON u.id=g.target_user_id AND u.workspace_id=target.id AND u.status='active' AND u.deleted_at IS NULL
 CROSS JOIN LATERAL security.resolve_account_actor(target.external_workspace_id,u.account_code,'administrator') a
 WHERE g.source_workspace_id=common.current_workspace_id() AND g.source_user_id=common.current_user_ref_id()
 AND common.current_role_code()='administrator' AND security.has_active_role('administrator')
 AND g.target_workspace_id=p_target AND g.status='active';
$$;

CREATE FUNCTION security.company_directory() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE(jsonb_agg(jsonb_build_object(
  'id',w.id,'name',w.name,'code',w.external_workspace_id,'kind',COALESCE(w.attributes->>'kind','standard'),
  'status',w.status,'version_no',w.version_no,
  'account_count',(SELECT count(*) FROM platform.user_ref u WHERE u.workspace_id=w.id AND u.deleted_at IS NULL AND u.status='active'),
  'department_count',(SELECT count(*) FROM platform.team t WHERE t.workspace_id=w.id AND t.deleted_at IS NULL AND t.status='active')
 ) ORDER BY w.created_at,w.id),'[]'::jsonb)
 FROM platform.workspace w WHERE w.status='active' AND w.deleted_at IS NULL
 AND security.management_actor() AND (w.id=common.current_workspace_id() OR security.company_management_actor(w.id) IS NOT NULL);
$$;

CREATE FUNCTION security.password_account_workspace(p_account text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT CASE WHEN count(*)=1 THEN min(w.external_workspace_id) END
 FROM platform.workspace w JOIN platform.user_ref u ON u.workspace_id=w.id
 JOIN platform.password_credential p ON p.user_ref_id=u.id AND p.workspace_id=w.id
 WHERE w.status='active' AND w.deleted_at IS NULL AND u.status='active' AND u.deleted_at IS NULL
 AND u.account_code=upper(btrim(p_account));
$$;

-- The selected company is only a lookup hint. Ambiguous account names never select
-- a workspace by trying passwords or by preferring demonstration data.
CREATE OR REPLACE FUNCTION security.password_candidate(p_workspace text,p_account text,p_role text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH selected AS (
  SELECT CASE WHEN NULLIF(btrim(p_workspace),'') IS NOT NULL THEN p_workspace ELSE
   (SELECT CASE WHEN count(*)=1 THEN min(w.external_workspace_id) END
    FROM platform.workspace w JOIN platform.user_ref u ON u.workspace_id=w.id
    JOIN platform.password_credential p ON p.user_ref_id=u.id AND p.workspace_id=w.id
    WHERE w.status='active' AND w.deleted_at IS NULL AND u.status='active' AND u.deleted_at IS NULL
     AND u.account_code=upper(btrim(p_account))) END AS workspace
 )
 SELECT to_jsonb(a)||jsonb_build_object('password_hash',p.password_hash,'must_change_password',p.must_change_password)
 FROM selected CROSS JOIN LATERAL security.resolve_account_actor(selected.workspace,p_account,p_role) a
 JOIN platform.password_credential p ON p.user_ref_id=a.user_id::uuid AND p.workspace_id=a.workspace_id::uuid;
$$;

CREATE OR REPLACE FUNCTION security.auth_session_is_active(p_session_id uuid,p_workspace_id uuid,p_user_ref_id uuid)
RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM platform.auth_session s JOIN platform.workspace w ON w.id=s.workspace_id
 WHERE s.id=p_session_id AND s.workspace_id=p_workspace_id AND s.user_ref_id=p_user_ref_id
 AND s.status='active' AND s.expires_at>clock_timestamp() AND w.status='active' AND w.deleted_at IS NULL);
$$;

-- A company management identity has no independent password; it is reached only
-- through a live authenticated source account and its explicit grant.
CREATE FUNCTION security.protect_managed_account() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.id=NEW.user_ref_id AND u.workspace_id=NEW.workspace_id
 AND u.attributes->>'platform_managed'='true') THEN
  RAISE EXCEPTION '平台管理身份不能设置独立密码' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER protect_managed_account BEFORE INSERT OR UPDATE ON platform.password_credential
FOR EACH ROW EXECUTE FUNCTION security.protect_managed_account();
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
       AND EXISTS(SELECT 1 FROM platform.workspace w WHERE w.id=s.workspace_id AND w.status='active' AND w.deleted_at IS NULL)
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
COMMIT;
