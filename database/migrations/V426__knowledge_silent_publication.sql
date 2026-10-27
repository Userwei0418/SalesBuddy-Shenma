BEGIN;
-- Keep function identities, grants and live ACL checks. Silent backfill uses the
-- same material/version and transactional outbox; it never toggles group settings.
CREATE OR REPLACE FUNCTION security.kd_publish(p_material uuid,p_body jsonb) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog SET row_security=off AS $$
DECLARE v knowledge.version; prev knowledge.distribution_publication; next_id uuid:=gen_random_uuid(); q jsonb; f jsonb; cover uuid; candidate knowledge.file; previewable boolean:=false;
BEGIN
 IF p_body ? 'notify' AND jsonb_typeof(p_body->'notify') IS DISTINCT FROM 'boolean' THEN
 RAISE EXCEPTION 'KNOWLEDGE_INPUT_INVALID' USING ERRCODE='22023'; END IF;
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
 'distribution.publish',jsonb_build_object('publication_id',next_id,'version_id',v.id,'enabled',p_body->'enabled',
 'notify',COALESCE((p_body->>'notify')::boolean,true)));
 RETURN next_id;
END $$;

CREATE OR REPLACE FUNCTION security.knowledge_distribution_enqueue(p_workspace uuid,p_material uuid,p_publication uuid,
 p_version uuid,p_kind text,p_revision integer,p_actor uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE c config.feishu_connection; p knowledge.distribution_publication; previous knowledge.distribution_publication; snapshot jsonb; raw jsonb; n integer; expected_kind text; announce boolean;
BEGIN
 IF p_workspace IS DISTINCT FROM common.current_workspace_id() OR p_actor IS DISTINCT FROM common.current_user_ref_id()
  OR NOT security.knowledge_feature('knowledge.catalog_manage') OR NOT security.knowledge_can(p_material,'manage')
  OR NOT security.knowledge_can(p_material,'read') THEN RAISE insufficient_privilege; END IF;
 IF p_kind NOT IN ('published','version_published','metadata_updated','withdrawn') THEN
  RAISE EXCEPTION 'KNOWLEDGE_SYNC_EVENT_INVALID' USING ERRCODE='22023'; END IF;
 SELECT * INTO p FROM knowledge.distribution_publication WHERE workspace_id=p_workspace AND material_id=p_material
  AND id=p_publication AND version_id=p_version AND revision=p_revision AND published_by=p_actor
  AND enabled=(p_kind<>'withdrawn');
 IF NOT FOUND OR EXISTS(SELECT 1 FROM knowledge.distribution_publication
   WHERE workspace_id=p_workspace AND material_id=p_material AND revision>p_revision) THEN
  RAISE EXCEPTION 'KNOWLEDGE_SYNC_PUBLICATION_STALE' USING ERRCODE='40001'; END IF;
 SELECT * INTO previous FROM knowledge.distribution_publication WHERE workspace_id=p_workspace
  AND material_id=p_material AND revision<p_revision ORDER BY revision DESC LIMIT 1;
 expected_kind:=CASE WHEN NOT p.enabled THEN 'withdrawn' WHEN previous.id IS NULL THEN 'published'
  WHEN previous.version_id=p.version_id THEN 'metadata_updated' ELSE 'version_published' END;
 IF p_kind<>expected_kind THEN RAISE EXCEPTION 'KNOWLEDGE_SYNC_EVENT_INVALID' USING ERRCODE='22023'; END IF;
 -- The immutable publish audit carries the administrator's explicit choice.
 -- Old publications without this field preserve their original notification intent.
 SELECT COALESCE((detail->>'notify')::boolean,true) INTO announce FROM knowledge.audit
 WHERE workspace_id=p_workspace AND material_id=p_material AND operation='distribution.publish'
 AND detail->>'publication_id'=p_publication::text;
 IF NOT FOUND THEN RAISE EXCEPTION 'KNOWLEDGE_SYNC_PUBLICATION_STALE' USING ERRCODE='40001'; END IF;
 SELECT * INTO c FROM config.feishu_connection WHERE workspace_id=p_workspace
  AND settings#>>'{mappings,knowledge,enabled}'='true';
 IF c.id IS NULL OR NOT security.workspace_is_active(p_workspace)
  OR NOT security.workspace_module_enabled(p_workspace,'feishu') THEN RETURN 0; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(c.id::text||':knowledge:'||p_material::text,0));
 -- Publishing/withdrawal must not wait behind the delayed statistics batch.
 UPDATE ops.feishu_event SET available_at=LEAST(available_at,clock_timestamp())
  WHERE connection_id=c.id AND object_kind='knowledge' AND object_id=p_material AND historical AND status='pending';
 IF c.enabled AND announce AND p_kind IN ('published','version_published') THEN
  raw:=ops.feishu_source(c.id,'knowledge',p_material);
  IF raw->>'excluded' IS DISTINCT FROM 'true' THEN
   snapshot:=raw||jsonb_build_object('schema_version',1,'change_id',p.id,'workspace_id',p_workspace,
    'material_id',p_material,'publication_id',p.id,'event_kind',p_kind);
  END IF;
 END IF;
 INSERT INTO ops.feishu_event(id,connection_id,workspace_id,object_kind,object_id,first_formal_create,
  historical,queue_origin,notification_snapshot)
 VALUES(p.id,c.id,p_workspace,'knowledge',p_material,p_kind='published' AND announce,snapshot IS NULL,'business',snapshot)
 ON CONFLICT(id) DO NOTHING;
 GET DIAGNOSTICS n=ROW_COUNT; RETURN n;
END $$;

INSERT INTO ops.schema_migration(version,description) VALUES('V426','Allow explicit silent knowledge publication with Feishu synchronization');
COMMIT;
