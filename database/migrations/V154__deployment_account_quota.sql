BEGIN;
SET LOCAL row_security=off;
-- Freeze account writes while taking the initial count and installing guards.
LOCK TABLE platform.user_ref IN SHARE ROW EXCLUSIVE MODE;

-- One quota for this customer deployment, shared by every company. Existing
-- accounts remain enabled even when their count is above the initial limit.
CREATE TABLE security.deployment_account_quota (
 singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
 max_active_accounts integer NOT NULL DEFAULT 50 CHECK (max_active_accounts > 0),
 active_accounts bigint NOT NULL CHECK (active_accounts >= 0)
);
INSERT INTO security.deployment_account_quota(singleton,max_active_accounts,active_accounts)
 SELECT true,50,count(*) FROM platform.user_ref
 WHERE status='active' AND deleted_at IS NULL
  AND COALESCE(attributes->>'platform_managed','false')<>'true';
ALTER TABLE security.deployment_account_quota ENABLE ROW LEVEL SECURITY;
ALTER TABLE security.deployment_account_quota FORCE ROW LEVEL SECURITY;
REVOKE ALL ON security.deployment_account_quota FROM PUBLIC;
COMMENT ON TABLE security.deployment_account_quota IS
 '部署维护专用账号额度；跨公司共用，只有平台管理镜像不占席。客户角色不能修改。降低上限不自动停用现有账号。';

CREATE FUNCTION security.lock_deployment_account_quota() RETURNS void
LANGUAGE sql VOLATILE SET search_path=pg_catalog AS $$
 SELECT pg_advisory_xact_lock(hashtextextended('deployment-account-quota',0));
$$;

CREATE FUNCTION security.lock_account_quota_write() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM security.lock_deployment_account_quota();
 RETURN NULL;
END $$;
-- Lock before tuple locks and before the existing phone/workspace trigger.
CREATE TRIGGER user_ref_account_quota_lock BEFORE INSERT OR UPDATE OR DELETE
 ON platform.user_ref FOR EACH STATEMENT EXECUTE FUNCTION security.lock_account_quota_write();
CREATE TRIGGER deployment_account_quota_lock BEFORE INSERT OR UPDATE OR DELETE
 ON security.deployment_account_quota FOR EACH STATEMENT EXECUTE FUNCTION security.lock_account_quota_write();

CREATE FUNCTION security.guard_deployment_account_quota() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE old_counted boolean:=false; new_counted boolean:=false; delta integer;
 old_managed boolean:=false; new_managed boolean:=false;
 caller_role text; maintenance boolean; account_limit integer;
BEGIN
 IF TG_OP<>'INSERT' THEN
  old_managed:=COALESCE(OLD.attributes->>'platform_managed','false')='true';
  old_counted:=OLD.status='active' AND OLD.deleted_at IS NULL AND NOT old_managed;
 END IF;
 IF TG_OP<>'DELETE' THEN
  new_managed:=COALESCE(NEW.attributes->>'platform_managed','false')='true';
  new_counted:=NEW.status='active' AND NEW.deleted_at IS NULL AND NOT new_managed;
 END IF;
 -- The runtime can edit other attributes, but cannot manufacture exempt accounts.
 IF TG_OP<>'DELETE' AND old_managed IS DISTINCT FROM new_managed THEN
  caller_role:=COALESCE(NULLIF(current_setting('role',true),'none'),session_user::text);
  SELECT r.rolsuper OR pg_has_role(r.oid,c.relowner,'USAGE') INTO maintenance
   FROM pg_roles r CROSS JOIN pg_class c
   WHERE r.rolname=caller_role AND c.oid='security.deployment_account_quota'::regclass;
  IF maintenance IS NOT TRUE THEN
   RAISE EXCEPTION '平台管理身份只能由部署维护设置' USING ERRCODE='42501';
  END IF;
 END IF;
 delta:=new_counted::integer-old_counted::integer;
 IF delta<>0 THEN
  -- Atomic arithmetic also prevents stale REPEATABLE READ snapshots from
  -- admitting two accounts. A failed user mutation rolls this change back.
  UPDATE security.deployment_account_quota SET active_accounts=active_accounts+delta
   WHERE singleton AND active_accounts+delta>=0
    AND (delta<0 OR active_accounts+delta<=max_active_accounts);
  IF NOT FOUND THEN
   SELECT max_active_accounts INTO account_limit FROM security.deployment_account_quota WHERE singleton;
   IF account_limit IS NULL OR delta<0 THEN
    RAISE EXCEPTION '账号额度状态异常，请联系部署维护人员' USING ERRCODE='P0001';
   END IF;
   RAISE EXCEPTION '启用账号已达到部署上限（%个），请先停用不再使用的账号',account_limit USING ERRCODE='P0001';
  END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
-- AFTER follows the actual action of INSERT ... ON CONFLICT DO UPDATE, so an
-- upsert does not charge both its attempted INSERT and its completed UPDATE.
CREATE TRIGGER user_ref_account_quota AFTER INSERT OR UPDATE OR DELETE
 ON platform.user_ref FOR EACH ROW EXECUTE FUNCTION security.guard_deployment_account_quota();

CREATE FUNCTION security.deployment_account_quota_status() RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
 IF security.authorization_has('organization.read') IS NOT TRUE THEN
  RAISE EXCEPTION '需要账号管理查看权限' USING ERRCODE='42501';
 END IF;
 SELECT jsonb_build_object('scope','deployment','limit',max_active_accounts,
  'used',active_accounts,'remaining',GREATEST(0,max_active_accounts-active_accounts))
 INTO result FROM security.deployment_account_quota WHERE singleton;
 IF result IS NULL THEN RAISE EXCEPTION '账号额度尚未配置' USING ERRCODE='P0001'; END IF;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION security.lock_deployment_account_quota(),security.lock_account_quota_write(),
 security.guard_deployment_account_quota(),security.deployment_account_quota_status() FROM PUBLIC;

ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v153;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r record; reconciled integer;
BEGIN
 reconciled:=security.reconcile_runtime_grants_v153();
 FOR r IN SELECT DISTINCT role.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles role ON role.oid=a.grantee
  WHERE n.nspname='platform' AND c.relname='user_ref'
   AND a.privilege_type='SELECT' AND a.grantee<>c.relowner
 LOOP
  EXECUTE format('REVOKE ALL ON security.deployment_account_quota FROM %I',r.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION security.lock_account_quota_write(),security.guard_deployment_account_quota() FROM %I',r.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.lock_deployment_account_quota(),security.deployment_account_quota_status() TO %I',r.rolname);
  reconciled:=reconciled+1;
 END LOOP;
 RETURN reconciled;
END $$;
-- Preserve the maintenance function's owner and exact ACL across upgrades.
DO $$ DECLARE permission record; function_owner name; BEGIN
 SELECT r.rolname INTO function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
  WHERE p.oid='security.reconcile_runtime_grants_v153()'::regprocedure;
 FOR permission IN SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid='security.reconcile_runtime_grants()'::regprocedure
 LOOP EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants() FROM %s CASCADE',
  CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END); END LOOP;
 EXECUTE format('ALTER FUNCTION security.reconcile_runtime_grants() OWNER TO %I',function_owner);
 FOR permission IN SELECT a.grantee,a.is_grantable,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='security.reconcile_runtime_grants_v153()'::regprocedure AND a.privilege_type='EXECUTE'
 LOOP EXECUTE format('GRANT EXECUTE ON FUNCTION security.reconcile_runtime_grants() TO %s%s',
  CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END,
  CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END); END LOOP;
END $$;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V154','Deployment-wide active account quota');
COMMIT;
