-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V162__tenant_original_storage_quota.sql.
-- Original SHA-256: 0a725c23579609d8c9c1b68316481cfd011a870089412e7fd965f9f12188c18d. See shenma_whole_121_manifest.json.
BEGIN;
LOCK TABLE activity.visit_import IN SHARE ROW EXCLUSIVE MODE;
CREATE TABLE control.tenant_storage_quota(
 workspace_id uuid PRIMARY KEY REFERENCES platform.workspace(id),
 limit_bytes bigint CHECK(limit_bytes BETWEEN 0 AND 9007199254740991),
 stored_bytes bigint NOT NULL DEFAULT 0 CHECK(stored_bytes>=0),
 reserved_bytes bigint NOT NULL DEFAULT 0 CHECK(reserved_bytes>=0)
);
CREATE TABLE control.storage_allocation(
 import_id uuid PRIMARY KEY,
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 actor_id uuid NOT NULL,
 actor_role text,
 receipt uuid NOT NULL UNIQUE,
 size_bytes bigint NOT NULL CHECK(size_bytes>=0),
 state text NOT NULL CHECK(state IN ('reserved','external_pending','discarding','stored','released')),
 driver text NOT NULL CHECK(driver IN ('postgres','local','s3')),
 profile text NOT NULL,
 storage_key text NOT NULL,
 filename text NOT NULL,
 content_sha256 text,
 staging_path text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX storage_allocation_pending_idx ON control.storage_allocation(workspace_id,created_at)
 WHERE state IN ('reserved','external_pending','discarding');
INSERT INTO control.tenant_storage_quota(workspace_id,stored_bytes)
 SELECT w.id,COALESCE(sum(i.file_size),0) FROM platform.workspace w LEFT JOIN activity.visit_import i
 ON i.workspace_id=w.id GROUP BY w.id;
INSERT INTO control.storage_allocation(import_id,workspace_id,actor_id,receipt,size_bytes,state,driver,profile,storage_key,filename,content_sha256)
 SELECT id,workspace_id,created_by_user_ref_id,gen_random_uuid(),file_size,'stored',storage_driver,storage_profile,storage_key,filename,content_sha256
 FROM activity.visit_import;
CREATE FUNCTION control.initialize_storage_quota() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 INSERT INTO control.tenant_storage_quota(workspace_id) VALUES(NEW.id);
 RETURN NEW;
END $$;
CREATE TRIGGER initialize_storage_quota AFTER INSERT ON platform.workspace
 FOR EACH ROW EXECUTE FUNCTION control.initialize_storage_quota();

CREATE FUNCTION security.reserve_visit_storage(p_import uuid,p_receipt uuid,p_size bigint,p_driver text,p_profile text,p_key text,p_sha text,p_staging text,p_filename text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id(); actor uuid:=common.current_user_ref_id(); prior control.storage_allocation;
BEGIN
 IF ws IS NULL OR actor IS NULL OR NOT security.workspace_is_active(ws) OR NOT security.authorization_has('visit.upload') THEN
  RAISE EXCEPTION 'STORAGE_RESERVATION_DENIED' USING ERRCODE='42501'; END IF;
 IF p_import IS NULL OR p_receipt IS NULL OR p_size IS NULL OR p_size NOT BETWEEN 1 AND 104857600
  OR p_driver IS NULL OR p_driver NOT IN ('postgres','local','s3')
  OR COALESCE(length(p_profile),0) NOT BETWEEN 1 AND 200 OR COALESCE(length(p_key),0) NOT BETWEEN 1 AND 2000
  OR COALESCE(length(p_staging),0) NOT BETWEEN 1 AND 2000 OR p_sha IS NULL OR p_sha!~'^[a-f0-9]{64}$' OR COALESCE(length(p_filename),0) NOT BETWEEN 1 AND 200 THEN
  RAISE EXCEPTION 'INVALID_STORAGE_RESERVATION' USING ERRCODE='P7422'; END IF;
 -- All writers take quota then allocation locks. Never hold this transaction
 -- across filesystem or external object-store calls.
 PERFORM 1 FROM control.tenant_storage_quota WHERE workspace_id=ws FOR UPDATE;
 SELECT * INTO prior FROM control.storage_allocation WHERE import_id=p_import;
 IF FOUND THEN
  IF prior.workspace_id<>ws OR prior.actor_id<>actor OR prior.receipt<>p_receipt OR prior.size_bytes<>p_size
   OR prior.driver<>p_driver OR prior.profile<>p_profile OR prior.storage_key<>p_key
   OR prior.filename<>p_filename OR prior.content_sha256 IS DISTINCT FROM p_sha OR prior.staging_path IS DISTINCT FROM p_staging OR prior.state='released' THEN
   RAISE EXCEPTION 'STORAGE_RESERVATION_CONFLICT' USING ERRCODE='P7409'; END IF;
  RETURN prior.state;
 END IF;
 UPDATE control.tenant_storage_quota SET reserved_bytes=reserved_bytes+p_size
 WHERE workspace_id=ws AND (limit_bytes IS NULL OR stored_bytes+reserved_bytes+p_size<=limit_bytes);
 IF NOT FOUND THEN RAISE EXCEPTION 'TENANT_STORAGE_QUOTA_EXCEEDED' USING ERRCODE='P7430'; END IF;
 INSERT INTO control.storage_allocation(import_id,workspace_id,actor_id,receipt,size_bytes,state,driver,profile,storage_key,filename,content_sha256,staging_path,actor_role)
 VALUES(p_import,ws,actor,p_receipt,p_size,'reserved',p_driver,p_profile,p_key,p_filename,p_sha,p_staging,NULLIF(current_setting('app.role_code',true),''));
 RETURN 'reserved';
END $$;

CREATE FUNCTION security.mark_storage_external(p_import uuid,p_receipt uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE allocation control.storage_allocation; ws uuid:=common.current_workspace_id();
BEGIN
 PERFORM 1 FROM control.tenant_storage_quota WHERE workspace_id=ws FOR UPDATE;
 SELECT * INTO allocation FROM control.storage_allocation WHERE import_id=p_import AND workspace_id=ws FOR UPDATE;
 IF NOT FOUND OR p_receipt IS NULL OR common.current_user_ref_id() IS NULL OR allocation.receipt<>p_receipt OR allocation.actor_id<>common.current_user_ref_id()
  OR allocation.driver<>'s3' OR allocation.state NOT IN ('reserved','external_pending')
  OR NOT security.authorization_has('visit.upload') OR NOT security.workspace_is_active(ws) THEN
  RAISE EXCEPTION 'STORAGE_RESERVATION_DENIED' USING ERRCODE='42501'; END IF;
 UPDATE control.storage_allocation SET state='external_pending',updated_at=clock_timestamp() WHERE import_id=p_import;
 RETURN true;
END $$;

CREATE FUNCTION security.fence_unpublished_storage(p_import uuid,p_receipt uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE allocation control.storage_allocation; ws uuid:=common.current_workspace_id();
BEGIN
 PERFORM 1 FROM control.tenant_storage_quota WHERE workspace_id=ws FOR UPDATE;
 SELECT * INTO allocation FROM control.storage_allocation WHERE import_id=p_import AND workspace_id=ws FOR UPDATE;
 IF NOT FOUND OR p_receipt IS NULL OR common.current_user_ref_id() IS NULL OR allocation.receipt<>p_receipt
  OR allocation.actor_id<>common.current_user_ref_id() THEN RAISE insufficient_privilege; END IF;
 IF allocation.state NOT IN ('reserved','discarding') OR EXISTS(SELECT 1 FROM activity.visit_import WHERE id=p_import) THEN RETURN false; END IF;
 UPDATE control.storage_allocation SET state='discarding',updated_at=clock_timestamp() WHERE import_id=p_import;
 RETURN true;
END $$;

CREATE FUNCTION security.release_unpublished_storage(p_import uuid,p_receipt uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE allocation control.storage_allocation; ws uuid:=common.current_workspace_id();
BEGIN
 PERFORM 1 FROM control.tenant_storage_quota WHERE workspace_id=ws FOR UPDATE;
 SELECT * INTO allocation FROM control.storage_allocation WHERE import_id=p_import AND workspace_id=ws FOR UPDATE;
 IF NOT FOUND OR p_receipt IS NULL OR common.current_user_ref_id() IS NULL OR allocation.receipt<>p_receipt OR allocation.actor_id<>common.current_user_ref_id() THEN
  RAISE EXCEPTION 'STORAGE_RESERVATION_DENIED' USING ERRCODE='42501'; END IF;
 IF allocation.state='released' THEN RETURN true; END IF;
 IF allocation.state<>'discarding' OR EXISTS(SELECT 1 FROM activity.visit_import WHERE id=p_import) THEN RETURN false; END IF;
 UPDATE control.tenant_storage_quota SET reserved_bytes=reserved_bytes-allocation.size_bytes WHERE workspace_id=ws;
 UPDATE control.storage_allocation SET state='released',updated_at=clock_timestamp() WHERE import_id=p_import;
 RETURN true;
END $$;

CREATE FUNCTION control.track_original_storage() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE allocation control.storage_allocation; ws uuid;
BEGIN
 IF TG_OP='TRUNCATE' THEN RAISE EXCEPTION 'ORIGINAL_TRUNCATE_REQUIRES_RECONCILIATION' USING ERRCODE='P7422'; END IF;
 ws:=CASE WHEN TG_OP='DELETE' THEN OLD.workspace_id ELSE NEW.workspace_id END;
 IF TG_OP='UPDATE' AND (NEW.workspace_id<>OLD.workspace_id OR NEW.id<>OLD.id OR NEW.file_size<>OLD.file_size) THEN
  RAISE EXCEPTION 'ORIGINAL_IDENTITY_AND_SIZE_IMMUTABLE' USING ERRCODE='P7422'; END IF;
 PERFORM 1 FROM control.tenant_storage_quota WHERE workspace_id=ws FOR UPDATE;
 SELECT * INTO allocation FROM control.storage_allocation WHERE import_id=CASE WHEN TG_OP='DELETE' THEN OLD.id ELSE NEW.id END FOR UPDATE;
 IF TG_OP='INSERT' THEN
  IF NOT FOUND THEN
   -- Maintenance imports and legacy callers still consume capacity, even when
   -- they do not use the external-upload reservation protocol.
   UPDATE control.tenant_storage_quota SET stored_bytes=stored_bytes+NEW.file_size WHERE workspace_id=ws
    AND (limit_bytes IS NULL OR stored_bytes+reserved_bytes+NEW.file_size<=limit_bytes);
   IF NOT FOUND THEN RAISE EXCEPTION 'TENANT_STORAGE_QUOTA_EXCEEDED' USING ERRCODE='P7430'; END IF;
   INSERT INTO control.storage_allocation(import_id,workspace_id,actor_id,receipt,size_bytes,state,driver,profile,storage_key,filename,content_sha256)
    VALUES(NEW.id,ws,NEW.created_by_user_ref_id,gen_random_uuid(),NEW.file_size,'stored',NEW.storage_driver,NEW.storage_profile,NEW.storage_key,NEW.filename,NEW.content_sha256);
   RETURN NULL;
  END IF;
  IF allocation.workspace_id<>ws OR allocation.actor_id<>NEW.created_by_user_ref_id OR allocation.size_bytes<>NEW.file_size
   OR allocation.driver<>NEW.storage_driver OR allocation.profile<>NEW.storage_profile OR allocation.storage_key<>NEW.storage_key
   OR allocation.content_sha256 IS DISTINCT FROM NEW.content_sha256 OR allocation.state NOT IN ('reserved','external_pending') THEN
   RAISE EXCEPTION 'STORAGE_RESERVATION_CONFLICT' USING ERRCODE='P7409'; END IF;
  UPDATE control.tenant_storage_quota SET reserved_bytes=reserved_bytes-NEW.file_size,stored_bytes=stored_bytes+NEW.file_size WHERE workspace_id=ws;
  UPDATE control.storage_allocation SET state='stored',updated_at=clock_timestamp() WHERE import_id=NEW.id;
 ELSIF TG_OP='UPDATE' THEN
  IF NOT FOUND OR allocation.state<>'stored' OR allocation.workspace_id<>ws OR allocation.size_bytes<>NEW.file_size
   OR allocation.driver<>NEW.storage_driver OR allocation.profile<>NEW.storage_profile OR allocation.storage_key<>NEW.storage_key
   OR allocation.content_sha256 IS DISTINCT FROM NEW.content_sha256 THEN
   RAISE EXCEPTION 'ORIGINAL_LOCATION_IMMUTABLE' USING ERRCODE='P7422'; END IF;
 ELSE
  IF NOT FOUND OR allocation.state<>'stored' THEN RAISE EXCEPTION 'STORAGE_COUNTER_INCONSISTENT' USING ERRCODE='P7500'; END IF;
  -- PostgreSQL chunks cascade in this same transaction. Removing a database
  -- pointer is not proof that a local/S3 original has been physically removed.
  UPDATE control.tenant_storage_quota SET stored_bytes=stored_bytes-OLD.file_size,
   reserved_bytes=reserved_bytes+CASE WHEN allocation.driver='postgres' THEN 0 ELSE OLD.file_size END WHERE workspace_id=ws;
  UPDATE control.storage_allocation SET state=CASE WHEN driver='postgres' THEN 'released' ELSE 'external_pending' END,
   updated_at=clock_timestamp() WHERE import_id=OLD.id;
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER track_original_storage AFTER INSERT OR DELETE OR UPDATE OF id,workspace_id,file_size,storage_driver,storage_profile,storage_key,content_sha256
 ON activity.visit_import FOR EACH ROW EXECUTE FUNCTION control.track_original_storage();
CREATE TRIGGER reject_unaccounted_original_truncate BEFORE TRUNCATE ON activity.visit_import
 FOR EACH STATEMENT EXECUTE FUNCTION control.track_original_storage();

-- Owner-only tools use this narrowly scoped function after copying and hashing
-- restored legacy originals; application/platform roles receive no EXECUTE.
CREATE FUNCTION control.rebind_restored_local_original(p_import uuid,p_path text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid;
BEGIN
 SELECT workspace_id INTO ws FROM control.storage_allocation WHERE import_id=p_import AND state='stored' AND driver='local';
 IF NOT FOUND OR COALESCE(length(p_path),0)=0 THEN RAISE EXCEPTION 'LOCAL_ORIGINAL_REQUIRED'; END IF;
 PERFORM 1 FROM control.tenant_storage_quota WHERE workspace_id=ws FOR UPDATE;
 UPDATE control.storage_allocation SET storage_key=p_path,updated_at=clock_timestamp() WHERE import_id=p_import;
 UPDATE activity.visit_import SET storage_key=p_path,file_path=p_path WHERE id=p_import;
END $$;

CREATE FUNCTION control.storage_quota(p_session text,p_workspace uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
 PERFORM control.require_actor(p_session);
 SELECT jsonb_build_object('workspace_id',w.id,'version_no',w.version_no,'limit_bytes',q.limit_bytes,
  'stored_bytes',q.stored_bytes,'reserved_bytes',q.reserved_bytes,
  'remaining_bytes',CASE WHEN q.limit_bytes IS NULL THEN NULL ELSE greatest(0,q.limit_bytes-q.stored_bytes-q.reserved_bytes) END,
  'pending_count',(SELECT count(*) FROM control.storage_allocation a WHERE a.workspace_id=w.id AND a.state IN ('reserved','external_pending','discarding')),
  'oldest_pending_at',(SELECT min(created_at) FROM control.storage_allocation a WHERE a.workspace_id=w.id AND a.state IN ('reserved','external_pending','discarding')))
 INTO result FROM platform.workspace w JOIN control.tenant_storage_quota q ON q.workspace_id=w.id WHERE w.id=p_workspace AND w.deleted_at IS NULL;
 IF result IS NULL THEN RAISE EXCEPTION 'TENANT_NOT_FOUND' USING ERRCODE='P7404'; END IF;
 RETURN result;
END $$;
CREATE FUNCTION control.save_storage_quota(p_session text,p_workspace uuid,p_version integer,p_limit bigint,p_reason text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=control.require_sensitive(p_session); version integer; before_value jsonb; after_value jsonb;
BEGIN
 SELECT version_no INTO version FROM platform.workspace WHERE id=p_workspace AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'TENANT_NOT_FOUND' USING ERRCODE='P7404'; END IF;
 IF version IS DISTINCT FROM p_version THEN RAISE EXCEPTION 'VERSION_CONFLICT' USING ERRCODE='P7409'; END IF;
 IF (p_limit IS NOT NULL AND p_limit NOT BETWEEN 0 AND 9007199254740991) OR COALESCE(length(btrim(p_reason)),0) NOT BETWEEN 1 AND 500 THEN
  RAISE EXCEPTION 'INVALID_STORAGE_QUOTA' USING ERRCODE='P7422'; END IF;
 PERFORM 1 FROM control.tenant_storage_quota WHERE workspace_id=p_workspace FOR UPDATE;
 before_value:=control.storage_quota(p_session,p_workspace);
 UPDATE control.tenant_storage_quota SET limit_bytes=p_limit WHERE workspace_id=p_workspace;
 UPDATE platform.workspace SET version_no=version_no+1,updated_at=clock_timestamp() WHERE id=p_workspace;
 after_value:=control.storage_quota(p_session,p_workspace);
 INSERT INTO control.audit(operator_id,workspace_id,action,reason,before_state,after_state)
 VALUES(actor,p_workspace,'tenant.storage_quota',btrim(p_reason),before_value,after_value);
 RETURN after_value;
END $$;
REVOKE ALL ON control.tenant_storage_quota,control.storage_allocation FROM PUBLIC,salegent_feishu_worker;
REVOKE ALL ON FUNCTION control.initialize_storage_quota(),control.track_original_storage(),control.rebind_restored_local_original(uuid,text),
 control.storage_quota(text,uuid),control.save_storage_quota(text,uuid,integer,bigint,text),
 security.reserve_visit_storage(uuid,uuid,bigint,text,text,text,text,text,text),security.mark_storage_external(uuid,uuid),security.fence_unpublished_storage(uuid,uuid),security.release_unpublished_storage(uuid,uuid)
 FROM PUBLIC,salegent_feishu_worker;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_internal_v161;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_internal_v161();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants_internal_v161() FROM %I',r.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.reserve_visit_storage(uuid,uuid,bigint,text,text,text,text,text,text),security.mark_storage_external(uuid,uuid),security.fence_unpublished_storage(uuid,uuid),security.release_unpublished_storage(uuid,uuid) TO %I',r.rolname);
 END LOOP;
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'control','USAGE')
 AND has_function_privilege(oid,'control.tenants(text)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION control.storage_quota(text,uuid),control.save_storage_quota(text,uuid,integer,bigint,text) TO %I',r.rolname);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_internal_v161() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V310','Original storage capacity reservations and durable uncertain outcomes');
COMMIT;
