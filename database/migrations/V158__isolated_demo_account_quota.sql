BEGIN;
-- Fail promptly instead of waiting behind a production account transaction.
SET LOCAL lock_timeout='2s';
SET LOCAL statement_timeout='30s';
SELECT security.lock_deployment_account_quota();

-- Empty by default: existing deployment counters, limits and companies do not
-- change. Only maintenance may enroll a new, empty demonstration company.
CREATE TABLE security.isolated_demo_account_quota (
 workspace_id uuid PRIMARY KEY REFERENCES platform.workspace(id),
 max_active_accounts integer NOT NULL CHECK(max_active_accounts>0),
 active_accounts bigint NOT NULL DEFAULT 0 CHECK(active_accounts>=0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE security.isolated_demo_account_quota ENABLE ROW LEVEL SECURITY;
ALTER TABLE security.isolated_demo_account_quota FORCE ROW LEVEL SECURITY;
REVOKE ALL ON security.isolated_demo_account_quota FROM PUBLIC;
COMMENT ON TABLE security.isolated_demo_account_quota IS
 'Maintenance-only isolated demo seats. Exact workspace enrollment; does not consume or increase production deployment seats.';
CREATE TRIGGER isolated_demo_account_quota_lock BEFORE INSERT OR UPDATE OR DELETE
 ON security.isolated_demo_account_quota FOR EACH STATEMENT
 EXECUTE FUNCTION security.lock_account_quota_write();

-- Keep enrollment stable for the complete lifetime of its accounts. A later
-- maintenance edit must not silently move their seats between counters.
CREATE FUNCTION security.guard_isolated_demo_quota_configuration() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='DELETE' THEN
  IF OLD.active_accounts<>0 OR EXISTS(SELECT 1 FROM platform.user_ref WHERE workspace_id=OLD.workspace_id) THEN
   RAISE EXCEPTION '存在账号的演示公司不能移除独立额度';
  END IF;
  RETURN OLD;
 END IF;
 IF TG_OP='UPDATE' AND NEW.workspace_id IS DISTINCT FROM OLD.workspace_id THEN
  RAISE EXCEPTION '演示额度所属公司不可修改';
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.active_accounts<>0 OR EXISTS(SELECT 1 FROM platform.user_ref WHERE workspace_id=NEW.workspace_id)
     OR NOT EXISTS(SELECT 1 FROM platform.workspace WHERE id=NEW.workspace_id
       AND status='active' AND deleted_at IS NULL AND attributes->>'kind'='demo')
     OR EXISTS(SELECT 1 FROM security.company_management_grant
       WHERE source_workspace_id=NEW.workspace_id OR target_workspace_id=NEW.workspace_id) THEN
   RAISE EXCEPTION '独立额度必须从无账号和跨公司授权的新演示公司初始化';
  END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION security.guard_isolated_demo_quota_configuration() FROM PUBLIC;
CREATE TRIGGER isolated_demo_quota_configuration BEFORE INSERT OR DELETE OR UPDATE OF workspace_id
 ON security.isolated_demo_account_quota FOR EACH ROW
 EXECUTE FUNCTION security.guard_isolated_demo_quota_configuration();

CREATE FUNCTION security.register_isolated_demo_quota(p_workspace uuid,p_limit integer) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM security.lock_deployment_account_quota();
 IF p_limit IS NULL OR p_limit<1 THEN RAISE EXCEPTION '演示账号额度必须大于零'; END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.workspace WHERE id=p_workspace
   AND status='active' AND deleted_at IS NULL AND attributes->>'kind'='demo') THEN
  RAISE EXCEPTION '仅可配置有效隔离演示公司的独立额度';
 END IF;
 -- Enroll before the very first user, including inactive/deleted users. This
 -- avoids moving an existing company's seats out of the production counter.
 IF EXISTS(SELECT 1 FROM platform.user_ref WHERE workspace_id=p_workspace) THEN
  RAISE EXCEPTION '独立额度仅可在新演示公司创建账号前配置';
 END IF;
 IF EXISTS(SELECT 1 FROM security.company_management_grant
   WHERE source_workspace_id=p_workspace OR target_workspace_id=p_workspace) THEN
  RAISE EXCEPTION '隔离演示公司不能包含跨公司管理授权';
 END IF;
 INSERT INTO security.isolated_demo_account_quota(workspace_id,max_active_accounts)
 VALUES(p_workspace,p_limit);
END $$;
REVOKE ALL ON FUNCTION security.register_isolated_demo_quota(uuid,integer) FROM PUBLIC;

CREATE OR REPLACE FUNCTION security.guard_deployment_account_quota() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE old_counted boolean:=false; new_counted boolean:=false;
 old_managed boolean:=false; new_managed boolean:=false;
 old_bucket uuid; new_bucket uuid; bucket record;
 caller_role text; maintenance boolean; account_limit integer;
BEGIN
 IF TG_OP<>'INSERT' THEN
  old_managed:=COALESCE(OLD.attributes->>'platform_managed','false')='true';
  old_counted:=OLD.status='active' AND OLD.deleted_at IS NULL AND NOT old_managed;
  SELECT workspace_id INTO old_bucket FROM security.isolated_demo_account_quota
   WHERE workspace_id=OLD.workspace_id;
 END IF;
 IF TG_OP<>'DELETE' THEN
  new_managed:=COALESCE(NEW.attributes->>'platform_managed','false')='true';
  new_counted:=NEW.status='active' AND NEW.deleted_at IS NULL AND NOT new_managed;
  SELECT workspace_id INTO new_bucket FROM security.isolated_demo_account_quota
   WHERE workspace_id=NEW.workspace_id;
 END IF;
 -- Preserve the original mirror-identity protection, including maintenance's
 -- inability to bypass an ordinary account's limit just by doing direct SQL.
 IF TG_OP<>'DELETE' AND old_managed IS DISTINCT FROM new_managed THEN
  caller_role:=COALESCE(NULLIF(current_setting('role',true),'none'),session_user::text);
  SELECT r.rolsuper OR pg_has_role(r.oid,c.relowner,'USAGE') INTO maintenance
   FROM pg_roles r CROSS JOIN pg_class c
   WHERE r.rolname=caller_role AND c.oid='security.deployment_account_quota'::regclass;
  IF maintenance IS NOT TRUE THEN
   RAISE EXCEPTION '平台管理身份只能由部署维护设置' USING ERRCODE='42501';
  END IF;
 END IF;
 -- NULL bucket is the unchanged deployment quota. Aggregate OLD and NEW so
 -- ordinary edits/upserts charge zero, while a maintenance workspace move
 -- releases its old bucket and charges its new bucket in the same transaction.
 -- The existing BEFORE STATEMENT advisory lock serializes every account write.
 FOR bucket IN SELECT workspace_id,sum(delta)::bigint AS delta FROM (
   SELECT old_bucket AS workspace_id,-old_counted::integer AS delta
   UNION ALL SELECT new_bucket,new_counted::integer
  ) changes GROUP BY workspace_id HAVING sum(delta)<>0 ORDER BY workspace_id NULLS FIRST
 LOOP
  IF bucket.workspace_id IS NULL THEN
   UPDATE security.deployment_account_quota SET active_accounts=active_accounts+bucket.delta
    WHERE singleton AND active_accounts+bucket.delta>=0
     AND (bucket.delta<0 OR active_accounts+bucket.delta<=max_active_accounts);
   IF NOT FOUND THEN
    SELECT max_active_accounts INTO account_limit FROM security.deployment_account_quota WHERE singleton;
    IF account_limit IS NULL OR bucket.delta<0 THEN
     RAISE EXCEPTION '账号额度状态异常，请联系部署维护人员' USING ERRCODE='P0001';
    END IF;
    RAISE EXCEPTION '启用账号已达到部署上限（%个），请先停用不再使用的账号',account_limit USING ERRCODE='P0001';
   END IF;
  ELSE
   UPDATE security.isolated_demo_account_quota SET active_accounts=active_accounts+bucket.delta
    WHERE workspace_id=bucket.workspace_id AND active_accounts+bucket.delta>=0
     AND (bucket.delta<0 OR active_accounts+bucket.delta<=max_active_accounts);
   IF NOT FOUND THEN
    SELECT max_active_accounts INTO account_limit FROM security.isolated_demo_account_quota
     WHERE workspace_id=bucket.workspace_id;
    IF account_limit IS NULL OR bucket.delta<0 THEN
     RAISE EXCEPTION '演示账号额度状态异常，请联系部署维护人员' USING ERRCODE='P0001';
    END IF;
    RAISE EXCEPTION '启用账号已达到演示公司独立上限（%个），请先停用不再使用的账号',account_limit USING ERRCODE='P0001';
   END IF;
  END IF;
 END LOOP;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION security.deployment_account_quota_status() RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
 IF security.authorization_has('organization.read') IS NOT TRUE THEN
  RAISE EXCEPTION '需要账号管理查看权限' USING ERRCODE='42501';
 END IF;
 SELECT jsonb_build_object('scope','workspace','limit',max_active_accounts,
  'used',active_accounts,'remaining',GREATEST(0,max_active_accounts-active_accounts))
 INTO result FROM security.isolated_demo_account_quota WHERE workspace_id=common.current_workspace_id();
 IF result IS NOT NULL THEN RETURN result; END IF;
 SELECT jsonb_build_object('scope','deployment','limit',max_active_accounts,
  'used',active_accounts,'remaining',GREATEST(0,max_active_accounts-active_accounts))
 INTO result FROM security.deployment_account_quota WHERE singleton;
 IF result IS NULL THEN RAISE EXCEPTION '账号额度尚未配置' USING ERRCODE='P0001'; END IF;
 RETURN result;
END $$;

-- Align new maintenance objects with the established quota owner. CREATE OR
-- REPLACE above preserves the existing guard/status function owners and ACLs.
DO $$ DECLARE quota_owner name; BEGIN
 SELECT r.rolname INTO quota_owner FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner
  WHERE c.oid='security.deployment_account_quota'::regclass;
 EXECUTE format('ALTER TABLE security.isolated_demo_account_quota OWNER TO %I',quota_owner);
 EXECUTE format('ALTER FUNCTION security.register_isolated_demo_quota(uuid,integer) OWNER TO %I',quota_owner);
 EXECUTE format('ALTER FUNCTION security.guard_isolated_demo_quota_configuration() OWNER TO %I',quota_owner);
END $$;

ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v157;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r record; reconciled integer;
BEGIN
 reconciled:=security.reconcile_runtime_grants_v157();
 FOR r IN SELECT DISTINCT role.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles role ON role.oid=a.grantee
  WHERE n.nspname='platform' AND c.relname='user_ref'
   AND a.privilege_type='SELECT' AND a.grantee<>c.relowner
 LOOP
  EXECUTE format('REVOKE ALL ON security.isolated_demo_account_quota FROM %I',r.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION security.register_isolated_demo_quota(uuid,integer),security.guard_isolated_demo_quota_configuration() FROM %I',r.rolname);
 END LOOP;
 RETURN reconciled;
END $$;
-- Preserve the maintenance function's owner and exact ACL across upgrades.
DO $$ DECLARE permission record; function_owner name; BEGIN
 SELECT r.rolname INTO function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
  WHERE p.oid='security.reconcile_runtime_grants_v157()'::regprocedure;
 FOR permission IN SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid='security.reconcile_runtime_grants()'::regprocedure
 LOOP EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants() FROM %s CASCADE',
  CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END); END LOOP;
 EXECUTE format('ALTER FUNCTION security.reconcile_runtime_grants() OWNER TO %I',function_owner);
 FOR permission IN SELECT a.grantee,a.is_grantable,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='security.reconcile_runtime_grants_v157()'::regprocedure AND a.privilege_type='EXECUTE'
 LOOP EXECUTE format('GRANT EXECUTE ON FUNCTION security.reconcile_runtime_grants() TO %s%s',
  CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END,
  CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END); END LOOP;
END $$;
SELECT security.reconcile_runtime_grants();
-- Preserve production scheduling; registered demonstration accounts never
-- enqueue background AI reviews merely because they remain enabled.
CREATE OR REPLACE FUNCTION ops.enqueue_daily_sales_competency_reviews() RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'platform', 'config', 'insight', 'ops'
    AS $$
DECLARE
  inserted_count integer;
BEGIN
  WITH framework AS (
    SELECT f.version_no
      FROM config.sales_competency_framework f
     WHERE f.status = 'active'
       AND f.effective_to = 'infinity'
     ORDER BY (f.workspace_id IS NOT NULL) DESC, f.version_no DESC
     LIMIT 1
  ), sales_users AS (
    SELECT u.workspace_id, u.id AS user_ref_id,
           COALESCE(array_agg(DISTINCT tm.team_id::text)
             FILTER (WHERE tm.team_id IS NOT NULL), ARRAY[]::text[]) AS team_ids
      FROM platform.user_ref u
      JOIN platform.role_binding ra
        ON ra.user_ref_id = u.id
       AND ra.workspace_id = u.workspace_id
       AND ra.role_code = 'sales'
       AND clock_timestamp() >= ra.valid_from
       AND clock_timestamp() < ra.valid_to
      LEFT JOIN platform.team_membership tm
        ON tm.user_ref_id = u.id
       AND tm.workspace_id = u.workspace_id
       AND clock_timestamp() >= tm.valid_from
       AND clock_timestamp() < tm.valid_to
     WHERE u.deleted_at IS NULL AND u.status = 'active'
       AND NOT EXISTS(SELECT 1 FROM security.isolated_demo_account_quota q WHERE q.workspace_id=u.workspace_id)
     GROUP BY u.workspace_id, u.id
  ), inserted_reviews AS (
    INSERT INTO insight.sales_competency_review (
      workspace_id, subject_user_ref_id, review_date, framework_version, status
    )
    SELECT s.workspace_id, s.user_ref_id,
           timezone('Asia/Shanghai', clock_timestamp())::date,
           f.version_no, 'queued'
      FROM sales_users s CROSS JOIN framework f
    ON CONFLICT (workspace_id, subject_user_ref_id, review_date, framework_version)
    DO NOTHING
    RETURNING id, workspace_id, subject_user_ref_id
  )
  INSERT INTO ops.job (
    workspace_id, job_type, aggregate_type, aggregate_id, payload, priority
  )
  SELECT r.workspace_id, 'sales_competency.review', 'sales_competency_review', r.id,
         jsonb_build_object(
           'user_id', r.subject_user_ref_id::text,
           'role', 'sales',
           'data_scope', 'self',
           'team_ids', s.team_ids
         ), 45
    FROM inserted_reviews r
    JOIN sales_users s ON s.user_ref_id = r.subject_user_ref_id
  ;

  GET DIAGNOSTICS inserted_count = ROW_COUNT;
  RETURN inserted_count;
END;
$$;

INSERT INTO ops.schema_migration(version,description) VALUES('V158','Independent maintenance-enrolled isolated demo account quota');
COMMIT;
