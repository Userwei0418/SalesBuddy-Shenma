BEGIN;
-- Preserve the existing password policy while exposing only the server-stored
-- session surface to the API gate. No credential material crosses this function.
CREATE OR REPLACE FUNCTION security.session_credentials(p_session uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('auth_method',s.auth_method,'must_change_password',
 COALESCE(p.must_change_password,false) AND COALESCE(policy.require_initial_change,true),
 'client_channel',CASE WHEN s.auth_method='password' AND s.client_context->>'channel'='web'
                      THEN 'web' ELSE 'wechat-mini-program' END)
 FROM platform.auth_session s LEFT JOIN platform.password_credential p
 ON p.user_ref_id=s.user_ref_id AND p.workspace_id=s.workspace_id
 LEFT JOIN security.password_policy policy ON policy.workspace_id=s.workspace_id
 WHERE s.id=p_session AND s.workspace_id=common.current_workspace_id() AND s.user_ref_id=common.current_user_ref_id()
 AND s.status='active' AND s.expires_at>clock_timestamp();
$$;
INSERT INTO ops.schema_migration(version,description) VALUES('V144','Enforce mini-program and console entry permissions from persisted sessions');
COMMIT;
