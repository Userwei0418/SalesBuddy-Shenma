-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V216__visit_upload_intents.sql.
-- Original SHA-256: f620a8f5d939fd584468e4835bdfbb179fa2632a65bb5ae1e7da35f613a50531. See shenma_whole_121_manifest.json.
BEGIN;
SET LOCAL check_function_bodies=on;

-- The API has no direct table grants on tenant quota. Expose only the lock for
-- its currently authorized company, preserving the existing reservation API.
CREATE FUNCTION security.lock_visit_upload_quota() RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id();
BEGIN
 IF ws IS NULL OR common.current_user_ref_id() IS NULL OR NOT security.workspace_is_active(ws)
  OR NOT security.authorization_has('visit.upload') THEN RAISE insufficient_privilege; END IF;
 PERFORM 1 FROM control.tenant_storage_quota WHERE workspace_id=ws FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'UPLOAD_QUOTA_UNAVAILABLE' USING ERRCODE='23514'; END IF;
END $$;
REVOKE ALL ON FUNCTION security.lock_visit_upload_quota() FROM PUBLIC,salegent_feishu_worker;

-- The fingerprint belongs to one explicit intent, never a global content index.
CREATE TABLE activity.visit_upload_intent (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 actor_id uuid NOT NULL,
 request_key uuid NOT NULL,
 filename text NOT NULL CHECK(length(filename) BETWEEN 1 AND 200),
 file_size bigint NOT NULL CHECK(file_size BETWEEN 1 AND 104857600),
 content_sha256 text NOT NULL CHECK(content_sha256 ~ '^[a-f0-9]{64}$'),
 fingerprint_version integer NOT NULL DEFAULT 1 CHECK(fingerprint_version=1),
 current_attempt_id uuid NOT NULL,
 state text NOT NULL DEFAULT 'processing' CHECK(state IN ('processing','uncertain','retryable_failure','registered')),
 registered_import_id uuid,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(workspace_id,actor_id,request_key),
 UNIQUE(id,workspace_id,actor_id),
 CHECK((state='registered')=(registered_import_id IS NOT NULL)),
 CHECK(registered_import_id IS NULL OR registered_import_id=current_attempt_id),
 FOREIGN KEY(actor_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(registered_import_id,workspace_id) REFERENCES activity.visit_import(id,workspace_id)
);
CREATE TABLE activity.visit_upload_attempt (
 id uuid PRIMARY KEY,
 intent_id uuid NOT NULL,
 workspace_id uuid NOT NULL,
 actor_id uuid NOT NULL,
 quota_receipt uuid NOT NULL,
 staging_path text NOT NULL CHECK(length(staging_path) BETWEEN 1 AND 2000),
 storage_driver text NOT NULL CHECK(storage_driver IN ('local','postgres','s3')),
 storage_profile text NOT NULL CHECK(length(storage_profile) BETWEEN 1 AND 200),
 storage_key text NOT NULL CHECK(length(storage_key) BETWEEN 1 AND 2000),
 state text NOT NULL DEFAULT 'processing' CHECK(state IN ('processing','uncertain','retryable_failure','registered')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 finished_at timestamptz,
 UNIQUE(id,intent_id),
 FOREIGN KEY(intent_id,workspace_id,actor_id) REFERENCES activity.visit_upload_intent(id,workspace_id,actor_id)
);
ALTER TABLE activity.visit_upload_intent ADD CONSTRAINT visit_upload_current_attempt
 FOREIGN KEY(current_attempt_id,id) REFERENCES activity.visit_upload_attempt(id,intent_id) DEFERRABLE INITIALLY DEFERRED;

-- The owner maintenance path can distinguish new intentions from historical
-- allocations without impersonating inactive historical uploaders.
ALTER TABLE control.storage_allocation ADD COLUMN upload_intent_id uuid
 REFERENCES activity.visit_upload_intent(id);
CREATE FUNCTION control.bind_visit_upload_allocation() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_attempt record; v_intent record;
BEGIN
 SELECT * INTO v_attempt FROM activity.visit_upload_attempt WHERE id=NEW.import_id;
 NEW.upload_intent_id:=NULL;
 IF NOT FOUND THEN RETURN NEW; END IF;
 SELECT * INTO v_intent FROM activity.visit_upload_intent WHERE id=v_attempt.intent_id FOR UPDATE;
 IF v_intent.current_attempt_id<>NEW.import_id OR v_intent.state<>'processing'
  OR v_attempt.state<>'processing' OR v_attempt.workspace_id<>NEW.workspace_id OR v_attempt.actor_id<>NEW.actor_id
  OR v_attempt.quota_receipt<>NEW.receipt OR v_attempt.staging_path IS DISTINCT FROM NEW.staging_path
  OR v_attempt.storage_driver<>NEW.driver OR v_attempt.storage_profile<>NEW.profile
  OR v_attempt.storage_key<>NEW.storage_key OR v_intent.file_size<>NEW.size_bytes
  OR v_intent.filename<>NEW.filename OR v_intent.content_sha256 IS DISTINCT FROM NEW.content_sha256
 THEN RAISE EXCEPTION 'UPLOAD_ALLOCATION_MISMATCH' USING ERRCODE='23514'; END IF;
 NEW.upload_intent_id:=v_intent.id;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.bind_visit_upload_allocation() FROM PUBLIC;
CREATE TRIGGER bind_visit_upload_allocation BEFORE INSERT ON control.storage_allocation
 FOR EACH ROW EXECUTE FUNCTION control.bind_visit_upload_allocation();

CREATE FUNCTION activity.check_visit_upload_allocation() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM control.storage_allocation a WHERE a.import_id=NEW.id
   AND a.upload_intent_id=NEW.intent_id AND a.receipt=NEW.quota_receipt
   AND a.workspace_id=NEW.workspace_id AND a.actor_id=NEW.actor_id)
 THEN RAISE EXCEPTION 'UPLOAD_ATTEMPT_REQUIRES_ALLOCATION' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION activity.check_visit_upload_allocation() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER visit_upload_allocation_required AFTER INSERT ON activity.visit_upload_attempt
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION activity.check_visit_upload_allocation();

ALTER TABLE activity.visit_upload_intent ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.visit_upload_intent FORCE ROW LEVEL SECURITY;
ALTER TABLE activity.visit_upload_attempt ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.visit_upload_attempt FORCE ROW LEVEL SECURITY;
CREATE POLICY active_tenant_guard ON activity.visit_upload_intent AS RESTRICTIVE FOR ALL
 USING(workspace_id=common.current_workspace_id() AND security.workspace_is_active(common.current_workspace_id()))
 WITH CHECK(workspace_id=common.current_workspace_id() AND security.workspace_is_active(common.current_workspace_id()));
CREATE POLICY active_tenant_guard ON activity.visit_upload_attempt AS RESTRICTIVE FOR ALL
 USING(workspace_id=common.current_workspace_id() AND security.workspace_is_active(common.current_workspace_id()))
 WITH CHECK(workspace_id=common.current_workspace_id() AND security.workspace_is_active(common.current_workspace_id()));
CREATE POLICY visit_upload_intent_owner ON activity.visit_upload_intent
 USING(workspace_id=common.current_workspace_id() AND actor_id=common.current_user_ref_id()
       AND security.authorization_has('visit.upload'))
 WITH CHECK(workspace_id=common.current_workspace_id() AND actor_id=common.current_user_ref_id()
       AND security.authorization_has('visit.upload'));
CREATE POLICY visit_upload_attempt_owner ON activity.visit_upload_attempt
 USING(workspace_id=common.current_workspace_id() AND actor_id=common.current_user_ref_id()
       AND security.authorization_has('visit.upload'))
 WITH CHECK(workspace_id=common.current_workspace_id() AND actor_id=common.current_user_ref_id()
       AND security.authorization_has('visit.upload'));

CREATE FUNCTION activity.guard_visit_upload_intent() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.state<>'processing' OR NEW.registered_import_id IS NOT NULL
  THEN RAISE EXCEPTION 'UPLOAD_INTENT_INITIAL_STATE' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF (NEW.id,NEW.workspace_id,NEW.actor_id,NEW.request_key,NEW.filename,NEW.file_size,
     NEW.content_sha256,NEW.fingerprint_version,NEW.created_at)
 IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.actor_id,OLD.request_key,OLD.filename,OLD.file_size,
     OLD.content_sha256,OLD.fingerprint_version,OLD.created_at)
 THEN RAISE EXCEPTION 'UPLOAD_INTENT_IMMUTABLE' USING ERRCODE='23514'; END IF;
 IF OLD.state='registered' THEN RAISE EXCEPTION 'UPLOAD_INTENT_REGISTERED' USING ERRCODE='23514'; END IF;
 IF NEW.current_attempt_id IS DISTINCT FROM OLD.current_attempt_id
  AND (OLD.state<>'retryable_failure' OR NEW.state<>'processing')
 THEN RAISE EXCEPTION 'UPLOAD_ATTEMPT_NOT_RETRYABLE' USING ERRCODE='23514'; END IF;
 IF NEW.current_attempt_id=OLD.current_attempt_id AND OLD.state='retryable_failure'
 THEN RAISE EXCEPTION 'UPLOAD_ATTEMPT_FINISHED' USING ERRCODE='23514'; END IF;
 IF NEW.state='registered' AND NOT EXISTS(
  SELECT 1 FROM activity.visit_import v WHERE v.id=NEW.registered_import_id AND v.workspace_id=NEW.workspace_id
   AND v.created_by_user_ref_id=NEW.actor_id AND v.filename=NEW.filename AND v.file_size=NEW.file_size
   AND v.content_sha256=NEW.content_sha256)
 THEN RAISE EXCEPTION 'UPLOAD_REGISTRATION_MISMATCH' USING ERRCODE='23514'; END IF;
 IF NEW.state='retryable_failure' AND (
  EXISTS(SELECT 1 FROM activity.visit_import v WHERE v.id=NEW.current_attempt_id)
  OR EXISTS(SELECT 1 FROM control.storage_allocation a WHERE a.import_id=NEW.current_attempt_id AND a.state<>'released'))
 THEN RAISE EXCEPTION 'UPLOAD_CLEANUP_UNCONFIRMED' USING ERRCODE='23514'; END IF;
 NEW.updated_at:=clock_timestamp();
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION activity.guard_visit_upload_intent() FROM PUBLIC;
CREATE TRIGGER visit_upload_intent_guard BEFORE INSERT OR UPDATE ON activity.visit_upload_intent
 FOR EACH ROW EXECUTE FUNCTION activity.guard_visit_upload_intent();

CREATE FUNCTION activity.guard_visit_upload_attempt() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.state<>'processing' THEN RAISE EXCEPTION 'UPLOAD_ATTEMPT_INITIAL_STATE' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF (NEW.id,NEW.intent_id,NEW.workspace_id,NEW.actor_id,NEW.quota_receipt,NEW.staging_path,
     NEW.storage_driver,NEW.storage_profile,NEW.storage_key,NEW.created_at)
 IS DISTINCT FROM (OLD.id,OLD.intent_id,OLD.workspace_id,OLD.actor_id,OLD.quota_receipt,OLD.staging_path,
     OLD.storage_driver,OLD.storage_profile,OLD.storage_key,OLD.created_at)
 OR OLD.state IN ('registered','retryable_failure')
 THEN RAISE EXCEPTION 'UPLOAD_ATTEMPT_IMMUTABLE' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION activity.guard_visit_upload_attempt() FROM PUBLIC;
CREATE TRIGGER visit_upload_attempt_guard BEFORE INSERT OR UPDATE ON activity.visit_upload_attempt
 FOR EACH ROW EXECUTE FUNCTION activity.guard_visit_upload_attempt();

REVOKE ALL ON activity.visit_upload_intent,activity.visit_upload_attempt FROM PUBLIC,salegent_feishu_worker;
DO $$ DECLARE v_role record; BEGIN
 FOR v_role IN SELECT DISTINCT g.grantee FROM information_schema.role_table_grants g
 JOIN pg_roles r ON r.rolname=g.grantee
 WHERE g.table_schema='activity' AND g.table_name='visit_import' AND g.privilege_type='INSERT'
 AND NOT pg_has_role(r.oid,'salegent_feishu_worker','member') LOOP
  EXECUTE format('GRANT SELECT,INSERT,UPDATE ON activity.visit_upload_intent,activity.visit_upload_attempt TO %I',v_role.grantee);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.lock_visit_upload_quota() TO %I',v_role.grantee);
 END LOOP;
END $$;
COMMENT ON TABLE activity.visit_upload_intent IS '本人上传意图查回；相同key不自动过期、不删除、不跨账号hash检索';
COMMENT ON TABLE activity.visit_upload_attempt IS '上传尝试与原件/配额引用；不确定状态不允许自动重建';
INSERT INTO ops.schema_migration(version,description) VALUES('V364','Durable owner-scoped upload intentions and recovery evidence');
COMMIT;
