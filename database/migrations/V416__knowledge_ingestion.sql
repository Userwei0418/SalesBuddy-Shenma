BEGIN;
CREATE OR REPLACE FUNCTION security.workspace_job_enabled(p_workspace uuid,p_type text) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.workspace_module_enabled(p_workspace,CASE WHEN p_type='visit.import' THEN 'visit'
 WHEN p_type='knowledge.ingest' THEN 'knowledge'
 WHEN p_type IN ('agent.run','sales_competency.review','battle_map.review','customer.risk.review',
 'business.advice','opportunity.change.review','work_report.generate') THEN 'ai' ELSE 'core' END);
$$;
-- Durable exact-version processing. API roles remain SELECT-only on these tables;
-- all mutations recheck current module, object and contributor permissions.
CREATE TABLE knowledge.ingestion (
 workspace_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(),
 material_id uuid NOT NULL, version_id uuid NOT NULL, requested_by uuid NOT NULL,
 provider_origin text NOT NULL, provider_dataset_id text NOT NULL, configuration_fingerprint text NOT NULL,
 acl_revision integer NOT NULL, attempt integer NOT NULL DEFAULT 1,
 job_id uuid NOT NULL, status text NOT NULL CHECK(status IN ('queued','uploading','parsing','succeeded','failed','unknown')),
 error_code text, created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,id), UNIQUE(id), UNIQUE(workspace_id,material_id,version_id),
 FOREIGN KEY(workspace_id,material_id,version_id) REFERENCES knowledge.version(workspace_id,material_id,id)
);
CREATE TABLE knowledge.ingestion_file (
 workspace_id uuid NOT NULL, ingestion_id uuid NOT NULL, file_id uuid NOT NULL,
 source_sha256 text NOT NULL, provider_origin text NOT NULL, provider_dataset_id text NOT NULL,
 provider_document_id text, status text NOT NULL DEFAULT 'pending'
 CHECK(status IN ('pending','uploading','uploaded','parsing','parsed')),
 chunk_count integer CHECK(chunk_count>=0), updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,ingestion_id,file_id),
 FOREIGN KEY(workspace_id,ingestion_id) REFERENCES knowledge.ingestion(workspace_id,id),
 FOREIGN KEY(workspace_id,file_id) REFERENCES knowledge.file(workspace_id,id),
 CHECK(provider_document_id IS NULL OR (provider_document_id ~ '^[A-Za-z0-9_-]+$' AND length(provider_document_id)<=300))
);
-- A provider document cannot be rebound to another company or source version.
CREATE UNIQUE INDEX knowledge_ingestion_document_unique
 ON knowledge.ingestion_file(provider_origin,provider_dataset_id,provider_document_id)
 WHERE provider_document_id IS NOT NULL;
ALTER TABLE knowledge.ingestion ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.ingestion FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge.ingestion_file ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.ingestion_file FORCE ROW LEVEL SECURITY;
REVOKE ALL ON knowledge.ingestion,knowledge.ingestion_file FROM PUBLIC;
CREATE POLICY ingestion_read ON knowledge.ingestion FOR SELECT USING (
 workspace_id=common.current_workspace_id() AND security.knowledge_can(material_id,'read'));
CREATE POLICY ingestion_file_read ON knowledge.ingestion_file FOR SELECT USING (
 workspace_id=common.current_workspace_id() AND EXISTS(SELECT 1 FROM knowledge.ingestion i
 WHERE i.id=ingestion_id AND i.workspace_id=knowledge.ingestion_file.workspace_id));

CREATE FUNCTION security.knowledge_ingestion_allowed(p_material uuid,p_version uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
 SELECT security.knowledge_can(p_material,'read') AND EXISTS(
 SELECT 1 FROM knowledge.version v JOIN knowledge.material m ON m.id=v.material_id AND m.workspace_id=v.workspace_id
 WHERE v.workspace_id=common.current_workspace_id() AND v.material_id=p_material AND v.id=p_version
 AND m.deleted_at IS NULL AND v.state<>'deleted' AND COALESCE(v.contribution->>'kind','')<>'legacy_import'
 AND (security.knowledge_can(p_material,'edit') OR (v.version_no=1 AND v.contributor_user_id=common.current_user_ref_id()
 AND security.knowledge_permission('knowledge.create',v.contributor_user_id,
 security.knowledge_current_teams(v.contributor_user_id),false))));
$$;

CREATE FUNCTION security.knowledge_ingestion_start(p_material uuid,p_version uuid,p_revision integer,
 p_origin text,p_dataset text,p_fingerprint text,p_actor jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE m knowledge.material; v knowledge.version; item knowledge.ingestion; new_job uuid:=gen_random_uuid(); result_id uuid;
BEGIN
 IF p_origin IS NULL OR p_origin !~ '^https?://[A-Za-z0-9.:-]+$' OR length(p_origin)>500
 OR p_dataset IS NULL OR (p_dataset !~ '^[A-Za-z0-9_-]+$' OR length(p_dataset)>300)
 OR p_fingerprint IS NULL OR p_fingerprint !~ '^[a-f0-9]{64}$'
 OR jsonb_typeof(p_actor)<>'object' OR p_actor->>'workspace_id' IS DISTINCT FROM common.current_workspace_id()::text
 OR p_actor->>'user_id' IS DISTINCT FROM common.current_user_ref_id()::text
 OR p_actor->>'role' IS DISTINCT FROM common.current_role_code() THEN
 RAISE EXCEPTION 'KNOWLEDGE_INGESTION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 SELECT * INTO m FROM knowledge.material WHERE workspace_id=common.current_workspace_id() AND id=p_material FOR UPDATE;
 IF NOT FOUND OR NOT security.knowledge_ingestion_allowed(p_material,p_version) THEN
 RAISE EXCEPTION 'KNOWLEDGE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF p_revision IS DISTINCT FROM m.revision OR m.latest_version_id IS DISTINCT FROM p_version THEN
 RAISE EXCEPTION 'KNOWLEDGE_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
 SELECT * INTO v FROM knowledge.version WHERE workspace_id=m.workspace_id AND id=p_version;
 IF jsonb_array_length(COALESCE(v.contribution->'selection'->'content'->'links','[]'))>0 THEN
 RAISE EXCEPTION 'KNOWLEDGE_INGESTION_LINKS_NOT_ARCHIVED' USING ERRCODE='22023'; END IF;
 IF NOT EXISTS(SELECT 1 FROM knowledge.version_file WHERE workspace_id=m.workspace_id AND version_id=p_version) THEN
 RAISE EXCEPTION 'KNOWLEDGE_INGESTION_FILES_REQUIRED' USING ERRCODE='22023'; END IF;
 SELECT * INTO item FROM knowledge.ingestion WHERE workspace_id=m.workspace_id AND material_id=p_material AND version_id=p_version FOR UPDATE;
 IF FOUND THEN
  IF item.provider_origin<>p_origin OR item.provider_dataset_id<>p_dataset OR item.configuration_fingerprint<>p_fingerprint THEN
   RAISE EXCEPTION 'KNOWLEDGE_INGESTION_CONFIGURATION_CHANGED' USING ERRCODE='40001'; END IF;
  IF item.status='succeeded' THEN RETURN item.id; END IF;
  IF item.status='unknown' OR EXISTS(SELECT 1 FROM knowledge.ingestion_file WHERE ingestion_id=item.id AND status='uploading') THEN
   RAISE EXCEPTION 'KNOWLEDGE_INGESTION_UPLOAD_UNKNOWN' USING ERRCODE='40001'; END IF;
  IF EXISTS(SELECT 1 FROM ops.job WHERE id=item.job_id AND status IN ('queued','running','failed')) THEN RETURN item.id; END IF;
  result_id:=item.id;
  UPDATE knowledge.ingestion SET job_id=new_job,requested_by=common.current_user_ref_id(),attempt=attempt+1,
    status='queued',error_code=NULL,acl_revision=(SELECT revision FROM knowledge.material_acl WHERE material_id=p_material),
    updated_at=clock_timestamp() WHERE id=result_id;
 ELSE
  INSERT INTO knowledge.ingestion(workspace_id,material_id,version_id,requested_by,provider_origin,provider_dataset_id,
    configuration_fingerprint,acl_revision,job_id,status)
  VALUES(m.workspace_id,p_material,p_version,common.current_user_ref_id(),p_origin,p_dataset,p_fingerprint,
   (SELECT revision FROM knowledge.material_acl WHERE material_id=p_material),new_job,'queued') RETURNING id INTO result_id;
  INSERT INTO knowledge.ingestion_file(workspace_id,ingestion_id,file_id,source_sha256,provider_origin,provider_dataset_id)
   SELECT m.workspace_id,result_id,f.id,f.sha256,p_origin,p_dataset FROM knowledge.version_file vf
   JOIN knowledge.file f ON f.id=vf.file_id AND f.workspace_id=vf.workspace_id
   WHERE vf.workspace_id=m.workspace_id AND vf.version_id=p_version;
 END IF;
 UPDATE knowledge.version SET state='syncing' WHERE id=p_version AND state<>'retrievable';
 INSERT INTO ops.job(id,workspace_id,job_type,aggregate_type,aggregate_id,payload,max_attempts,priority)
 VALUES(new_job,m.workspace_id,'knowledge.ingest','knowledge_ingestion',result_id,p_actor,1,30);
 INSERT INTO knowledge.audit(workspace_id,actor_id,material_id,operation,detail)
 VALUES(m.workspace_id,common.current_user_ref_id(),p_material,'ingestion.request',jsonb_build_object('ingestion_id',result_id,'version_id',p_version,'job_id',new_job));
 RETURN result_id;
END $$;

CREATE FUNCTION security.knowledge_ingestion_step(p_id uuid,p_action text,p_file uuid DEFAULT NULL,
 p_document text DEFAULT NULL,p_chunks integer DEFAULT NULL,p_error text DEFAULT NULL) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE i knowledge.ingestion; m knowledge.material; f knowledge.ingestion_file;
BEGIN
 SELECT * INTO i FROM knowledge.ingestion WHERE workspace_id=common.current_workspace_id() AND id=p_id FOR UPDATE;
 IF NOT FOUND OR i.requested_by<>common.current_user_ref_id()
 OR current_setting('app.job_id',true) IS DISTINCT FROM i.job_id::text
 OR NOT EXISTS(SELECT 1 FROM ops.job WHERE id=i.job_id AND workspace_id=i.workspace_id AND aggregate_id=i.id
   AND job_type='knowledge.ingest' AND status='running' AND locked_until>clock_timestamp()) THEN
  RAISE EXCEPTION 'KNOWLEDGE_INGESTION_LEASE_INVALID' USING ERRCODE='42501'; END IF;
 IF p_action='failed' THEN
  IF p_error IS NULL OR p_error !~ '^KNOWLEDGE_[A-Z0-9_]{1,90}$' THEN
   RAISE EXCEPTION 'KNOWLEDGE_INGESTION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  UPDATE knowledge.ingestion SET status=CASE WHEN EXISTS(SELECT 1 FROM knowledge.ingestion_file
    WHERE ingestion_id=i.id AND status='uploading') THEN 'unknown' ELSE 'failed' END,
    error_code=p_error,updated_at=clock_timestamp() WHERE id=i.id AND status<>'succeeded';
  UPDATE knowledge.version SET state='failed' WHERE id=i.version_id AND state NOT IN ('retrievable','deleted');
  RETURN;
 END IF;
 IF NOT security.knowledge_ingestion_allowed(i.material_id,i.version_id)
 OR i.acl_revision IS DISTINCT FROM (SELECT revision FROM knowledge.material_acl WHERE material_id=i.material_id) THEN
  RAISE EXCEPTION 'KNOWLEDGE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO m FROM knowledge.material WHERE id=i.material_id AND workspace_id=i.workspace_id FOR UPDATE;
 IF m.latest_version_id IS DISTINCT FROM i.version_id OR m.deleted_at IS NOT NULL THEN
  RAISE EXCEPTION 'KNOWLEDGE_INGESTION_VERSION_STALE' USING ERRCODE='40001'; END IF;
 IF EXISTS(SELECT 1 FROM knowledge.ingestion_file s JOIN knowledge.file a ON a.id=s.file_id
 WHERE s.ingestion_id=i.id AND s.source_sha256<>a.sha256) THEN
  RAISE EXCEPTION 'KNOWLEDGE_INGESTION_SOURCE_CHANGED' USING ERRCODE='40001'; END IF;
 IF p_action='validate' THEN RETURN; END IF;
 IF p_action='activate' THEN
  IF NOT EXISTS(SELECT 1 FROM knowledge.ingestion_file WHERE ingestion_id=i.id)
   OR EXISTS(SELECT 1 FROM knowledge.ingestion_file WHERE ingestion_id=i.id AND
      (status<>'parsed' OR provider_document_id IS NULL OR chunk_count IS NULL OR chunk_count<1)) THEN
   RAISE EXCEPTION 'KNOWLEDGE_INGESTION_INCOMPLETE' USING ERRCODE='22023'; END IF;
  UPDATE knowledge.version SET state='retrievable' WHERE id=i.version_id;
  UPDATE knowledge.material SET active_version_id=i.version_id,updated_at=clock_timestamp() WHERE id=i.material_id;
  UPDATE knowledge.ingestion SET status='succeeded',error_code=NULL,updated_at=clock_timestamp() WHERE id=i.id;
  INSERT INTO knowledge.audit(workspace_id,actor_id,material_id,operation,detail)
  VALUES(i.workspace_id,common.current_user_ref_id(),i.material_id,'ingestion.activated',
    jsonb_build_object('ingestion_id',i.id,'version_id',i.version_id));
  RETURN;
 END IF;
 SELECT * INTO f FROM knowledge.ingestion_file WHERE ingestion_id=i.id AND file_id=p_file FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'KNOWLEDGE_INGESTION_FILE_INVALID' USING ERRCODE='22023'; END IF;
 IF p_action='upload_started' AND f.status='pending' THEN
  UPDATE knowledge.ingestion_file SET status='uploading',updated_at=clock_timestamp() WHERE ingestion_id=i.id AND file_id=p_file;
  UPDATE knowledge.ingestion SET status='uploading',updated_at=clock_timestamp() WHERE id=i.id;
 ELSIF p_action='uploaded' AND f.status='uploading' AND p_document ~ '^[A-Za-z0-9_-]+$' AND length(p_document)<=300 THEN
  IF EXISTS(SELECT 1 FROM knowledge.provider_binding WHERE provider_dataset_id=i.provider_dataset_id
    AND provider_document_id=p_document AND state='active') THEN
   RAISE EXCEPTION 'KNOWLEDGE_PROVIDER_BINDING_CONFLICT' USING ERRCODE='40001'; END IF;
  UPDATE knowledge.ingestion_file SET status='uploaded',provider_document_id=p_document,updated_at=clock_timestamp()
   WHERE ingestion_id=i.id AND file_id=p_file;
 ELSIF p_action='parse_started' AND f.status IN ('uploaded','parsing') THEN
  UPDATE knowledge.ingestion_file SET status='parsing',updated_at=clock_timestamp() WHERE ingestion_id=i.id AND file_id=p_file;
  UPDATE knowledge.ingestion SET status='parsing',updated_at=clock_timestamp() WHERE id=i.id;
  UPDATE knowledge.version SET state='parsing' WHERE id=i.version_id;
 ELSIF p_action='parsed' AND f.status IN ('uploaded','parsing') AND p_chunks>0 THEN
  UPDATE knowledge.ingestion_file SET status='parsed',chunk_count=p_chunks,updated_at=clock_timestamp()
   WHERE ingestion_id=i.id AND file_id=p_file;
 ELSE RAISE EXCEPTION 'KNOWLEDGE_INGESTION_TRANSITION_INVALID' USING ERRCODE='22023';
 END IF;
END $$;
-- Serialize cross-table identity claims. Legacy manual bindings have no origin,
-- so conservatively reserve dataset/document across origins when they are used.
CREATE FUNCTION security.knowledge_document_claim_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
BEGIN
 IF NEW.provider_document_id IS NULL THEN RETURN NEW; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('knowledge-document:'||NEW.provider_dataset_id||':'||NEW.provider_document_id,0));
 IF TG_TABLE_NAME='provider_binding' THEN
  IF EXISTS(SELECT 1 FROM knowledge.ingestion_file WHERE provider_dataset_id=NEW.provider_dataset_id
       AND provider_document_id=NEW.provider_document_id)
   OR EXISTS(SELECT 1 FROM knowledge.ingestion WHERE workspace_id=NEW.workspace_id
       AND material_id=NEW.material_id AND version_id=NEW.version_id)
   OR EXISTS(SELECT 1 FROM knowledge.provider_binding WHERE provider_dataset_id=NEW.provider_dataset_id
       AND provider_document_id=NEW.provider_document_id
       AND (workspace_id,material_id,version_id)<>(NEW.workspace_id,NEW.material_id,NEW.version_id)) THEN
   RAISE EXCEPTION 'KNOWLEDGE_PROVIDER_BINDING_CONFLICT' USING ERRCODE='40001'; END IF;
 ELSE
  IF EXISTS(SELECT 1 FROM knowledge.provider_binding WHERE provider_dataset_id=NEW.provider_dataset_id
       AND provider_document_id=NEW.provider_document_id AND state='active') THEN
   RAISE EXCEPTION 'KNOWLEDGE_PROVIDER_BINDING_CONFLICT' USING ERRCODE='40001'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER knowledge_provider_document_claim BEFORE INSERT OR UPDATE ON knowledge.provider_binding
 FOR EACH ROW EXECUTE FUNCTION security.knowledge_document_claim_guard();
CREATE TRIGGER knowledge_ingestion_document_claim BEFORE INSERT OR UPDATE OF provider_document_id ON knowledge.ingestion_file
 FOR EACH ROW EXECUTE FUNCTION security.knowledge_document_claim_guard();
REVOKE ALL ON FUNCTION security.knowledge_document_claim_guard() FROM PUBLIC,salegent_feishu_worker;
REVOKE ALL ON FUNCTION security.knowledge_ingestion_allowed(uuid,uuid),
 security.knowledge_ingestion_start(uuid,uuid,integer,text,text,text,jsonb),
 security.knowledge_ingestion_step(uuid,text,uuid,text,integer,text) FROM PUBLIC,salegent_feishu_worker;
INSERT INTO ops.schema_migration(version,description) VALUES('V416','Durable exact-version knowledge file ingestion');
COMMIT;
