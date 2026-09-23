BEGIN;
-- Management identity is independent of legacy demonstration accounts.
ALTER TABLE platform.role_binding DROP CONSTRAINT role_binding_role_code_check;
ALTER TABLE platform.role_binding ADD CONSTRAINT role_binding_role_code_check
 CHECK(role_code IN ('sales','supervisor','manager','operations','administrator'));
ALTER TABLE platform.team_membership DROP CONSTRAINT team_membership_membership_role_check;
ALTER TABLE platform.team_membership ADD CONSTRAINT team_membership_membership_role_check
 CHECK(membership_role IN ('sales','supervisor','manager','operations','administrator'));
ALTER TABLE platform.auth_session ADD COLUMN auth_method text NOT NULL DEFAULT 'demo'
 CHECK(auth_method IN ('demo','password'));
ALTER TABLE platform.auth_session ADD COLUMN active_role text;
COMMENT ON TABLE platform.auth_session IS '服务端会话：验证方式和当前角色由服务端写入，不相信客户端声明';
CREATE TABLE platform.password_credential (
 user_ref_id uuid PRIMARY KEY, workspace_id uuid NOT NULL,
 password_hash text NOT NULL, must_change_password boolean NOT NULL DEFAULT true,
 password_changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
ALTER TABLE platform.password_credential ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.password_credential FORCE ROW LEVEL SECURITY;
REVOKE ALL ON platform.password_credential FROM PUBLIC;
COMMENT ON TABLE platform.password_credential IS '仅安全函数访问；口令采用 scrypt 随机盐哈希，不存明文';
CREATE TABLE security.login_throttle (
 key_hash text PRIMARY KEY, attempts integer NOT NULL, window_start timestamptz NOT NULL
);
REVOKE ALL ON security.login_throttle FROM PUBLIC;
CREATE FUNCTION security.management_actor() RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT common.current_role_code() IN ('operations','administrator')
 AND security.has_active_role(common.current_role_code());
$$;
CREATE FUNCTION security.login_attempt(p_key text,p_limit integer) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE n integer;
BEGIN
 IF length(p_key)<>64 OR p_limit NOT IN (5,50) THEN RAISE EXCEPTION 'invalid throttle key'; END IF;
 INSERT INTO security.login_throttle VALUES(p_key,1,clock_timestamp())
 ON CONFLICT(key_hash) DO UPDATE SET
 attempts=CASE WHEN login_throttle.window_start<clock_timestamp()-interval '15 minutes' THEN 1 ELSE login_throttle.attempts+1 END,
 window_start=CASE WHEN login_throttle.window_start<clock_timestamp()-interval '15 minutes' THEN clock_timestamp() ELSE login_throttle.window_start END
 RETURNING attempts INTO n;
 DELETE FROM security.login_throttle WHERE window_start<clock_timestamp()-interval '1 day';
 RETURN n<=p_limit;
END $$;
CREATE FUNCTION security.resolve_account_actor(p_workspace_external_id text, p_account_code text, p_role text DEFAULT NULL) RETURNS TABLE(workspace_id text, user_id text, account_code text, display_name text, role_code text, data_scope_code text, team_ids text[], team_names text[])
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
    COALESCE(array_agg(DISTINCT tm.team_id::text)
      FILTER (WHERE tm.team_id IS NOT NULL), ARRAY[]::text[]),
    COALESCE(array_agg(DISTINCT t.name)
      FILTER (WHERE t.name IS NOT NULL), ARRAY[]::text[])
  FROM platform.workspace w
  JOIN platform.user_ref u
    ON u.workspace_id = w.id AND u.deleted_at IS NULL AND u.status = 'active'
  JOIN platform.role_binding rb
    ON rb.workspace_id = w.id AND rb.user_ref_id = u.id
   AND clock_timestamp() >= rb.valid_from AND clock_timestamp() < rb.valid_to
  LEFT JOIN platform.team_membership tm
    ON tm.workspace_id = w.id AND tm.user_ref_id = u.id
   AND clock_timestamp() >= tm.valid_from AND clock_timestamp() < tm.valid_to
  LEFT JOIN platform.team t ON t.id = tm.team_id AND t.deleted_at IS NULL
  WHERE w.external_workspace_id = p_workspace_external_id
    AND w.status = 'active'
    AND u.account_code = upper(btrim(p_account_code))
    AND (p_role IS NULL OR rb.role_code=p_role)
  GROUP BY w.id, u.id, u.account_code, u.display_name,
           rb.role_code, rb.data_scope_code
  ORDER BY CASE rb.role_code WHEN 'administrator' THEN 1 WHEN 'operations' THEN 2 WHEN 'manager' THEN 3 WHEN 'supervisor' THEN 4 ELSE 5 END, rb.data_scope_code
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
    COALESCE(array_agg(DISTINCT tm.team_id::text)
      FILTER (WHERE tm.team_id IS NOT NULL), ARRAY[]::text[]),
    COALESCE(array_agg(DISTINCT t.name)
      FILTER (WHERE t.name IS NOT NULL), ARRAY[]::text[])
  FROM rotated
  JOIN platform.user_ref u ON u.id = rotated.user_ref_id AND u.status='active' AND u.deleted_at IS NULL
  JOIN platform.role_binding rb
    ON rb.workspace_id = u.workspace_id AND rb.user_ref_id = u.id AND (rotated.active_role IS NULL OR rb.role_code=rotated.active_role)
   AND clock_timestamp() >= rb.valid_from AND clock_timestamp() < rb.valid_to
  LEFT JOIN platform.team_membership tm
    ON tm.workspace_id = u.workspace_id AND tm.user_ref_id = u.id
   AND clock_timestamp() >= tm.valid_from AND clock_timestamp() < tm.valid_to
  LEFT JOIN platform.team t ON t.id = tm.team_id AND t.deleted_at IS NULL
  GROUP BY rotated.id, u.workspace_id, u.id, u.account_code, u.display_name,
           rb.role_code, rb.data_scope_code
  ORDER BY rb.role_code LIMIT 1;
END;
$$;



CREATE OR REPLACE FUNCTION security.resolve_demo_actor(p_workspace_external_id text,p_account_code text)
 RETURNS TABLE(workspace_id text,user_id text,account_code text,display_name text,role_code text,data_scope_code text,team_ids text[],team_names text[])
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT a.* FROM security.resolve_account_actor(p_workspace_external_id,p_account_code,NULL) a
 WHERE a.role_code IN ('sales','supervisor','manager')
 AND NOT EXISTS(SELECT 1 FROM platform.password_credential p WHERE p.user_ref_id=a.user_id::uuid);
$$;
CREATE FUNCTION security.password_candidate(p_workspace text,p_account text,p_role text) RETURNS jsonb
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT to_jsonb(a)||jsonb_build_object('password_hash',p.password_hash,'must_change_password',p.must_change_password)
 FROM security.resolve_account_actor(p_workspace,p_account,p_role) a
 JOIN platform.password_credential p ON p.user_ref_id=a.user_id::uuid AND p.workspace_id=a.workspace_id::uuid;
$$;
CREATE FUNCTION security.complete_password_login(p_session uuid,p_hash text,p_attempt_key text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM platform.password_credential p WHERE p.user_ref_id=common.current_user_ref_id()
 AND p.workspace_id=common.current_workspace_id() AND p.password_hash=p_hash)
 OR NOT security.has_active_role(common.current_role_code()) THEN
 RAISE EXCEPTION 'credential changed' USING ERRCODE='28000'; END IF;
 UPDATE platform.auth_session SET auth_method='password',active_role=common.current_role_code()
 WHERE id=p_session AND workspace_id=common.current_workspace_id() AND user_ref_id=common.current_user_ref_id();
 DELETE FROM security.login_throttle WHERE key_hash=p_attempt_key;
END $$;
CREATE FUNCTION security.session_credentials(p_session uuid) RETURNS jsonb
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('auth_method',s.auth_method,'must_change_password',COALESCE(p.must_change_password,false))
 FROM platform.auth_session s LEFT JOIN platform.password_credential p ON p.user_ref_id=s.user_ref_id
 WHERE s.id=p_session AND s.workspace_id=common.current_workspace_id() AND s.user_ref_id=common.current_user_ref_id()
 AND s.status='active' AND s.expires_at>clock_timestamp();
$$;
CREATE FUNCTION security.change_own_password(p_old_hash text,p_new_hash text,p_session uuid) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE n integer;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM platform.auth_session s WHERE s.id=p_session AND s.user_ref_id=common.current_user_ref_id()
 AND s.workspace_id=common.current_workspace_id() AND s.auth_method='password' AND s.status='active'
 AND s.expires_at>clock_timestamp()) THEN RETURN false; END IF;
 UPDATE platform.password_credential SET password_hash=p_new_hash,must_change_password=false,password_changed_at=clock_timestamp()
 WHERE user_ref_id=common.current_user_ref_id() AND workspace_id=common.current_workspace_id() AND password_hash=p_old_hash;
 GET DIAGNOSTICS n=ROW_COUNT;
 IF n=1 THEN
 UPDATE platform.auth_session SET status='revoked',revoked_at=clock_timestamp()
 WHERE user_ref_id=common.current_user_ref_id() AND workspace_id=common.current_workspace_id() AND id<>p_session AND status='active';
 END IF;
 RETURN n=1;
END $$;
-- Password material has no normal SELECT policy, including for administrators.
-- Only these narrow security functions can read or update a credential.
CREATE FUNCTION security.own_password_candidate() RETURNS jsonb
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('password_hash',password_hash) FROM platform.password_credential
 WHERE user_ref_id=common.current_user_ref_id() AND workspace_id=common.current_workspace_id();
$$;
CREATE FUNCTION security.set_account_password(p_user uuid,p_hash text,p_must_change boolean) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT security.management_actor() OR NOT EXISTS(SELECT 1 FROM platform.user_ref u
 WHERE u.id=p_user AND u.workspace_id=common.current_workspace_id() AND u.deleted_at IS NULL) THEN
 RAISE EXCEPTION '无权重置账号口令' USING ERRCODE='42501'; END IF;
 IF common.current_role_code()<>'administrator' AND EXISTS(SELECT 1 FROM platform.role_binding r
 WHERE r.user_ref_id=p_user AND r.workspace_id=common.current_workspace_id() AND r.role_code IN ('operations','administrator')
 AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to) THEN
 RAISE EXCEPTION '管理账号口令由系统管理员重置' USING ERRCODE='42501'; END IF;
 IF p_hash NOT LIKE 'scrypt$32768$8$1$%' THEN RAISE EXCEPTION 'invalid password encoding' USING ERRCODE='22023'; END IF;
 INSERT INTO platform.password_credential(user_ref_id,workspace_id,password_hash,must_change_password)
 VALUES(p_user,common.current_workspace_id(),p_hash,p_must_change)
 ON CONFLICT(user_ref_id) DO UPDATE SET password_hash=EXCLUDED.password_hash,must_change_password=EXCLUDED.must_change_password,password_changed_at=clock_timestamp();
 UPDATE platform.auth_session SET status='revoked',revoked_at=clock_timestamp() WHERE user_ref_id=p_user AND workspace_id=common.current_workspace_id() AND status='active';
END $$;
CREATE FUNCTION security.account_has_password(p_user uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.management_actor() AND EXISTS(SELECT 1 FROM platform.password_credential
 WHERE user_ref_id=p_user AND workspace_id=common.current_workspace_id());
$$;
COMMIT;
