BEGIN;

-- Preferences only: no business table, company quota, authorization default,
-- CRM source marker or Feishu target is changed by this migration.
CREATE TABLE crm.opportunity_saved_view (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 owner_user_ref_id uuid NOT NULL,
 name text NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 40),
 config jsonb NOT NULL CHECK(jsonb_typeof(config)='object' AND octet_length(config::text)<=32768),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 deleted_at timestamptz,
 version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 FOREIGN KEY(owner_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
CREATE INDEX opportunity_saved_view_owner
 ON crm.opportunity_saved_view(workspace_id,owner_user_ref_id,updated_at DESC,id DESC)
 WHERE deleted_at IS NULL;

ALTER TABLE crm.opportunity_saved_view ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.opportunity_saved_view FORCE ROW LEVEL SECURITY;
CREATE POLICY opportunity_view_read ON crm.opportunity_saved_view FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND owner_user_ref_id=common.current_user_ref_id()
 AND security.authorization_has('opportunity.read'));
CREATE POLICY opportunity_view_insert ON crm.opportunity_saved_view FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND owner_user_ref_id=common.current_user_ref_id()
 AND deleted_at IS NULL AND security.authorization_has('opportunity.read'));
CREATE POLICY opportunity_view_update ON crm.opportunity_saved_view FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND owner_user_ref_id=common.current_user_ref_id()
 AND deleted_at IS NULL AND security.authorization_has('opportunity.read'))
 WITH CHECK(workspace_id=common.current_workspace_id() AND owner_user_ref_id=common.current_user_ref_id()
 AND security.authorization_has('opportunity.read'));

CREATE FUNCTION crm.protect_opportunity_saved_view() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF (NEW.id,NEW.workspace_id,NEW.owner_user_ref_id,NEW.created_at)
  IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.owner_user_ref_id,OLD.created_at)
 THEN RAISE EXCEPTION '视图所属公司和创建人不可修改' USING ERRCODE='22023'; END IF;
 IF OLD.deleted_at IS NOT NULL THEN RAISE EXCEPTION '视图已删除' USING ERRCODE='22023'; END IF;
 NEW.updated_at=clock_timestamp(); NEW.version_no=OLD.version_no+1; RETURN NEW;
END $$;
CREATE TRIGGER opportunity_saved_view_version BEFORE UPDATE ON crm.opportunity_saved_view
 FOR EACH ROW EXECUTE FUNCTION crm.protect_opportunity_saved_view();
CREATE TRIGGER opportunity_saved_view_audit AFTER INSERT OR UPDATE ON crm.opportunity_saved_view
 FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row();
REVOKE ALL ON crm.opportunity_saved_view FROM PUBLIC,salegent_feishu_worker;
REVOKE ALL ON FUNCTION crm.protect_opportunity_saved_view() FROM PUBLIC,salegent_feishu_worker;

-- Follow V160 runtime reconciliation so future NOSUPERUSER/NOBYPASSRLS
-- application roles get the same narrowly scoped ACL as existing roles.
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v160;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
 LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r record; reconciled integer;
BEGIN
 reconciled:=security.reconcile_runtime_grants_v160();
 FOR r IN SELECT DISTINCT role.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles role ON role.oid=a.grantee
  WHERE n.nspname='platform' AND c.relname='user_ref'
   AND a.privilege_type='SELECT' AND a.grantee<>c.relowner
 LOOP
  EXECUTE format('REVOKE ALL ON crm.opportunity_saved_view FROM %I',r.rolname);
  EXECUTE format('GRANT SELECT,INSERT,UPDATE ON crm.opportunity_saved_view TO %I',r.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION crm.protect_opportunity_saved_view() FROM %I',r.rolname);
 END LOOP;
 REVOKE ALL ON crm.opportunity_saved_view FROM salegent_feishu_worker;
 REVOKE ALL ON FUNCTION crm.protect_opportunity_saved_view() FROM salegent_feishu_worker;
 RETURN reconciled;
END $$;
-- Preserve the maintenance function's owner and exact ACL across upgrades.
DO $$ DECLARE permission record; function_owner name; BEGIN
 SELECT r.rolname INTO function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
  WHERE p.oid='security.reconcile_runtime_grants_v160()'::regprocedure;
 FOR permission IN SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid='security.reconcile_runtime_grants()'::regprocedure
 LOOP EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants() FROM %s CASCADE',
  CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END); END LOOP;
 EXECUTE format('ALTER FUNCTION security.reconcile_runtime_grants() OWNER TO %I',function_owner);
 FOR permission IN SELECT a.grantee,a.is_grantable,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='security.reconcile_runtime_grants_v160()'::regprocedure AND a.privilege_type='EXECUTE'
 LOOP EXECUTE format('GRANT EXECUTE ON FUNCTION security.reconcile_runtime_grants() TO %s%s',
  CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END,
  CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END); END LOOP;
END $$;
SELECT security.reconcile_runtime_grants();
COMMENT ON TABLE crm.opportunity_saved_view IS '神码个人商机视图；仅保存列/筛选/排序/分组配置，不授予业务权限，不支持团队共享';
INSERT INTO ops.schema_migration(version,description) VALUES('V161','Personal opportunity saved views with owner-only RLS');
COMMIT;
