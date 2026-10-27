BEGIN;

-- Shenma-only fixed workspace binding. Preserve employee identities, grants,
-- RLS, session boundaries, and business rows. Credentials are provisioned
-- separately via the existing operator-only registration function.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM platform.workspace WHERE id='10b037a3-0d6a-4d63-ba69-f7ffb33dc474'::uuid
  AND external_workspace_id='shenma-isolated-demo-20260929' AND name='神码演示测试公司（隔离）'
  AND status='active' AND deleted_at IS NULL) THEN
  RAISE EXCEPTION 'SHENMA_READONLY_WORKSPACE_MISMATCH';
 END IF;
 IF EXISTS(SELECT 1 FROM security.demo_readonly_identity WHERE workspace_id<>'10b037a3-0d6a-4d63-ba69-f7ffb33dc474'::uuid) THEN
  RAISE EXCEPTION 'SHENMA_READONLY_OTHER_BINDING_PRESENT';
 END IF;
END $$;

CREATE OR REPLACE FUNCTION security.demo_readonly_workspace() RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT id FROM platform.workspace WHERE id='10b037a3-0d6a-4d63-ba69-f7ffb33dc474'::uuid
 AND external_workspace_id='shenma-isolated-demo-20260929' AND status='active' AND deleted_at IS NULL;
$$;

CREATE OR REPLACE FUNCTION security.demo_readonly_guard_identity() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM platform.workspace WHERE id=NEW.workspace_id AND external_workspace_id='shenma-isolated-demo-20260929' AND id='10b037a3-0d6a-4d63-ba69-f7ffb33dc474'::uuid AND status='active' AND deleted_at IS NULL) THEN
  RAISE EXCEPTION 'DEMO_IDENTITY_COMPANY_INVALID' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.workspace_id=NEW.workspace_id
  AND upper(u.account_code)=NEW.account_code AND u.deleted_at IS NULL) THEN
  RAISE EXCEPTION 'DEMO_IDENTITY_ACCOUNT_CONFLICT' USING ERRCODE='23505';
 END IF;
 IF TG_OP='UPDATE' THEN
  IF NEW.id<>OLD.id OR NEW.workspace_id<>OLD.workspace_id OR NEW.account_code<>OLD.account_code THEN
   RAISE EXCEPTION 'DEMO_IDENTITY_BINDING_IMMUTABLE' USING ERRCODE='23514';
  END IF;
  IF NEW.password_hash IS DISTINCT FROM OLD.password_hash OR NEW.enabled IS DISTINCT FROM OLD.enabled THEN
   NEW.permission_version:=OLD.permission_version+1;
  ELSIF NEW.permission_version<OLD.permission_version THEN
   RAISE EXCEPTION 'DEMO_IDENTITY_VERSION_INVALID' USING ERRCODE='23514';
  END IF;
  IF NEW.permission_version<>OLD.permission_version THEN
   UPDATE security.demo_readonly_session SET revoked_at=clock_timestamp()
    WHERE identity_id=OLD.id AND revoked_at IS NULL;
  END IF;
  NEW.updated_at:=clock_timestamp();
 END IF;
 RETURN NEW;
END $$;

-- CREATE OR REPLACE keeps existing owners and ACLs. No GRANT, role or policy change.
INSERT INTO ops.schema_migration(version,description)
 VALUES('V448','Shenma Mini read-only identity bound to the existing isolated company');
COMMIT;
