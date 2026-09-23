BEGIN;

CREATE TABLE security.password_policy (
 workspace_id uuid PRIMARY KEY REFERENCES platform.workspace(id),
 require_initial_change boolean NOT NULL DEFAULT true,
 version_no bigint NOT NULL DEFAULT 1 CHECK(version_no>0),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE security.password_policy ENABLE ROW LEVEL SECURITY;
ALTER TABLE security.password_policy FORCE ROW LEVEL SECURITY;
REVOKE ALL ON security.password_policy FROM PUBLIC;

CREATE FUNCTION security.get_password_policy() RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
 IF security.management_actor() IS NOT TRUE THEN
  RAISE EXCEPTION '需要账号管理权限' USING ERRCODE='42501';
 END IF;
 SELECT jsonb_build_object('require_initial_change',p.require_initial_change,'version_no',p.version_no)
 INTO result FROM security.password_policy p WHERE p.workspace_id=common.current_workspace_id();
 RETURN COALESCE(result,jsonb_build_object('require_initial_change',true,'version_no',0));
END $$;

CREATE FUNCTION security.set_password_policy(p_required boolean,p_version bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE prior jsonb; result jsonb;
BEGIN
 IF security.management_actor() IS NOT TRUE OR common.current_role_code()<>'administrator' THEN
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
END $$;

-- Preserve each credential's initial-password flag. Disabling the company gate
-- does not erase it: enabling it later still protects unchanged/reset passwords.
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
 SELECT to_jsonb(a)||jsonb_build_object('password_hash',p.password_hash,'must_change_password',p.must_change_password AND COALESCE(policy.require_initial_change,true))
 FROM selected CROSS JOIN LATERAL security.resolve_account_actor(selected.workspace,p_account,p_role) a
 JOIN platform.password_credential p ON p.user_ref_id=a.user_id::uuid AND p.workspace_id=a.workspace_id::uuid
 LEFT JOIN security.password_policy policy ON policy.workspace_id=p.workspace_id;
$$;

CREATE OR REPLACE FUNCTION security.session_credentials(p_session uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('auth_method',s.auth_method,'must_change_password',
 COALESCE(p.must_change_password,false) AND COALESCE(policy.require_initial_change,true))
 FROM platform.auth_session s LEFT JOIN platform.password_credential p
 ON p.user_ref_id=s.user_ref_id AND p.workspace_id=s.workspace_id
 LEFT JOIN security.password_policy policy ON policy.workspace_id=s.workspace_id
 WHERE s.id=p_session AND s.workspace_id=common.current_workspace_id() AND s.user_ref_id=common.current_user_ref_id()
 AND s.status='active' AND s.expires_at>clock_timestamp();
$$;
GRANT EXECUTE ON FUNCTION security.get_password_policy(),security.set_password_policy(boolean,bigint) TO PUBLIC;
COMMENT ON TABLE security.password_policy IS '公司初始或重置密码改密要求；默认开启，停用不清除账号初始密码标记';
COMMIT;
