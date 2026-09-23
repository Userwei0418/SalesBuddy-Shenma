BEGIN;

-- No new counters or recovery bypass: the original account/IP windows remain the
-- sole throttle state. Read the actual window without granting table access.
CREATE FUNCTION security.login_limit_status(p_key text,p_limit integer)
RETURNS TABLE(login_locked boolean,login_retry_at timestamptz,login_attempts integer,retry_after_seconds integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE checked_at timestamptz:=clock_timestamp(); n integer:=0; retry_at timestamptz;
BEGIN
 IF p_key IS NULL OR p_key !~ '^[a-f0-9]{64}$' OR p_limit IS NULL OR p_limit NOT IN (5,50) THEN
  RAISE EXCEPTION 'invalid throttle key' USING ERRCODE='22023';
 END IF;
 SELECT t.attempts,t.window_start+interval '15 minutes' INTO n,retry_at
 FROM security.login_throttle t WHERE t.key_hash=p_key
 AND t.window_start+interval '15 minutes'>checked_at;
 n:=COALESCE(n,0);
 RETURN QUERY SELECT n>=p_limit,CASE WHEN n>=p_limit THEN retry_at END,n,
  CASE WHEN n>=p_limit THEN GREATEST(1,ceil(extract(epoch FROM retry_at-checked_at))::integer) ELSE 0 END;
END $$;

CREATE FUNCTION security.account_login_status(p_user uuid)
RETURNS TABLE(login_locked boolean,login_retry_at timestamptz,login_attempts integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE throttle_key text;
BEGIN
 IF security.management_actor() IS NOT TRUE THEN
  RAISE EXCEPTION '需要账号管理权限' USING ERRCODE='42501';
 END IF;
 SELECT encode(public.digest('account:'||w.external_workspace_id||':'||upper(btrim(u.account_code)),'sha256'),'hex')
 INTO throttle_key FROM platform.user_ref u JOIN platform.workspace w ON w.id=u.workspace_id
 WHERE u.id=p_user AND u.workspace_id=common.current_workspace_id() AND u.deleted_at IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION '账号不存在' USING ERRCODE='P0002'; END IF;
 IF throttle_key IS NULL THEN RETURN QUERY SELECT false,NULL::timestamptz,0; RETURN; END IF;
 RETURN QUERY SELECT s.login_locked,s.login_retry_at,s.login_attempts
 FROM security.login_limit_status(throttle_key,5) s;
END $$;

CREATE FUNCTION security.unlock_account_login(p_user uuid,p_reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE throttle_key text; target_name text; prior jsonb; after_value jsonb;
BEGIN
 IF security.management_actor() IS NOT TRUE THEN
  RAISE EXCEPTION '需要账号管理权限' USING ERRCODE='42501';
 END IF;
 IF p_reason IS NULL OR length(btrim(p_reason)) NOT BETWEEN 1 AND 500 THEN
  RAISE EXCEPTION '请填写解除原因（1–500 字）' USING ERRCODE='22023';
 END IF;
 SELECT encode(public.digest('account:'||w.external_workspace_id||':'||upper(btrim(u.account_code)),'sha256'),'hex'),u.display_name
 INTO throttle_key,target_name FROM platform.user_ref u JOIN platform.workspace w ON w.id=u.workspace_id
 WHERE u.id=p_user AND u.workspace_id=common.current_workspace_id() AND u.deleted_at IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION '账号不存在' USING ERRCODE='P0002'; END IF;
 IF throttle_key IS NULL THEN RAISE EXCEPTION '账号尚未配置登录标识' USING ERRCODE='22023'; END IF;
 IF common.current_role_code()<>'administrator' AND EXISTS(
  SELECT 1 FROM platform.role_binding r WHERE r.user_ref_id=p_user AND r.workspace_id=common.current_workspace_id()
  AND r.role_code IN ('operations','administrator') AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to
 ) THEN RAISE EXCEPTION '运营及系统管理员账号由系统管理员管理' USING ERRCODE='42501'; END IF;
 -- Lock the existing row so the before-state belongs to this exact deletion.
 PERFORM 1 FROM security.login_throttle WHERE key_hash=throttle_key FOR UPDATE;
 SELECT to_jsonb(s) INTO prior FROM security.account_login_status(p_user) s;
 DELETE FROM security.login_throttle WHERE key_hash=throttle_key;
 SELECT to_jsonb(s)||jsonb_build_object('reason',btrim(p_reason)) INTO after_value FROM security.account_login_status(p_user) s;
 INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,module_code,object_type,object_id,object_label,
 request_id,client_ip,user_agent,before_snapshot,after_snapshot,changed_fields)
 VALUES(common.current_workspace_id(),common.current_user_ref_id(),common.current_role_code(),
 'account.login_unlock','platform','user_ref',p_user,target_name,
 NULLIF(current_setting('app.request_id',true),'')::uuid,NULLIF(current_setting('app.client_ip',true),'')::inet,
 left(current_setting('app.user_agent',true),500),prior,after_value,
 ARRAY['login_locked','login_retry_at','login_attempts']);
END $$;

-- Preserve credential/session policy; resetting a password also clears the
-- target account window. Never delete an IP key or another account's window.
CREATE OR REPLACE FUNCTION security.set_account_password(p_user uuid,p_hash text,p_must_change boolean) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF security.management_actor() IS NOT TRUE OR NOT EXISTS(SELECT 1 FROM platform.user_ref u
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
 PERFORM security.unlock_account_login(p_user,'重置密码时解除账号限制');
END $$;

COMMENT ON FUNCTION security.account_login_status(uuid) IS '运营账号列表：仅账号当前15分钟窗口，次数包含成功前的尝试；不代表网络限制';
COMMENT ON FUNCTION security.unlock_account_login(uuid,text) IS '同事务解除指定账号限制并记录原因；目标UUID在函数内解析，无法用此入口清IP或跨工作空间账号';
-- Authentication is pre-session; management helpers enforce current active role
-- and workspace internally. Explicit grants cover later-created runtime roles.
GRANT EXECUTE ON FUNCTION security.login_limit_status(text,integer),security.account_login_status(uuid),security.unlock_account_login(uuid,text) TO PUBLIC;
COMMIT;
