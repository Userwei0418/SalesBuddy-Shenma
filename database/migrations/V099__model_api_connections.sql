BEGIN;
CREATE TABLE config.model_api_test (
 id uuid PRIMARY KEY, workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 purpose text NOT NULL CHECK(purpose IN ('text','asr','tts')),
 actor_user_ref_id uuid NOT NULL REFERENCES platform.user_ref(id),
 expected_version integer NOT NULL CHECK(expected_version>=0),
 request_digest text NOT NULL, config_snapshot jsonb NOT NULL,
 api_key_ciphertext bytea, cipher_format text, encryption_key_id text, api_key_tail text,
 source_guard text, restored_from_version integer,
 status text NOT NULL DEFAULT 'running' CHECK(status IN ('running','passed','failed')),
 result jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '15 minutes',
 UNIQUE(workspace_id,id),
 CHECK((api_key_ciphertext IS NULL AND cipher_format IS NULL AND encryption_key_id IS NULL AND api_key_tail IS NULL)
 OR (api_key_ciphertext IS NOT NULL AND cipher_format='aes256gcm-v1' AND encryption_key_id IS NOT NULL
 AND octet_length(api_key_ciphertext)>=29 AND length(api_key_tail)=4))
);
CREATE INDEX model_api_test_recent ON config.model_api_test(workspace_id,purpose,created_at DESC);
CREATE TABLE config.model_api_release (
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 purpose text NOT NULL CHECK(purpose IN ('text','asr','tts')),
 version_no integer NOT NULL CHECK(version_no>0), config_snapshot jsonb NOT NULL,
 api_key_ciphertext bytea, cipher_format text, encryption_key_id text, api_key_tail text,
 test_id uuid NOT NULL UNIQUE, created_by_user_ref_id uuid NOT NULL REFERENCES platform.user_ref(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), restored_from_version integer,
 PRIMARY KEY(workspace_id,purpose,version_no),
 FOREIGN KEY(workspace_id,test_id) REFERENCES config.model_api_test(workspace_id,id),
 FOREIGN KEY(workspace_id,purpose,restored_from_version)
 REFERENCES config.model_api_release(workspace_id,purpose,version_no),
 CHECK(restored_from_version IS NULL OR restored_from_version<version_no),
 CHECK((api_key_ciphertext IS NULL AND cipher_format IS NULL AND encryption_key_id IS NULL AND api_key_tail IS NULL)
 OR (api_key_ciphertext IS NOT NULL AND cipher_format='aes256gcm-v1' AND encryption_key_id IS NOT NULL
 AND octet_length(api_key_ciphertext)>=29 AND length(api_key_tail)=4))
);
CREATE TABLE config.model_api_current (
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id), purpose text NOT NULL,
 version_no integer NOT NULL,
 PRIMARY KEY(workspace_id,purpose),
 FOREIGN KEY(workspace_id,purpose,version_no) REFERENCES config.model_api_release(workspace_id,purpose,version_no)
);
ALTER TABLE config.model_api_test ENABLE ROW LEVEL SECURITY;
ALTER TABLE config.model_api_release ENABLE ROW LEVEL SECURITY;
ALTER TABLE config.model_api_current ENABLE ROW LEVEL SECURITY;
CREATE POLICY model_api_test_admin ON config.model_api_test FOR ALL
 USING(workspace_id=common.current_workspace_id() AND common.current_role_code()='administrator'
       AND security.has_active_role('administrator'))
 WITH CHECK(workspace_id=common.current_workspace_id() AND common.current_role_code()='administrator'
       AND security.has_active_role('administrator'));
CREATE POLICY model_api_release_read ON config.model_api_release FOR SELECT
 USING(workspace_id=common.current_workspace_id());
CREATE POLICY model_api_release_write ON config.model_api_release FOR INSERT
 WITH CHECK(workspace_id=common.current_workspace_id() AND common.current_role_code()='administrator'
       AND security.has_active_role('administrator'));
CREATE POLICY model_api_current_read ON config.model_api_current FOR SELECT
 USING(workspace_id=common.current_workspace_id());
CREATE POLICY model_api_current_insert ON config.model_api_current FOR INSERT
 WITH CHECK(workspace_id=common.current_workspace_id() AND common.current_role_code()='administrator'
       AND security.has_active_role('administrator'));
CREATE POLICY model_api_current_update ON config.model_api_current FOR UPDATE
 USING(workspace_id=common.current_workspace_id() AND common.current_role_code()='administrator'
       AND security.has_active_role('administrator'))
 WITH CHECK(workspace_id=common.current_workspace_id() AND common.current_role_code()='administrator'
       AND security.has_active_role('administrator'));
CREATE TRIGGER model_api_release_append_only BEFORE UPDATE OR DELETE OR TRUNCATE
 ON config.model_api_release FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();
-- Reconcile roles provisioned both before and after this migration.
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v098;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
 LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r record; reconciled integer;
BEGIN
 reconciled:=security.reconcile_runtime_grants_v098();
 FOR r IN SELECT DISTINCT role.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles role ON role.oid=a.grantee
  WHERE n.nspname='config' AND c.relname='agent_runtime_config'
   AND a.privilege_type='SELECT' AND a.grantee<>c.relowner
 LOOP
  EXECUTE format('GRANT USAGE ON SCHEMA config TO %I',r.rolname);
  EXECUTE format('GRANT SELECT,INSERT,UPDATE ON config.model_api_test,config.model_api_current TO %I',r.rolname);
  EXECUTE format('GRANT SELECT,INSERT ON config.model_api_release TO %I',r.rolname);
  reconciled:=reconciled+1;
 END LOOP;
 RETURN reconciled;
END $$;
-- Preserve the predecessor owner and exact execution ACL; do not expose a maintenance function.
DO $$ DECLARE permission record; function_owner name; BEGIN
 SELECT r.rolname INTO function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
  WHERE p.oid='security.reconcile_runtime_grants_v098()'::regprocedure;
 FOR permission IN SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid='security.reconcile_runtime_grants()'::regprocedure
 LOOP EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants() FROM %s CASCADE',
  CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END); END LOOP;
 EXECUTE format('ALTER FUNCTION security.reconcile_runtime_grants() OWNER TO %I',function_owner);
 FOR permission IN SELECT a.grantee,a.is_grantable,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='security.reconcile_runtime_grants_v098()'::regprocedure AND a.privilege_type='EXECUTE'
 LOOP EXECUTE format('GRANT EXECUTE ON FUNCTION security.reconcile_runtime_grants() TO %s%s',
  CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END,
  CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END); END LOOP;
END $$;
SELECT security.reconcile_runtime_grants();
COMMENT ON TABLE config.model_api_test IS '用途接口测试回执；只有同版本、同发起人、未过期成功测试可发布；凭据加密';
COMMENT ON TABLE config.model_api_release IS '按公司/用途追加发布版本；历史API不返回密文或旧密钥';
COMMENT ON TABLE config.model_api_current IS '用途当前接口绑定；无记录继承原配置，升级不改变现有提供方';
COMMIT;
