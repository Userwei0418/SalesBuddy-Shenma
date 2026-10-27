BEGIN;
-- Audio is available only through owner-authorized inline playback, never downloads/exports. Document
-- permissions, function identities and existing runtime EXECUTE grants stay intact.
DO $preflight$
BEGIN
 IF (SELECT md5(prosrc) FROM pg_proc WHERE oid='security.authorization_import(text,uuid)'::regprocedure) IS DISTINCT FROM '91ce5ba74c6256750e908d4c684a516f' THEN
  RAISE EXCEPTION 'V283_UNRECOGNIZED_AUDIO_AUTHORIZATION';
 END IF;
 IF (SELECT md5(prosrc) FROM pg_proc WHERE oid='security.closed_export_originals(text,boolean,integer,integer)'::regprocedure) IS DISTINCT FROM '9c9e49c38785ce486b1698a9a65a3340' THEN
  RAISE EXCEPTION 'V283_UNRECOGNIZED_AUDIO_AUTHORIZATION';
 END IF;
 IF (SELECT md5(prosrc) FROM pg_proc WHERE oid='security.closed_export_content_chunk(text,uuid,integer)'::regprocedure) IS DISTINCT FROM '1e23cd3524fe70a866d16423d134e252' THEN
  RAISE EXCEPTION 'V283_UNRECOGNIZED_AUDIO_AUTHORIZATION';
 END IF;
END $preflight$;

CREATE OR REPLACE FUNCTION security.authorization_import(p_permission text,p_import uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM activity.visit_import i WHERE i.id=p_import
 AND i.workspace_id=common.current_workspace_id()
 AND (p_permission IS DISTINCT FROM 'visit.download_original' OR i.filename !~* '\.(mp3|wav|m4a|aac|ogg|flac|amr|webm)$')
 AND (
  (i.created_by_user_ref_id=common.current_user_ref_id() AND security.authorization_allows(
   p_permission,i.workspace_id,i.created_by_user_ref_id,ARRAY[]::uuid[],true))
  -- BEGIN V374 own unarchived team resource
  OR (i.created_by_user_ref_id=common.current_user_ref_id()
   AND p_permission IN ('visit.read','visit.download_original')
   AND NOT EXISTS(SELECT 1 FROM activity.visit linked
    WHERE linked.source_import_id=i.id AND linked.workspace_id=i.workspace_id
     AND linked.status IN ('confirmed','archived') AND linked.deleted_at IS NULL)
   AND security.authorization_subject(p_permission,'person',i.created_by_user_ref_id,NULL))
  -- END V374 own unarchived team resource
  OR (p_permission IN ('visit.read','visit.download_original') AND EXISTS(
   SELECT 1 FROM activity.visit v WHERE v.source_import_id=i.id AND v.workspace_id=i.workspace_id
   AND v.status IN ('confirmed','archived') AND v.deleted_at IS NULL
   AND security.authorization_visit(p_permission,v.id)))));
$$;

CREATE OR REPLACE FUNCTION security.closed_export_originals(p_session_hash text,p_originals boolean,p_offset integer,p_limit integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE person jsonb; previous jsonb; result jsonb;
BEGIN
 PERFORM security._closed_export_page(p_offset,p_limit);
 person:=security.closed_export_authorize(p_session_hash,ARRAY['visits'],p_originals);
 previous:=security._closed_export_context(person);
 SELECT COALESCE(jsonb_agg(to_jsonb(selected)),'[]') INTO result FROM (
  SELECT i.id,i.workspace_id,i.filename,i.file_size,
   CASE WHEN p_originals AND access.original_download_allowed THEN i.file_path ELSE '' END file_path,
   CASE WHEN p_originals AND access.original_download_allowed THEN i.storage_profile END storage_profile,
   CASE WHEN p_originals AND access.original_download_allowed THEN i.storage_driver END storage_driver,
   CASE WHEN p_originals AND access.original_download_allowed THEN i.storage_key END storage_key,i.content_sha256,access.original_download_allowed,
   ARRAY(SELECT v.id FROM activity.visit v WHERE v.workspace_id=i.workspace_id AND v.source_import_id=i.id
    AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived') AND security.authorization_visit('visit.read',v.id)
    ORDER BY v.id) visit_ids
  FROM activity.visit_import i CROSS JOIN LATERAL (SELECT (i.filename !~* '\.(mp3|wav|m4a|aac|ogg|flac|amr|webm)$') AS original_download_allowed) access WHERE i.workspace_id=(person->>'workspace_id')::uuid
  AND EXISTS(SELECT 1 FROM activity.visit v WHERE v.workspace_id=i.workspace_id AND v.source_import_id=i.id
   AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived') AND security.authorization_visit('visit.read',v.id))
  ORDER BY i.id OFFSET p_offset LIMIT p_limit
 ) selected;
 PERFORM security._closed_export_restore(previous);
 PERFORM security.closed_export_authorize(p_session_hash,ARRAY['visits'],p_originals);
 RETURN jsonb_build_object('items',result,'next_offset',CASE WHEN jsonb_array_length(result)=p_limit THEN p_offset+p_limit ELSE NULL END);
END $$;

CREATE OR REPLACE FUNCTION security.closed_export_content_chunk(p_session_hash text,p_import uuid,p_chunk integer)
RETURNS TABLE(chunk_no integer,content bytea)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE person jsonb; previous jsonb; imported activity.visit_import%ROWTYPE;
BEGIN
 IF p_import IS NULL OR p_chunk IS NULL OR p_chunk NOT BETWEEN 0 AND 104857600 THEN
  RAISE EXCEPTION 'CLOSED_EXPORT_INVALID_REQUEST' USING ERRCODE='P7422'; END IF;
 person:=security.closed_export_authorize(p_session_hash,ARRAY['visits'],true);
 previous:=security._closed_export_context(person);
 SELECT * INTO imported FROM activity.visit_import i WHERE i.id=p_import AND i.workspace_id=(person->>'workspace_id')::uuid
 AND (i.filename !~* '\.(mp3|wav|m4a|aac|ogg|flac|amr|webm)$')
 AND i.storage_driver='postgres' AND i.file_size BETWEEN 1 AND 104857600
 AND i.storage_key=i.workspace_id::text||'/'||i.id::text
 AND EXISTS(SELECT 1 FROM activity.visit v WHERE v.workspace_id=i.workspace_id AND v.source_import_id=i.id
  AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived') AND security.authorization_visit('visit.read',v.id));
 IF NOT FOUND THEN RAISE EXCEPTION 'CLOSED_EXPORT_DENIED' USING ERRCODE='P7403'; END IF;
 -- Historical imports may use blocks smaller than 1 MiB. Each nonempty block
 -- consumes at least one registered byte; allow the final EOF probe as well.
 IF p_chunk>imported.file_size THEN RAISE EXCEPTION 'CLOSED_EXPORT_INVALID_REQUEST' USING ERRCODE='P7422'; END IF;
 RETURN QUERY SELECT c.chunk_no,c.content FROM activity.visit_import_content c
 -- Return the next actual index so the shared reader detects a missing block
 -- followed by later/trailing content instead of mistaking the gap for EOF.
 WHERE c.import_id=p_import AND c.workspace_id=imported.workspace_id AND c.chunk_no>=p_chunk
 ORDER BY c.chunk_no LIMIT 1;
 PERFORM security._closed_export_restore(previous);
 PERFORM security.closed_export_authorize(p_session_hash,ARRAY['visits'],true);
END $$;

INSERT INTO ops.schema_migration(version,description) VALUES('V431','Owner-only inline visit audio; block audio downloads and active/closed exports');
COMMIT;
