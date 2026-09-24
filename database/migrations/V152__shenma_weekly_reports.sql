BEGIN;
INSERT INTO config.permission_catalog(code,label,module,scopes) VALUES
 ('access.business_web','使用业务 Web','使用入口',ARRAY['workspace']),
 ('weekly_report.generate','生成本人周报','个人周报',ARRAY['self']),
 ('weekly_report.read','查看本人周报','个人周报',ARRAY['self']),
 ('weekly_report.edit','编辑本人周报','个人周报',ARRAY['self']),
 ('weekly_report.cancel','取消本人周报生成','个人周报',ARRAY['self']);
UPDATE config.permission_role_default SET permissions=permissions || (
 SELECT jsonb_agg(jsonb_build_object('permission',code,'scope',scopes[1],'team_ids','[]'::jsonb))
 FROM config.permission_catalog WHERE code='access.business_web' OR code LIKE 'weekly_report.%'
) WHERE role_code IN ('sales','supervisor','manager');
INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code)
 SELECT r.workspace_id,r.id,c.code,c.scopes[1] FROM config.permission_role r
 CROSS JOIN config.permission_catalog c WHERE r.builtin_role_code IN ('sales','supervisor','manager')
 AND (c.code='access.business_web' OR c.code LIKE 'weekly_report.%');
UPDATE config.permission_role SET version_no=version_no+1,updated_at=clock_timestamp()
 WHERE builtin_role_code IN ('sales','supervisor','manager');

CREATE TABLE insight.weekly_report (
 id uuid PRIMARY KEY, workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 author_id uuid NOT NULL REFERENCES platform.user_ref(id), request_id uuid NOT NULL,
 job_id uuid REFERENCES ops.job(id),
 status text NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
 result_status text CHECK(result_status IN ('ready','insufficient_data','invalid_input')),
 input_snapshot text NOT NULL, input_sha256 text NOT NULL CHECK(length(input_sha256)=64),
 binding jsonb NOT NULL, original_result jsonb, draft_markdown text, draft_version integer NOT NULL DEFAULT 0,
 error_code text, runtime_metadata jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), started_at timestamptz, finished_at timestamptz,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(workspace_id,author_id,request_id), UNIQUE(workspace_id,author_id,id),
 CHECK(draft_version>=0), CHECK(draft_version=0 OR result_status='ready')
);
CREATE INDEX weekly_report_owner ON insight.weekly_report(workspace_id,author_id,created_at DESC);
CREATE TABLE insight.weekly_report_revision (
 workspace_id uuid NOT NULL, author_id uuid NOT NULL, report_id uuid NOT NULL,
 version_no integer NOT NULL CHECK(version_no>0), body_markdown text NOT NULL,
 source text NOT NULL CHECK(source IN ('agent','manual')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(report_id,version_no),
 FOREIGN KEY(workspace_id,author_id,report_id) REFERENCES insight.weekly_report(workspace_id,author_id,id)
);
ALTER TABLE insight.weekly_report ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.weekly_report FORCE ROW LEVEL SECURITY;
ALTER TABLE insight.weekly_report_revision ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.weekly_report_revision FORCE ROW LEVEL SECURITY;
CREATE POLICY weekly_report_select ON insight.weekly_report FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND author_id=common.current_user_ref_id()
 AND security.authorization_has('weekly_report.read'));
CREATE POLICY weekly_report_insert ON insight.weekly_report FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND author_id=common.current_user_ref_id()
 AND security.authorization_has('weekly_report.generate'));
CREATE POLICY weekly_report_update ON insight.weekly_report FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND author_id=common.current_user_ref_id()
 AND (security.authorization_has('weekly_report.generate') OR security.authorization_has('weekly_report.edit')
      OR security.authorization_has('weekly_report.cancel')))
 WITH CHECK(workspace_id=common.current_workspace_id() AND author_id=common.current_user_ref_id());
CREATE POLICY weekly_revision_select ON insight.weekly_report_revision FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND author_id=common.current_user_ref_id()
 AND security.authorization_has('weekly_report.read'));
CREATE POLICY weekly_revision_insert ON insight.weekly_report_revision FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND author_id=common.current_user_ref_id()
 AND (security.authorization_has('weekly_report.generate') OR security.authorization_has('weekly_report.edit')));
CREATE TRIGGER weekly_revision_append_only BEFORE UPDATE OR DELETE OR TRUNCATE
 ON insight.weekly_report_revision FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();
CREATE FUNCTION insight.freeze_weekly_source() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF ROW(NEW.id,NEW.workspace_id,NEW.author_id,NEW.request_id,NEW.job_id,NEW.input_snapshot,NEW.input_sha256,NEW.binding,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.id,OLD.workspace_id,OLD.author_id,OLD.request_id,OLD.job_id,OLD.input_snapshot,OLD.input_sha256,OLD.binding,OLD.created_at)
 OR (OLD.original_result IS NOT NULL AND NEW.original_result IS DISTINCT FROM OLD.original_result) THEN
  RAISE EXCEPTION 'Weekly source and original result are immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER freeze_weekly_source BEFORE UPDATE ON insight.weekly_report
 FOR EACH ROW EXECUTE FUNCTION insight.freeze_weekly_source();
-- Detect hidden own records instead of silently claiming a complete report.
-- Returns only the current user's count, never another user's rows or contents.
CREATE FUNCTION security.weekly_source_count(p_start timestamptz,p_end timestamptz,p_waterline timestamptz)
 RETURNS bigint LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT CASE WHEN security.authorization_has('weekly_report.generate') AND security.authorization_has('visit.read')
 THEN (SELECT count(*) FROM activity.visit WHERE workspace_id=common.current_workspace_id()
 AND recorder_user_ref_id=common.current_user_ref_id() AND deleted_at IS NULL
 AND status IN ('confirmed','archived') AND created_at>=p_start AND created_at<p_end AND created_at<=p_waterline)
 ELSE NULL END;
$$;
REVOKE ALL ON FUNCTION security.weekly_source_count(timestamptz,timestamptz,timestamptz) FROM PUBLIC;
CREATE OR REPLACE FUNCTION security.session_credentials(p_session uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('auth_method',s.auth_method,'must_change_password',
 COALESCE(p.must_change_password,false) AND COALESCE(policy.require_initial_change,true),
 'client_channel',CASE WHEN s.auth_method='password' AND s.client_context->>'channel' IN ('web','business_web')
                      THEN s.client_context->>'channel' ELSE 'wechat-mini-program' END)
 FROM platform.auth_session s LEFT JOIN platform.password_credential p
 ON p.user_ref_id=s.user_ref_id AND p.workspace_id=s.workspace_id
 LEFT JOIN security.password_policy policy ON policy.workspace_id=s.workspace_id
 WHERE s.id=p_session AND s.workspace_id=common.current_workspace_id() AND s.user_ref_id=common.current_user_ref_id()
 AND s.status='active' AND s.expires_at>clock_timestamp();
$$;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v151;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
 LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r record; reconciled integer;
BEGIN
 reconciled:=security.reconcile_runtime_grants_v151();
 FOR r IN SELECT DISTINCT role.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles role ON role.oid=a.grantee
  WHERE n.nspname='config' AND c.relname='agent_runtime_config'
   AND a.privilege_type='SELECT' AND a.grantee<>c.relowner
 LOOP
  EXECUTE format('GRANT SELECT,INSERT,UPDATE ON insight.weekly_report TO %I',r.rolname);
  EXECUTE format('GRANT SELECT,INSERT ON insight.weekly_report_revision TO %I',r.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.weekly_source_count(timestamptz,timestamptz,timestamptz) TO %I',r.rolname);
  reconciled:=reconciled+1;
 END LOOP;
 RETURN reconciled;
END $$;
-- Preserve the predecessor owner and exact execution ACL; do not expose a maintenance function.
DO $$ DECLARE permission record; function_owner name; BEGIN
 SELECT r.rolname INTO function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
  WHERE p.oid='security.reconcile_runtime_grants_v151()'::regprocedure;
 FOR permission IN SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid='security.reconcile_runtime_grants()'::regprocedure
 LOOP EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants() FROM %s CASCADE',
  CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END); END LOOP;
 EXECUTE format('ALTER FUNCTION security.reconcile_runtime_grants() OWNER TO %I',function_owner);
 FOR permission IN SELECT a.grantee,a.is_grantable,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='security.reconcile_runtime_grants_v151()'::regprocedure AND a.privilege_type='EXECUTE'
 LOOP EXECUTE format('GRANT EXECUTE ON FUNCTION security.reconcile_runtime_grants() TO %s%s',
  CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END,
  CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END); END LOOP;
END $$;
SELECT security.reconcile_runtime_grants();

INSERT INTO ops.schema_migration(version,description) VALUES('V152','Shenma weekly.v2 business Web reports');
COMMIT;
