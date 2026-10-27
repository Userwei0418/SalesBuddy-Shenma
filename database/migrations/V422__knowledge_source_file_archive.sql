BEGIN;
-- Source descriptions remain unchanged. Bindings belong to immutable versions,
-- so reimport can retain only exact attachments still present in the new source.
CREATE TABLE knowledge.source_file_binding (
 workspace_id uuid NOT NULL, material_id uuid NOT NULL, version_id uuid NOT NULL,
 source_token text NOT NULL CHECK(source_token ~ '^[A-Za-z0-9_-]{1,100}$'),
 name text NOT NULL, size_bytes integer NOT NULL CHECK(size_bytes>0 AND size_bytes<=52428800),
 file_id uuid NOT NULL,
 PRIMARY KEY(workspace_id,version_id,source_token),
 UNIQUE(workspace_id,version_id,file_id),
 FOREIGN KEY(workspace_id,material_id,version_id) REFERENCES knowledge.version(workspace_id,material_id,id),
 FOREIGN KEY(workspace_id,version_id,file_id) REFERENCES knowledge.version_file(workspace_id,version_id,file_id)
);
ALTER TABLE knowledge.source_file_binding ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.source_file_binding FORCE ROW LEVEL SECURITY;
REVOKE ALL ON knowledge.source_file_binding FROM PUBLIC;
CREATE POLICY source_file_read ON knowledge.source_file_binding FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.knowledge_can(material_id,'read')
 AND EXISTS(SELECT 1 FROM knowledge.version v WHERE v.workspace_id=source_file_binding.workspace_id
  AND v.id=source_file_binding.version_id AND v.state<>'deleted'));

CREATE FUNCTION security.knowledge_source_files(p_material uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE m knowledge.material; v knowledge.version; b knowledge.source_binding; a knowledge.material_acl; files jsonb;
BEGIN
 IF NOT security.knowledge_feature('knowledge.catalog_manage') OR NOT security.knowledge_can(p_material,'read')
  OR NOT security.knowledge_can(p_material,'manage') THEN
  RAISE EXCEPTION 'KNOWLEDGE_SOURCE_ARCHIVE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO m FROM knowledge.material WHERE workspace_id=common.current_workspace_id() AND id=p_material AND deleted_at IS NULL;
 SELECT * INTO v FROM knowledge.version WHERE workspace_id=m.workspace_id AND material_id=m.id AND id=m.latest_version_id AND state<>'deleted';
 SELECT * INTO b FROM knowledge.source_binding WHERE workspace_id=m.workspace_id AND material_id=m.id;
 SELECT * INTO a FROM knowledge.material_acl WHERE workspace_id=m.workspace_id AND material_id=m.id;
 IF b.material_id IS NULL OR v.id IS NULL OR v.contribution->>'kind' IS DISTINCT FROM 'legacy_import' THEN
  RAISE EXCEPTION 'KNOWLEDGE_SOURCE_ARCHIVE_UNAVAILABLE' USING ERRCODE='P0002'; END IF;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('source_token',s.source_token,'name',s.name,'size_bytes',s.size_bytes,
  'file_id',s.file_id,'sha256',f.sha256,'media_type',f.media_type) ORDER BY vf.ordinal),'[]') INTO files
 FROM knowledge.source_file_binding s JOIN knowledge.file f ON f.workspace_id=s.workspace_id AND f.id=s.file_id
 JOIN knowledge.version_file vf ON vf.workspace_id=s.workspace_id AND vf.version_id=s.version_id AND vf.file_id=s.file_id
 WHERE s.workspace_id=m.workspace_id AND s.version_id=v.id;
 RETURN jsonb_build_object('material_id',m.id,'version_id',v.id,'version_no',v.version_no,'material_revision',m.revision,
  'acl_revision',a.revision,'source_revision',b.source_revision,'source_status',v.contribution#>>'{source,source_status}',
  'attachments',v.contribution#>'{source,attachments}','files',files,'changed',false,
  'can_archive',v.contribution#>>'{source,source_status}'='可用' AND m.revision=b.imported_material_revision
   AND v.id=b.imported_version_id);
END $$;

CREATE FUNCTION security.knowledge_archive_source_files(p_material uuid,p_material_revision integer,
 p_acl_revision integer,p_source_revision bigint,p_attachments jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE m knowledge.material; v knowledge.version; b knowledge.source_binding; a knowledge.material_acl;
 item jsonb; source_item jsonb; f knowledge.file; old_file knowledge.file; existing knowledge.source_file_binding;
 new_id uuid; new_no integer; result jsonb; changed boolean:=false;
 checked_file uuid;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('knowledge:'||common.current_workspace_id()::text,0));
 result:=security.knowledge_source_files(p_material);
 IF result->>'can_archive' IS DISTINCT FROM 'true' THEN
  RAISE EXCEPTION 'KNOWLEDGE_SOURCE_ARCHIVE_UNAVAILABLE' USING ERRCODE='22023'; END IF;
 SELECT * INTO m FROM knowledge.material WHERE workspace_id=common.current_workspace_id() AND id=p_material FOR UPDATE;
 SELECT * INTO a FROM knowledge.material_acl WHERE workspace_id=m.workspace_id AND material_id=m.id FOR UPDATE;
 SELECT * INTO b FROM knowledge.source_binding WHERE workspace_id=m.workspace_id AND material_id=m.id FOR UPDATE;
 SELECT * INTO v FROM knowledge.version WHERE workspace_id=m.workspace_id AND id=m.latest_version_id;
 IF p_material_revision IS DISTINCT FROM m.revision OR p_acl_revision IS DISTINCT FROM a.revision
  OR p_source_revision IS DISTINCT FROM b.source_revision THEN
  RAISE EXCEPTION 'KNOWLEDGE_SOURCE_REVISION_CONFLICT' USING ERRCODE='40001'; END IF;
 IF jsonb_typeof(p_attachments) IS DISTINCT FROM 'array' OR jsonb_array_length(p_attachments) NOT BETWEEN 1 AND 100
  OR (SELECT count(*)<>count(DISTINCT x->>'source_token') OR count(*)<>count(DISTINCT x->>'file_id')
    FROM jsonb_array_elements(p_attachments) x) THEN
  RAISE EXCEPTION 'KNOWLEDGE_SOURCE_ATTACHMENT_INVALID' USING ERRCODE='22023'; END IF;
 FOR item IN SELECT * FROM jsonb_array_elements(p_attachments) LOOP
  IF jsonb_typeof(item) IS DISTINCT FROM 'object'
   OR EXISTS(SELECT 1 FROM jsonb_object_keys(item) k WHERE k NOT IN ('source_token','name','size_bytes','file_id'))
   OR jsonb_typeof(item->'source_token') IS DISTINCT FROM 'string' OR item->>'source_token' !~ '^[A-Za-z0-9_-]{1,100}$'
   OR jsonb_typeof(item->'name') IS DISTINCT FROM 'string' OR length(item->>'name') NOT BETWEEN 1 AND 300
   OR jsonb_typeof(item->'size_bytes') IS DISTINCT FROM 'number' OR item->>'size_bytes' !~ '^[0-9]+$'
   OR jsonb_typeof(item->'file_id') IS DISTINCT FROM 'string' THEN
   RAISE EXCEPTION 'KNOWLEDGE_SOURCE_ATTACHMENT_INVALID' USING ERRCODE='22023'; END IF;
  SELECT x INTO source_item FROM jsonb_array_elements(v.contribution#>'{source,attachments}') x
   WHERE x->>'source_token'=item->>'source_token';
  IF source_item IS NULL OR source_item->>'name' IS DISTINCT FROM item->>'name'
   OR source_item->'size_bytes' IS DISTINCT FROM item->'size_bytes' THEN
   RAISE EXCEPTION 'KNOWLEDGE_SOURCE_ATTACHMENT_MISMATCH' USING ERRCODE='22023'; END IF;
  SELECT * INTO f FROM knowledge.file WHERE workspace_id=m.workspace_id AND id=(item->>'file_id')::uuid;
  SELECT * INTO existing FROM knowledge.source_file_binding WHERE workspace_id=m.workspace_id
   AND version_id=v.id AND source_token=item->>'source_token';
  IF f.id IS NULL OR (existing.file_id IS DISTINCT FROM f.id AND NOT security.knowledge_file_attach(f.id,m.id)) THEN
   RAISE EXCEPTION 'KNOWLEDGE_FILE_DENIED' USING ERRCODE='42501'; END IF;
  IF EXISTS(SELECT 1 FROM knowledge.source_file_binding WHERE workspace_id=m.workspace_id
   AND version_id=v.id AND file_id=f.id AND source_token<>item->>'source_token') THEN
   RAISE EXCEPTION 'KNOWLEDGE_SOURCE_FILE_DUPLICATE' USING ERRCODE='22023'; END IF;
  IF f.filename IS DISTINCT FROM item->>'name' OR to_jsonb(f.size_bytes) IS DISTINCT FROM item->'size_bytes'
   OR f.size_bytes>52428800 THEN
   RAISE EXCEPTION 'KNOWLEDGE_SOURCE_FILE_MISMATCH' USING ERRCODE='22023'; END IF;
  IF existing.file_id IS NOT NULL THEN
   SELECT * INTO old_file FROM knowledge.file WHERE workspace_id=m.workspace_id AND id=existing.file_id;
   IF old_file.sha256 IS DISTINCT FROM f.sha256 THEN
    RAISE EXCEPTION 'KNOWLEDGE_SOURCE_FILE_ALREADY_ARCHIVED' USING ERRCODE='40001'; END IF;
  ELSE changed:=true; END IF;
 END LOOP;
 IF NOT changed THEN RETURN result; END IF;
 -- A no-write current-binding replay above requires only read/manage. Any
 -- new version must authorize every retained file, including omitted inputs.
 FOR checked_file IN SELECT file_id FROM knowledge.source_file_binding
  WHERE workspace_id=m.workspace_id AND version_id=v.id LOOP
  IF NOT security.knowledge_file_attach(checked_file,m.id) THEN
   RAISE EXCEPTION 'KNOWLEDGE_FILE_DENIED' USING ERRCODE='42501'; END IF;
 END LOOP;
 new_id:=gen_random_uuid();
 SELECT COALESCE(max(version_no),0)+1 INTO new_no FROM knowledge.version WHERE workspace_id=m.workspace_id AND material_id=m.id;
 INSERT INTO knowledge.version(workspace_id,material_id,id,version_no,contribution,state,contributor_user_id)
  VALUES(m.workspace_id,m.id,new_id,new_no,v.contribution,'saved',common.current_user_ref_id());
 -- Retain existing source bindings and append only newly requested source tokens.
 INSERT INTO knowledge.version_file(workspace_id,material_id,version_id,file_id,ordinal)
 SELECT m.workspace_id,m.id,new_id,chosen.file_id,(row_number() OVER(ORDER BY src.ordinality)-1)::integer
 FROM jsonb_array_elements(v.contribution#>'{source,attachments}') WITH ORDINALITY src(value,ordinality)
 JOIN LATERAL(SELECT old.file_id FROM knowledge.source_file_binding old
   WHERE old.workspace_id=m.workspace_id AND old.version_id=v.id AND old.source_token=src.value->>'source_token'
   UNION ALL SELECT (x->>'file_id')::uuid FROM jsonb_array_elements(p_attachments) x
   WHERE x->>'source_token'=src.value->>'source_token' AND NOT EXISTS(SELECT 1 FROM knowledge.source_file_binding old
    WHERE old.workspace_id=m.workspace_id AND old.version_id=v.id AND old.source_token=src.value->>'source_token')) chosen ON true;
 INSERT INTO knowledge.source_file_binding(workspace_id,material_id,version_id,source_token,name,size_bytes,file_id)
 SELECT m.workspace_id,m.id,new_id,src->>'source_token',src->>'name',(src->>'size_bytes')::integer,vf.file_id
 FROM jsonb_array_elements(v.contribution#>'{source,attachments}') src
 JOIN knowledge.version_file vf ON vf.workspace_id=m.workspace_id AND vf.version_id=new_id
 WHERE vf.file_id=COALESCE((SELECT old.file_id FROM knowledge.source_file_binding old WHERE old.workspace_id=m.workspace_id
  AND old.version_id=v.id AND old.source_token=src->>'source_token'),
  (SELECT (x->>'file_id')::uuid FROM jsonb_array_elements(p_attachments) x WHERE x->>'source_token'=src->>'source_token'));
 UPDATE knowledge.material SET latest_version_id=new_id,revision=revision+1,updated_at=clock_timestamp()
  WHERE workspace_id=m.workspace_id AND id=m.id;
 UPDATE knowledge.source_binding SET imported_material_revision=m.revision+1,imported_version_id=new_id
  WHERE workspace_id=m.workspace_id AND material_id=m.id;
 INSERT INTO knowledge.audit(workspace_id,actor_id,material_id,operation,detail)
  VALUES(m.workspace_id,common.current_user_ref_id(),m.id,'source.archive',jsonb_build_object(
   'previous_version_id',v.id,'version_id',new_id,'source_revision',b.source_revision,
   'file_count',(SELECT count(*) FROM knowledge.version_file WHERE workspace_id=m.workspace_id AND version_id=new_id)));
 RETURN security.knowledge_source_files(m.id)||jsonb_build_object('changed',true);
END $$;
REVOKE ALL ON FUNCTION security.knowledge_source_files(uuid),
 security.knowledge_archive_source_files(uuid,integer,integer,bigint,jsonb) FROM PUBLIC;

-- Preserve the existing public import function identity/ACL and its complete
-- conflict contract. The private predecessor has no runtime EXECUTE grant.
DO $$ DECLARE original_owner name; BEGIN
 SELECT pg_get_userbyid(proowner) INTO original_owner FROM pg_proc
  WHERE oid='security.knowledge_import_record(uuid,uuid,jsonb,text,text,text,bigint,jsonb,boolean)'::regprocedure;
 EXECUTE replace(pg_get_functiondef('security.knowledge_import_record(uuid,uuid,jsonb,text,text,text,bigint,jsonb,boolean)'::regprocedure),
  'CREATE OR REPLACE FUNCTION security.knowledge_import_record(', 'CREATE OR REPLACE FUNCTION security.knowledge_import_before_source_files(');
 EXECUTE format('ALTER FUNCTION security.knowledge_import_before_source_files(uuid,uuid,jsonb,text,text,text,bigint,jsonb,boolean) OWNER TO %I',original_owner);
END $$;
REVOKE ALL ON FUNCTION security.knowledge_import_before_source_files(uuid,uuid,jsonb,text,text,text,bigint,jsonb,boolean) FROM PUBLIC;

CREATE OR REPLACE FUNCTION security.knowledge_import_record(p_workspace uuid,p_owner uuid,p_grants jsonb,
 p_base text,p_table text,p_record text,p_source_revision bigint,p_content jsonb,p_dry_run boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE old_version uuid; new_version uuid; result jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('knowledge:'||common.current_workspace_id()::text,0));
 -- Authenticate before looking up the prior source mapping, even though only
 -- the unchanged predecessor's result is returned to the caller.
 PERFORM security.knowledge_import_authorize(p_workspace,p_owner,p_grants);
 SELECT imported_version_id INTO old_version FROM knowledge.source_binding
  WHERE workspace_id=p_workspace AND base_id=p_base AND table_id=p_table AND record_id=p_record;
 result:=security.knowledge_import_before_source_files(p_workspace,p_owner,p_grants,p_base,p_table,p_record,
  p_source_revision,p_content,p_dry_run);
 IF NOT p_dry_run AND result->>'outcome'='updated' AND p_content#>>'{source,source_status}'='可用' THEN
  new_version:=(result->>'version_id')::uuid;
  INSERT INTO knowledge.version_file(workspace_id,material_id,version_id,file_id,ordinal)
  SELECT s.workspace_id,s.material_id,new_version,s.file_id,(row_number() OVER(ORDER BY src.ordinality)-1)::integer
  FROM knowledge.source_file_binding s
  JOIN jsonb_array_elements(p_content#>'{source,attachments}') WITH ORDINALITY src(value,ordinality)
   ON src.value->>'source_token'=s.source_token AND src.value->>'name'=s.name
    AND src.value->'size_bytes'=to_jsonb(s.size_bytes)
  WHERE s.workspace_id=p_workspace AND s.version_id=old_version;
  INSERT INTO knowledge.source_file_binding(workspace_id,material_id,version_id,source_token,name,size_bytes,file_id)
  SELECT s.workspace_id,s.material_id,new_version,s.source_token,s.name,s.size_bytes,s.file_id
  FROM knowledge.source_file_binding s JOIN knowledge.version_file vf ON vf.workspace_id=s.workspace_id
   AND vf.version_id=new_version AND vf.file_id=s.file_id WHERE s.workspace_id=p_workspace AND s.version_id=old_version;
 END IF;
 RETURN result;
END $$;
-- Source review/disable state remains a live publication boundary. A metadata
-- reimport does not rewrite publication/share history: availability is checked
-- afresh for both the pinned published version and the latest source version.
-- Restoring source availability follows the existing enabled/non-revoked share
-- policy; this emits no publication event or notification.
CREATE OR REPLACE FUNCTION security.kd_publication_live(p_id uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog SET row_security=off AS $$
 SELECT EXISTS(SELECT 1 FROM knowledge.distribution_publication p
 JOIN knowledge.material m ON m.workspace_id=p.workspace_id AND m.id=p.material_id
 JOIN knowledge.version v ON v.workspace_id=p.workspace_id AND v.id=p.version_id
 JOIN knowledge.version latest ON latest.workspace_id=m.workspace_id AND latest.id=m.latest_version_id
 WHERE p.id=p_id AND p.enabled AND m.deleted_at IS NULL AND v.state<>'deleted' AND latest.state<>'deleted'
 AND v.contribution->'selection'->>'content_scope' IN ('public','customer')
 AND latest.contribution->'selection'->>'content_scope' IN ('public','customer')
 AND (v.contribution->>'kind' IS DISTINCT FROM 'legacy_import' OR v.contribution#>>'{source,source_status}'='可用')
 AND (latest.contribution->>'kind' IS DISTINCT FROM 'legacy_import' OR latest.contribution#>>'{source,source_status}'='可用')
 AND security.workspace_is_active(p.workspace_id)
 AND security.tenant_permission_enabled(p.workspace_id,'knowledge.read')
 AND EXISTS(SELECT 1 FROM knowledge.version_file vf JOIN knowledge.file f ON f.workspace_id=vf.workspace_id AND f.id=vf.file_id
 WHERE vf.workspace_id=p.workspace_id AND vf.version_id=p.version_id));
$$;

CREATE OR REPLACE FUNCTION security.kd_publish(p_material uuid,p_body jsonb) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog SET row_security=off AS $$
DECLARE v knowledge.version; prev knowledge.distribution_publication; next_id uuid:=gen_random_uuid(); q jsonb; f jsonb; cover uuid; candidate knowledge.file; previewable boolean:=false;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('knowledge:'||common.current_workspace_id()::text,0));
 IF NOT security.kd_admin() OR NOT security.knowledge_can(p_material,'manage') OR NOT security.knowledge_can(p_material,'read') THEN
 RAISE EXCEPTION 'KNOWLEDGE_DISTRIBUTION_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO prev FROM knowledge.distribution_publication WHERE workspace_id=common.current_workspace_id() AND material_id=p_material ORDER BY revision DESC LIMIT 1;
 IF (p_body->>'expected_revision')::integer IS DISTINCT FROM COALESCE(prev.revision,0) THEN
 RAISE EXCEPTION 'KNOWLEDGE_PUBLICATION_CONFLICT' USING ERRCODE='40001'; END IF;
 SELECT * INTO v FROM knowledge.version WHERE workspace_id=common.current_workspace_id() AND material_id=p_material AND id=(p_body->>'version_id')::uuid AND state<>'deleted';
 IF NOT FOUND THEN RAISE EXCEPTION 'KNOWLEDGE_VERSION_UNAVAILABLE' USING ERRCODE='P0002'; END IF;
 IF (p_body->>'enabled')::boolean AND ((v.contribution->'selection'->>'content_scope' IN ('public','customer')) IS NOT TRUE
 OR (v.contribution->>'kind'='legacy_import' AND v.contribution#>>'{source,source_status}' IS DISTINCT FROM '可用')
 OR NOT EXISTS(SELECT 1 FROM knowledge.version_file WHERE workspace_id=v.workspace_id AND version_id=v.id)) THEN
 RAISE EXCEPTION 'KNOWLEDGE_DISTRIBUTION_CONTENT_REQUIRED' USING ERRCODE='22023'; END IF;
 IF (p_body->>'enabled')::boolean THEN
 IF NOT EXISTS(SELECT 1 FROM knowledge.material m JOIN knowledge.version latest ON latest.workspace_id=m.workspace_id AND latest.id=m.latest_version_id
 WHERE m.id=p_material AND m.workspace_id=v.workspace_id AND latest.state<>'deleted'
 AND latest.contribution->'selection'->>'content_scope' IN ('public','customer')
 AND (latest.contribution->>'kind' IS DISTINCT FROM 'legacy_import' OR latest.contribution#>>'{source,source_status}'='可用')) THEN
 RAISE EXCEPTION 'KNOWLEDGE_DISTRIBUTION_CONTENT_REQUIRED' USING ERRCODE='22023'; END IF;
 IF (p_body->>'allow_preview')::boolean AND NOT (p_body->>'allow_download')::boolean THEN
 FOR candidate IN SELECT k.* FROM knowledge.file k JOIN knowledge.version_file vf ON vf.workspace_id=k.workspace_id AND vf.file_id=k.id
 WHERE vf.workspace_id=v.workspace_id AND vf.version_id=v.id AND k.media_type IN ('text/plain','text/markdown','text/csv','application/pdf') LOOP
 IF candidate.media_type='application/pdf' THEN
 IF substring(candidate.content FROM 1 FOR 5)=decode('255044462d','hex') THEN previewable:=true; END IF;
 ELSE
 BEGIN PERFORM convert_from(candidate.content,'UTF8'); previewable:=true;
 EXCEPTION WHEN character_not_in_repertoire OR untranslatable_character THEN NULL; END;
 END IF;
 END LOOP;
 IF NOT previewable THEN RAISE EXCEPTION 'KNOWLEDGE_PREVIEW_UNSUPPORTED' USING ERRCODE='P0415'; END IF;
 END IF;
 END IF;
 q:=p_body->'questionnaire';
 IF jsonb_typeof(q->'enabled') IS DISTINCT FROM 'boolean' OR jsonb_typeof(q->'fields') IS DISTINCT FROM 'array'
 OR jsonb_array_length(q->'fields')<>4 OR (SELECT count(DISTINCT x->>'name') FROM jsonb_array_elements(q->'fields') x)<>4
 OR length(COALESCE(p_body->>'description',''))>2000 THEN RAISE EXCEPTION 'KNOWLEDGE_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 FOR f IN SELECT * FROM jsonb_array_elements(q->'fields') LOOP
 IF COALESCE(f->>'name','') NOT IN ('name','company','contact','contact_intent') OR jsonb_typeof(f->'enabled') IS DISTINCT FROM 'boolean'
 OR jsonb_typeof(f->'required') IS DISTINCT FROM 'boolean' OR ((f->>'required')::boolean AND NOT (f->>'enabled')::boolean) THEN
 RAISE EXCEPTION 'KNOWLEDGE_INPUT_INVALID' USING ERRCODE='22023'; END IF; END LOOP;
 IF (p_body->>'enabled')::boolean AND NOT ((p_body->>'allow_preview')::boolean OR (p_body->>'allow_download')::boolean) THEN
 RAISE EXCEPTION 'KNOWLEDGE_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 cover:=(p_body->>'cover_file_id')::uuid;
 IF cover IS NOT NULL AND NOT EXISTS(SELECT 1 FROM knowledge.file k
 JOIN knowledge.version_file vf ON vf.workspace_id=k.workspace_id AND vf.file_id=k.id
 WHERE vf.workspace_id=v.workspace_id AND vf.material_id=p_material AND vf.version_id=v.id AND k.id=cover
 AND k.media_type IN ('image/png','image/jpeg','image/webp') AND k.size_bytes<=2097152
 AND security.knowledge_file_read(k.id)) THEN
 RAISE EXCEPTION 'KNOWLEDGE_COVER_UNAVAILABLE' USING ERRCODE='22023'; END IF;
 UPDATE knowledge.distribution_publication SET enabled=false WHERE workspace_id=v.workspace_id AND material_id=p_material AND enabled;
 INSERT INTO knowledge.distribution_publication(workspace_id,id,material_id,version_id,revision,enabled,description,cover_file_id,
 allow_preview,allow_download,questionnaire,published_by) VALUES(v.workspace_id,next_id,p_material,v.id,COALESCE(prev.revision,0)+1,
 (p_body->>'enabled')::boolean,COALESCE(p_body->>'description',''),cover,(p_body->>'allow_preview')::boolean,
 (p_body->>'allow_download')::boolean,q,common.current_user_ref_id());
 INSERT INTO knowledge.audit(workspace_id,actor_id,material_id,operation,detail) VALUES(v.workspace_id,common.current_user_ref_id(),p_material,
 'distribution.publish',jsonb_build_object('publication_id',next_id,'version_id',v.id,'enabled',p_body->'enabled'));
 RETURN next_id;
END $$;

INSERT INTO ops.schema_migration(version,description) VALUES('V422','Exact immutable source attachment archives and reimport retention');
COMMIT;
