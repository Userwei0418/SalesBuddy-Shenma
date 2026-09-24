BEGIN;
-- Account maintenance permissions are independent of business appointments.

CREATE FUNCTION security.authorization_password_write(p_user uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_has('account.reset_password') OR (security.authorization_has('account.create') AND EXISTS(
 SELECT 1 FROM platform.user_ref u WHERE u.id=p_user AND u.workspace_id=common.current_workspace_id()
  AND pg_xact_status(u.xmin::text::xid8)='in progress' AND u.created_at>=transaction_timestamp()));
$$;
REVOKE ALL ON FUNCTION security.authorization_password_write(uuid) FROM PUBLIC,salegent_feishu_worker;

CREATE OR REPLACE FUNCTION security.account_has_password(p_user uuid)
 RETURNS boolean
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 SELECT security.authorization_has('organization.read') AND EXISTS(SELECT 1 FROM platform.password_credential
 WHERE user_ref_id=p_user AND workspace_id=common.current_workspace_id());
$function$
;

CREATE OR REPLACE FUNCTION security.account_login_status(p_user uuid)
 RETURNS TABLE(login_locked boolean, login_retry_at timestamp with time zone, login_attempts integer)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE throttle_key text;
BEGIN
 IF (security.authorization_has('organization.read') OR security.authorization_has('account.unlock') OR security.authorization_has('account.reset_password') OR security.authorization_has('account.create')) IS NOT TRUE THEN
  RAISE EXCEPTION '需要账号管理权限' USING ERRCODE='42501';
 END IF;
 SELECT encode(public.digest('account:'||w.external_workspace_id||':'||upper(btrim(u.account_code)),'sha256'),'hex')
 INTO throttle_key FROM platform.user_ref u JOIN platform.workspace w ON w.id=u.workspace_id
 WHERE u.id=p_user AND u.workspace_id=common.current_workspace_id() AND u.deleted_at IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION '账号不存在' USING ERRCODE='P0002'; END IF;
 IF throttle_key IS NULL THEN RETURN QUERY SELECT false,NULL::timestamptz,0; RETURN; END IF;
 RETURN QUERY SELECT s.login_locked,s.login_retry_at,s.login_attempts
 FROM security.login_limit_status(throttle_key,5) s;
END $function$
;

CREATE OR REPLACE FUNCTION security.get_password_policy()
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE result jsonb;
BEGIN
 IF (security.authorization_has('organization.read') OR security.authorization_has('account.password_policy') OR security.authorization_has('account.create') OR security.authorization_has('account.reset_password')) IS NOT TRUE THEN
  RAISE EXCEPTION '需要账号管理权限' USING ERRCODE='42501';
 END IF;
 SELECT jsonb_build_object('require_initial_change',p.require_initial_change,'version_no',p.version_no)
 INTO result FROM security.password_policy p WHERE p.workspace_id=common.current_workspace_id();
 RETURN COALESCE(result,jsonb_build_object('require_initial_change',true,'version_no',0));
END $function$
;

CREATE OR REPLACE FUNCTION security.revoke_managed_account_sessions(p_user uuid)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
 IF (security.authorization_has('account.update') OR security.authorization_has('account.reset_password')) IS NOT TRUE OR NOT EXISTS(
  SELECT 1 FROM platform.user_ref u WHERE u.id=p_user
  AND u.workspace_id=common.current_workspace_id() AND u.deleted_at IS NULL
  AND COALESCE(u.attributes->>'platform_managed','false')<>'true'
 ) THEN RAISE EXCEPTION '无权管理此账号会话' USING ERRCODE='42501'; END IF;
 IF NOT security.authorization_has('authorization.accounts_manage') AND EXISTS(
  SELECT 1 FROM platform.role_binding r WHERE r.user_ref_id=p_user
  AND r.workspace_id=common.current_workspace_id() AND r.role_code IN ('operations','administrator')
  AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to
 ) THEN RAISE EXCEPTION '管理账号会话由系统管理员管理' USING ERRCODE='42501'; END IF;
 UPDATE platform.auth_session SET status='revoked',revoked_at=clock_timestamp()
 WHERE user_ref_id=p_user AND workspace_id=common.current_workspace_id() AND status='active';
END $function$
;

CREATE OR REPLACE FUNCTION security.set_account_password(p_user uuid, p_hash text, p_must_change boolean)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
 IF security.authorization_password_write(p_user) IS NOT TRUE OR NOT EXISTS(SELECT 1 FROM platform.user_ref u
 WHERE u.id=p_user AND u.workspace_id=common.current_workspace_id() AND u.deleted_at IS NULL) THEN
 RAISE EXCEPTION '无权重置账号口令' USING ERRCODE='42501'; END IF;
 IF NOT security.authorization_has('authorization.accounts_manage') AND EXISTS(SELECT 1 FROM platform.role_binding r
 WHERE r.user_ref_id=p_user AND r.workspace_id=common.current_workspace_id() AND r.role_code IN ('operations','administrator')
 AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to) THEN
 RAISE EXCEPTION '管理账号口令由系统管理员重置' USING ERRCODE='42501'; END IF;
 IF p_hash NOT LIKE 'scrypt$32768$8$1$%' THEN RAISE EXCEPTION 'invalid password encoding' USING ERRCODE='22023'; END IF;
 INSERT INTO platform.password_credential(user_ref_id,workspace_id,password_hash,must_change_password)
 VALUES(p_user,common.current_workspace_id(),p_hash,p_must_change)
 ON CONFLICT(user_ref_id) DO UPDATE SET password_hash=EXCLUDED.password_hash,must_change_password=EXCLUDED.must_change_password,password_changed_at=clock_timestamp();
 UPDATE platform.auth_session SET status='revoked',revoked_at=clock_timestamp() WHERE user_ref_id=p_user AND workspace_id=common.current_workspace_id() AND status='active';
 PERFORM security.unlock_account_after_password(p_user,'重置密码时解除账号限制');
END $function$
;

CREATE OR REPLACE FUNCTION security.set_password_policy(p_required boolean, p_version bigint)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE prior jsonb; result jsonb;
BEGIN
 IF security.authorization_has('account.password_policy') IS NOT TRUE THEN
  RAISE EXCEPTION '登录策略需要系统管理员维护' USING ERRCODE='42501';
 END IF;
 IF p_required IS NULL OR p_version IS NULL OR p_version<0 THEN
  RAISE EXCEPTION '无效的登录策略' USING ERRCODE='22023';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('account-management:'||common.current_workspace_id()::text,0));
 prior:=security.get_password_policy();
 IF (prior->>'version_no')::bigint<>p_version THEN
  RAISE EXCEPTION '登录策略已修改，请刷新后重试' USING ERRCODE='P0001';
 END IF;
 INSERT INTO security.password_policy(workspace_id,require_initial_change)
 VALUES(common.current_workspace_id(),p_required)
 ON CONFLICT(workspace_id) DO UPDATE SET require_initial_change=EXCLUDED.require_initial_change,
 version_no=password_policy.version_no+1,updated_at=clock_timestamp();
 result:=security.get_password_policy();
 INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,module_code,object_type,object_id,object_label,
 request_id,client_ip,user_agent,before_snapshot,after_snapshot,changed_fields)
 VALUES(common.current_workspace_id(),common.current_user_ref_id(),common.current_role_code(),
 'account.password_policy','platform','workspace',common.current_workspace_id(),'首次登录改密要求',
 NULLIF(current_setting('app.request_id',true),'')::uuid,NULLIF(current_setting('app.client_ip',true),'')::inet,
 left(current_setting('app.user_agent',true),500),prior,result,ARRAY['require_initial_change']);
 RETURN result;
END $function$
;

CREATE OR REPLACE FUNCTION security.unlock_account_after_password(p_user uuid, p_reason text)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE throttle_key text; target_name text; prior jsonb; after_value jsonb;
BEGIN
 IF (security.authorization_has('account.unlock') OR security.authorization_password_write(p_user)) IS NOT TRUE THEN
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
 IF NOT security.authorization_has('authorization.accounts_manage') AND EXISTS(
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
END $function$
;
REVOKE ALL ON FUNCTION security.unlock_account_after_password(uuid,text) FROM PUBLIC,salegent_feishu_worker;

CREATE OR REPLACE FUNCTION security.unlock_account_login(p_user uuid,p_reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT security.authorization_has('account.unlock') THEN RAISE insufficient_privilege; END IF;
 PERFORM security.unlock_account_after_password(p_user,p_reason);
END $$;
CREATE FUNCTION security.check_permission_administrator() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT security.authorization_has('account.update') AND NOT security.authorization_has('authorization.accounts_manage') THEN
  RAISE insufficient_privilege;
 END IF;
 PERFORM security.require_permission_administrator();
END $$;
REVOKE ALL ON FUNCTION security.check_permission_administrator() FROM PUBLIC,salegent_feishu_worker;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v128;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_v128();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
 AND rolname NOT LIKE 'pg\_%' ESCAPE '\' AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.check_permission_administrator() TO %I',r.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION security.authorization_password_write(uuid),security.unlock_account_after_password(uuid,text) FROM %I',r.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_v128() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V129','Permission controlled account maintenance');
COMMIT;
