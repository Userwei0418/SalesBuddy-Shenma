BEGIN;
-- Keep preview capability consistent across API, publication admission and public
-- share delivery. Image previews are allowed only for the declared MIME type
-- and its expected magic bytes; downloads and access policy are unchanged.
CREATE FUNCTION security.kd_preview_supported(p_media_type text,p_content bytea)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER
SET search_path=pg_catalog SET row_security=off AS $$
BEGIN
 IF p_media_type='application/pdf' THEN
  RETURN substring(p_content FROM 1 FOR 5)=decode('255044462d','hex');
 ELSIF p_media_type='image/png' THEN
  RETURN substring(p_content FROM 1 FOR 8)=decode('89504e470d0a1a0a','hex');
 ELSIF p_media_type='image/jpeg' THEN
  RETURN substring(p_content FROM 1 FOR 3)=decode('ffd8ff','hex');
 ELSIF p_media_type='image/webp' THEN
  RETURN substring(p_content FROM 1 FOR 4)=decode('52494646','hex')
   AND substring(p_content FROM 9 FOR 4)=decode('57454250','hex');
 ELSIF p_media_type IN ('text/plain','text/markdown','text/csv') THEN
  BEGIN
   PERFORM convert_from(p_content,'UTF8');
   RETURN true;
  EXCEPTION WHEN character_not_in_repertoire OR untranslatable_character THEN
   RETURN false;
  END;
 END IF;
 RETURN false;
END $$;
DO $$ DECLARE original_owner name; BEGIN
 SELECT pg_get_userbyid(proowner) INTO original_owner FROM pg_proc
  WHERE oid='security.kd_files(uuid)'::regprocedure;
 EXECUTE format('ALTER FUNCTION security.kd_preview_supported(text,bytea) OWNER TO %I',original_owner);
END $$;
REVOKE ALL ON FUNCTION security.kd_preview_supported(text,bytea) FROM PUBLIC;

CREATE OR REPLACE FUNCTION security.kd_files(p_id uuid) RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog SET row_security=off AS $$
 SELECT COALESCE(jsonb_agg(jsonb_build_object('file_id',f.id,'filename',f.filename,'media_type',f.media_type,
 'size_bytes',f.size_bytes,'preview_supported',security.kd_preview_supported(f.media_type,f.content)) ORDER BY vf.ordinal),'[]')
 FROM knowledge.distribution_publication p JOIN knowledge.version_file vf ON vf.workspace_id=p.workspace_id AND vf.version_id=p.version_id
 JOIN knowledge.file f ON f.workspace_id=vf.workspace_id AND f.id=vf.file_id WHERE p.id=p_id;
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
 WHERE vf.workspace_id=v.workspace_id AND vf.version_id=v.id
  AND k.media_type IN ('text/plain','text/markdown','text/csv','application/pdf','image/png','image/jpeg','image/webp') LOOP
  IF security.kd_preview_supported(candidate.media_type,candidate.content) THEN previewable:=true; END IF;
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

CREATE OR REPLACE FUNCTION security.kd_public_file(p_hash text,p_grant text,p_file uuid,p_mode text)
RETURNS TABLE(filename text,media_type text,content bytea) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE s knowledge.distribution_share; p knowledge.distribution_publication; r knowledge.distribution_response; f knowledge.file;
BEGIN
 SELECT * INTO s FROM knowledge.distribution_share WHERE token_hash=p_hash;
 IF NOT FOUND OR NOT security.kd_share_live(s.id) THEN RAISE EXCEPTION 'KNOWLEDGE_SHARE_UNAVAILABLE' USING ERRCODE='P0002'; END IF;
 SELECT * INTO p FROM knowledge.distribution_publication WHERE id=s.publication_id;
 IF p_mode='cover' THEN
 SELECT k.* INTO f FROM knowledge.file k
 JOIN knowledge.version_file vf ON vf.workspace_id=k.workspace_id AND vf.file_id=k.id
 JOIN knowledge.version v ON v.workspace_id=vf.workspace_id AND v.material_id=vf.material_id AND v.id=vf.version_id
 WHERE k.id=p.cover_file_id AND vf.workspace_id=p.workspace_id AND vf.material_id=p.material_id
 AND vf.version_id=p.version_id AND v.state<>'deleted'
 AND k.media_type IN ('image/png','image/jpeg','image/webp') AND k.size_bytes<=2097152;
 ELSE
 SELECT * INTO r FROM knowledge.distribution_response WHERE share_id=s.id AND workspace_id=s.workspace_id AND grant_hash=p_grant AND expires_at>clock_timestamp();
 IF NOT FOUND THEN RAISE EXCEPTION 'KNOWLEDGE_QUESTIONNAIRE_REQUIRED' USING ERRCODE='42501'; END IF;
 IF (p_mode='preview' AND NOT p.allow_preview) OR (p_mode='download' AND NOT p.allow_download) OR p_mode NOT IN ('preview','download') THEN
 RAISE EXCEPTION 'KNOWLEDGE_DISTRIBUTION_DENIED' USING ERRCODE='42501'; END IF;
 SELECT k.* INTO f FROM knowledge.file k JOIN knowledge.version_file vf ON vf.workspace_id=k.workspace_id AND vf.file_id=k.id
 WHERE vf.workspace_id=p.workspace_id AND vf.version_id=p.version_id AND k.id=p_file;
 END IF;
 IF f.id IS NULL THEN RAISE EXCEPTION 'KNOWLEDGE_FILE_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF p_mode='preview' AND NOT security.kd_preview_supported(f.media_type,f.content) THEN
  RAISE EXCEPTION 'KNOWLEDGE_PREVIEW_UNSUPPORTED' USING ERRCODE='P0415';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('kd-public:'||s.id::text,0));
 IF NOT security.kd_share_live(s.id) THEN RAISE EXCEPTION 'KNOWLEDGE_SHARE_UNAVAILABLE' USING ERRCODE='P0002'; END IF;
 IF (SELECT count(*) FROM knowledge.distribution_event WHERE share_id=s.id AND kind<>'visit' AND occurred_at>clock_timestamp()-interval '1 hour')>=10000 THEN
  RAISE EXCEPTION 'KNOWLEDGE_PUBLIC_RATE_LIMIT' USING ERRCODE='P0429'; END IF;
 INSERT INTO knowledge.distribution_event(workspace_id,share_id,browser_hash,kind) VALUES(s.workspace_id,s.id,COALESCE(r.browser_hash,''),p_mode);
 RETURN QUERY SELECT f.filename,f.media_type,f.content;
END $$;

INSERT INTO ops.schema_migration(version,description)
 VALUES('V425','Allow controlled PNG, JPEG and WebP previews for knowledge shares');
COMMIT;
