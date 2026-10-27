BEGIN;
-- Explicit publication is independent of internal ACL and provider retrieval state.
CREATE TABLE knowledge.distribution_publication (
 workspace_id uuid NOT NULL, id uuid PRIMARY KEY DEFAULT gen_random_uuid(), material_id uuid NOT NULL,
 version_id uuid NOT NULL, revision integer NOT NULL CHECK(revision>0), enabled boolean NOT NULL,
 description text NOT NULL, cover_file_id uuid, allow_preview boolean NOT NULL, allow_download boolean NOT NULL,
 questionnaire jsonb NOT NULL, published_by uuid NOT NULL REFERENCES platform.user_ref(id),
 published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(workspace_id,material_id,revision), UNIQUE(workspace_id,id),
 FOREIGN KEY(workspace_id,material_id,version_id) REFERENCES knowledge.version(workspace_id,material_id,id),
 FOREIGN KEY(workspace_id,cover_file_id) REFERENCES knowledge.file(workspace_id,id)
);
CREATE UNIQUE INDEX distribution_one_enabled ON knowledge.distribution_publication(workspace_id,material_id) WHERE enabled;
CREATE TABLE knowledge.distribution_share (
 workspace_id uuid NOT NULL, id uuid PRIMARY KEY DEFAULT gen_random_uuid(), publication_id uuid NOT NULL,
 sales_user_id uuid NOT NULL REFERENCES platform.user_ref(id), token_hash text NOT NULL UNIQUE CHECK(length(token_hash)=64),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), revoked_at timestamptz,
 FOREIGN KEY(workspace_id,publication_id) REFERENCES knowledge.distribution_publication(workspace_id,id)
);
CREATE TABLE knowledge.distribution_response (
 workspace_id uuid NOT NULL, id uuid PRIMARY KEY DEFAULT gen_random_uuid(), share_id uuid NOT NULL REFERENCES knowledge.distribution_share(id),
 browser_hash text NOT NULL CHECK(length(browser_hash)=64), request_key uuid NOT NULL, request_digest text NOT NULL CHECK(length(request_digest)=64),
 answers jsonb NOT NULL, grant_hash text NOT NULL UNIQUE CHECK(length(grant_hash)=64), receipt jsonb NOT NULL,
 submitted_at timestamptz NOT NULL DEFAULT clock_timestamp(), expires_at timestamptz NOT NULL,
 UNIQUE(share_id,request_key)
);
CREATE TABLE knowledge.distribution_event (
 workspace_id uuid NOT NULL, id uuid PRIMARY KEY DEFAULT gen_random_uuid(), share_id uuid NOT NULL REFERENCES knowledge.distribution_share(id),
 browser_hash text NOT NULL CHECK(length(browser_hash) IN (0,64)), kind text NOT NULL CHECK(kind IN ('visit','preview','download','cover')),
 occurred_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX distribution_event_time ON knowledge.distribution_event(share_id,occurred_at);
CREATE INDEX distribution_response_time ON knowledge.distribution_response(share_id,submitted_at);

CREATE FUNCTION security.kd_admin() RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog SET row_security=off AS $$
 SELECT security.knowledge_feature('knowledge.catalog_manage') AND security.knowledge_feature('knowledge.read');
$$;
CREATE FUNCTION security.kd_publication_live(p_id uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog SET row_security=off AS $$
 SELECT EXISTS(SELECT 1 FROM knowledge.distribution_publication p
 JOIN knowledge.material m ON m.workspace_id=p.workspace_id AND m.id=p.material_id
 JOIN knowledge.version v ON v.workspace_id=p.workspace_id AND v.id=p.version_id
 JOIN knowledge.version latest ON latest.workspace_id=m.workspace_id AND latest.id=m.latest_version_id
 WHERE p.id=p_id AND p.enabled AND m.deleted_at IS NULL AND v.state<>'deleted' AND latest.state<>'deleted'
 AND v.contribution->'selection'->>'content_scope' IN ('public','customer')
 AND latest.contribution->'selection'->>'content_scope' IN ('public','customer')
 AND security.workspace_is_active(p.workspace_id)
 AND security.tenant_permission_enabled(p.workspace_id,'knowledge.read')
 AND EXISTS(SELECT 1 FROM knowledge.version_file vf JOIN knowledge.file f ON f.workspace_id=vf.workspace_id AND f.id=vf.file_id
 WHERE vf.workspace_id=p.workspace_id AND vf.version_id=p.version_id));
$$;
-- This private helper temporarily resolves ONLY the tenant and actor stored on a
-- validated share; no caller can provide a tenant, actor, role, or ACL override.
CREATE FUNCTION security.kd_share_live(p_id uuid) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog SET row_security=off AS $$
DECLARE s knowledge.distribution_share; p knowledge.distribution_publication; ok boolean;
 old_w text:=current_setting('app.workspace_id',true); old_u text:=current_setting('app.user_ref_id',true);
BEGIN
 SELECT * INTO s FROM knowledge.distribution_share WHERE id=p_id;
 IF NOT FOUND OR s.revoked_at IS NOT NULL OR NOT security.kd_publication_live(s.publication_id) THEN RETURN false; END IF;
 SELECT * INTO p FROM knowledge.distribution_publication WHERE id=s.publication_id;
 PERFORM set_config('app.workspace_id',s.workspace_id::text,true),set_config('app.user_ref_id',s.sales_user_id::text,true);
 ok:=security.knowledge_can(p.material_id,'read');
 PERFORM set_config('app.workspace_id',COALESCE(old_w,''),true),set_config('app.user_ref_id',COALESCE(old_u,''),true);
 RETURN ok;
END $$;
CREATE FUNCTION security.kd_files(p_id uuid) RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog SET row_security=off AS $$
 SELECT COALESCE(jsonb_agg(jsonb_build_object('file_id',f.id,'filename',f.filename,'media_type',f.media_type,
 'size_bytes',f.size_bytes,'preview_supported',f.media_type IN ('text/plain','text/markdown','text/csv')) ORDER BY vf.ordinal),'[]')
 FROM knowledge.distribution_publication p JOIN knowledge.version_file vf ON vf.workspace_id=p.workspace_id AND vf.version_id=p.version_id
 JOIN knowledge.file f ON f.workspace_id=vf.workspace_id AND f.id=vf.file_id WHERE p.id=p_id;
$$;
CREATE FUNCTION security.kd_sales_name(p_share uuid) RETURNS text LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog SET row_security=off AS $$
 SELECT u.display_name FROM knowledge.distribution_share s JOIN platform.user_ref u ON u.workspace_id=s.workspace_id AND u.id=s.sales_user_id
 WHERE s.id=p_share AND s.workspace_id=common.current_workspace_id() AND security.knowledge_feature('knowledge.read')
 AND (s.sales_user_id=common.current_user_ref_id() OR security.kd_admin());
$$;
CREATE FUNCTION security.kd_publish(p_material uuid,p_body jsonb) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
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
 OR NOT EXISTS(SELECT 1 FROM knowledge.version_file WHERE workspace_id=v.workspace_id AND version_id=v.id)) THEN
 RAISE EXCEPTION 'KNOWLEDGE_DISTRIBUTION_CONTENT_REQUIRED' USING ERRCODE='22023'; END IF;
 IF (p_body->>'enabled')::boolean THEN
 IF NOT EXISTS(SELECT 1 FROM knowledge.material m JOIN knowledge.version latest ON latest.workspace_id=m.workspace_id AND latest.id=m.latest_version_id
 WHERE m.id=p_material AND m.workspace_id=v.workspace_id AND latest.state<>'deleted'
 AND latest.contribution->'selection'->>'content_scope' IN ('public','customer')) THEN
 RAISE EXCEPTION 'KNOWLEDGE_DISTRIBUTION_CONTENT_REQUIRED' USING ERRCODE='22023'; END IF;
 IF (p_body->>'allow_preview')::boolean AND NOT (p_body->>'allow_download')::boolean THEN
 FOR candidate IN SELECT k.* FROM knowledge.file k JOIN knowledge.version_file vf ON vf.workspace_id=k.workspace_id AND vf.file_id=k.id
 WHERE vf.workspace_id=v.workspace_id AND vf.version_id=v.id AND k.media_type IN ('text/plain','text/markdown','text/csv') LOOP
 BEGIN PERFORM convert_from(candidate.content,'UTF8'); previewable:=true;
 EXCEPTION WHEN character_not_in_repertoire OR untranslatable_character THEN NULL; END;
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
 IF cover IS NOT NULL AND NOT EXISTS(SELECT 1 FROM knowledge.file WHERE workspace_id=v.workspace_id AND id=cover
 AND media_type IN ('image/png','image/jpeg','image/webp') AND size_bytes<=2097152 AND security.knowledge_file_read(id)) THEN
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
CREATE FUNCTION security.kd_publication_visible(p_id uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog SET row_security=off AS $$
 SELECT EXISTS(SELECT 1 FROM knowledge.distribution_publication p WHERE p.id=p_id AND p.workspace_id=common.current_workspace_id()
 AND security.knowledge_can(p.material_id,'read') AND security.kd_publication_live(p.id));
$$;
CREATE FUNCTION security.kd_create_share(p_material uuid,p_publication uuid,p_hash text) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog SET row_security=off AS $$
DECLARE result uuid;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('knowledge:'||common.current_workspace_id()::text,0));
 IF NOT security.knowledge_can(p_material,'read') OR NOT EXISTS(SELECT 1 FROM knowledge.distribution_publication WHERE id=p_publication
 AND material_id=p_material AND workspace_id=common.current_workspace_id()) OR NOT security.kd_publication_live(p_publication) THEN
 RAISE EXCEPTION 'KNOWLEDGE_SHARE_UNAVAILABLE' USING ERRCODE='P0002'; END IF;
 INSERT INTO knowledge.distribution_share(workspace_id,publication_id,sales_user_id,token_hash)
 VALUES(common.current_workspace_id(),p_publication,common.current_user_ref_id(),p_hash) RETURNING id INTO result;
 RETURN result;
END $$;
CREATE FUNCTION security.kd_revoke(p_share uuid,p_expected boolean) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog SET row_security=off AS $$
DECLARE s knowledge.distribution_share;
BEGIN
 SELECT * INTO s FROM knowledge.distribution_share WHERE id=p_share AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF NOT FOUND OR NOT security.knowledge_feature('knowledge.read') OR NOT (s.sales_user_id=common.current_user_ref_id() OR security.kd_admin()) THEN
 RAISE EXCEPTION 'KNOWLEDGE_DISTRIBUTION_DENIED' USING ERRCODE='42501'; END IF;
 IF (s.revoked_at IS NOT NULL) IS DISTINCT FROM p_expected THEN RAISE EXCEPTION 'KNOWLEDGE_PUBLICATION_CONFLICT' USING ERRCODE='40001'; END IF;
 UPDATE knowledge.distribution_share SET revoked_at=COALESCE(revoked_at,clock_timestamp()) WHERE id=p_share;
 RETURN p_share;
END $$;
-- The public gateway is the ONLY anonymous database capability. It never grants
-- table reads, accepts identity context, returns source URLs, or exposes raw answers.
CREATE FUNCTION security.kd_public_metadata(p_hash text,p_browser text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog SET row_security=off AS $$
DECLARE s knowledge.distribution_share; p knowledge.distribution_publication; v knowledge.version; n integer;
BEGIN
 SELECT * INTO s FROM knowledge.distribution_share WHERE token_hash=p_hash;
 IF NOT FOUND OR NOT security.kd_share_live(s.id) THEN RAISE EXCEPTION 'KNOWLEDGE_SHARE_UNAVAILABLE' USING ERRCODE='P0002'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('kd-public:'||s.id::text,0));
 IF NOT security.kd_share_live(s.id) THEN RAISE EXCEPTION 'KNOWLEDGE_SHARE_UNAVAILABLE' USING ERRCODE='P0002'; END IF;
 SELECT count(*) INTO n FROM knowledge.distribution_event WHERE share_id=s.id AND kind='visit' AND occurred_at>clock_timestamp()-interval '1 hour';
 IF n>=10000 OR (SELECT count(*) FROM knowledge.distribution_event WHERE share_id=s.id AND browser_hash=p_browser
 AND kind='visit' AND occurred_at>clock_timestamp()-interval '1 hour')>=300 THEN
 RAISE EXCEPTION 'KNOWLEDGE_PUBLIC_RATE_LIMIT' USING ERRCODE='P0429'; END IF;
 SELECT * INTO p FROM knowledge.distribution_publication WHERE id=s.publication_id;
 SELECT * INTO v FROM knowledge.version WHERE id=p.version_id;
 INSERT INTO knowledge.distribution_event(workspace_id,share_id,browser_hash,kind) VALUES(s.workspace_id,s.id,p_browser,'visit');
 RETURN jsonb_build_object('content_name',v.contribution->>'content_name','description',p.description,'version_no',v.version_no,
 'publication_id',p.id,'questionnaire_version',p.revision,'questionnaire',p.questionnaire,'allow_preview',p.allow_preview,
 'allow_download',p.allow_download,'files',security.kd_files(p.id),'has_cover',p.cover_file_id IS NOT NULL,
 'requires_questionnaire',(p.questionnaire->>'enabled')::boolean);
END $$;
CREATE FUNCTION security.kd_public_response(p_hash text,p_browser text,p_key uuid,p_digest text,p_revision integer,p_answers jsonb,p_token text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE s knowledge.distribution_share; p knowledge.distribution_publication; r knowledge.distribution_response; f jsonb; receipt jsonb; rid uuid:=gen_random_uuid(); expiry timestamptz:=clock_timestamp()+interval '24 hours';
BEGIN
 SELECT * INTO s FROM knowledge.distribution_share WHERE token_hash=p_hash;
 IF NOT FOUND OR NOT security.kd_share_live(s.id) THEN RAISE EXCEPTION 'KNOWLEDGE_SHARE_UNAVAILABLE' USING ERRCODE='P0002'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('kd-public:'||s.id::text,0));
 IF NOT security.kd_share_live(s.id) THEN RAISE EXCEPTION 'KNOWLEDGE_SHARE_UNAVAILABLE' USING ERRCODE='P0002'; END IF;
 SELECT * INTO p FROM knowledge.distribution_publication WHERE id=s.publication_id;
 IF p_revision IS DISTINCT FROM p.revision THEN RAISE EXCEPTION 'KNOWLEDGE_QUESTIONNAIRE_CHANGED' USING ERRCODE='40001'; END IF;
 SELECT * INTO r FROM knowledge.distribution_response WHERE share_id=s.id AND request_key=p_key;
 IF FOUND THEN
 IF r.request_digest IS DISTINCT FROM p_digest OR r.browser_hash IS DISTINCT FROM p_browser THEN
 RAISE EXCEPTION 'KNOWLEDGE_IDEMPOTENCY_CONFLICT' USING ERRCODE='40001'; END IF;
 IF r.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'KNOWLEDGE_GRANT_EXPIRED' USING ERRCODE='42501'; END IF;
 RETURN r.receipt; END IF;
 IF jsonb_typeof(p_answers) IS DISTINCT FROM 'object' OR EXISTS(SELECT 1 FROM jsonb_each(p_answers) a
 WHERE a.key NOT IN ('name','company','contact','contact_intent') OR jsonb_typeof(a.value)<>'string' OR length(a.value#>>'{}')>500) THEN
 RAISE EXCEPTION 'KNOWLEDGE_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 FOR f IN SELECT * FROM jsonb_array_elements(p.questionnaire->'fields') LOOP
 IF (p.questionnaire->>'enabled')::boolean AND (f->>'enabled')::boolean AND (f->>'required')::boolean
 AND length(btrim(COALESCE(p_answers->>(f->>'name'),'')))=0 THEN
 RAISE EXCEPTION 'KNOWLEDGE_QUESTIONNAIRE_REQUIRED' USING ERRCODE='42501'; END IF;
 IF (NOT (p.questionnaire->>'enabled')::boolean OR NOT (f->>'enabled')::boolean) AND length(COALESCE(p_answers->>(f->>'name'),''))>0 THEN
 RAISE EXCEPTION 'KNOWLEDGE_INPUT_INVALID' USING ERRCODE='22023'; END IF; END LOOP;
 IF (SELECT count(*) FROM knowledge.distribution_response WHERE share_id=s.id AND submitted_at>clock_timestamp()-interval '1 hour')>=2000
 OR (SELECT count(*) FROM knowledge.distribution_response WHERE share_id=s.id AND browser_hash=p_browser AND submitted_at>clock_timestamp()-interval '1 hour')>=20 THEN
 RAISE EXCEPTION 'KNOWLEDGE_PUBLIC_RATE_LIMIT' USING ERRCODE='P0429'; END IF;
 receipt:=jsonb_build_object('response_id',rid,'grant_token',p_token,'expires_at',expiry,'publication_id',p.id,'questionnaire_version',p.revision);
 INSERT INTO knowledge.distribution_response(workspace_id,id,share_id,browser_hash,request_key,request_digest,answers,grant_hash,receipt,expires_at)
 VALUES(s.workspace_id,rid,s.id,p_browser,p_key,p_digest,p_answers,encode(sha256(convert_to(p_token,'UTF8')),'hex'),receipt,expiry);
 RETURN receipt;
END $$;
CREATE FUNCTION security.kd_public_file(p_hash text,p_grant text,p_file uuid,p_mode text)
RETURNS TABLE(filename text,media_type text,content bytea) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE s knowledge.distribution_share; p knowledge.distribution_publication; r knowledge.distribution_response; f knowledge.file;
BEGIN
 SELECT * INTO s FROM knowledge.distribution_share WHERE token_hash=p_hash;
 IF NOT FOUND OR NOT security.kd_share_live(s.id) THEN RAISE EXCEPTION 'KNOWLEDGE_SHARE_UNAVAILABLE' USING ERRCODE='P0002'; END IF;
 SELECT * INTO p FROM knowledge.distribution_publication WHERE id=s.publication_id;
 IF p_mode='cover' THEN
 SELECT k.* INTO f FROM knowledge.file k WHERE k.id=p.cover_file_id AND k.workspace_id=p.workspace_id
 AND k.media_type IN ('image/png','image/jpeg','image/webp');
 ELSE
 SELECT * INTO r FROM knowledge.distribution_response WHERE share_id=s.id AND workspace_id=s.workspace_id AND grant_hash=p_grant AND expires_at>clock_timestamp();
 IF NOT FOUND THEN RAISE EXCEPTION 'KNOWLEDGE_QUESTIONNAIRE_REQUIRED' USING ERRCODE='42501'; END IF;
 IF (p_mode='preview' AND NOT p.allow_preview) OR (p_mode='download' AND NOT p.allow_download) OR p_mode NOT IN ('preview','download') THEN
 RAISE EXCEPTION 'KNOWLEDGE_DISTRIBUTION_DENIED' USING ERRCODE='42501'; END IF;
 SELECT k.* INTO f FROM knowledge.file k JOIN knowledge.version_file vf ON vf.workspace_id=k.workspace_id AND vf.file_id=k.id
 WHERE vf.workspace_id=p.workspace_id AND vf.version_id=p.version_id AND k.id=p_file;
 END IF;
 IF f.id IS NULL THEN RAISE EXCEPTION 'KNOWLEDGE_FILE_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF p_mode='preview' THEN
 IF f.media_type NOT IN ('text/plain','text/markdown','text/csv') THEN RAISE EXCEPTION 'KNOWLEDGE_PREVIEW_UNSUPPORTED' USING ERRCODE='P0415'; END IF;
 BEGIN PERFORM convert_from(f.content,'UTF8'); EXCEPTION WHEN character_not_in_repertoire OR untranslatable_character THEN
 RAISE EXCEPTION 'KNOWLEDGE_PREVIEW_UNSUPPORTED' USING ERRCODE='P0415'; END;
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('kd-public:'||s.id::text,0));
 IF NOT security.kd_share_live(s.id) THEN RAISE EXCEPTION 'KNOWLEDGE_SHARE_UNAVAILABLE' USING ERRCODE='P0002'; END IF;
 IF (SELECT count(*) FROM knowledge.distribution_event WHERE share_id=s.id AND kind<>'visit' AND occurred_at>clock_timestamp()-interval '1 hour')>=10000 THEN
 RAISE EXCEPTION 'KNOWLEDGE_PUBLIC_RATE_LIMIT' USING ERRCODE='P0429'; END IF;
 INSERT INTO knowledge.distribution_event(workspace_id,share_id,browser_hash,kind) VALUES(s.workspace_id,s.id,COALESCE(r.browser_hash,''),p_mode);
 RETURN QUERY SELECT f.filename,f.media_type,f.content;
END $$;

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['distribution_publication','distribution_share','distribution_response','distribution_event'] LOOP
 EXECUTE format('ALTER TABLE knowledge.%I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE knowledge.%I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('REVOKE ALL ON knowledge.%I FROM PUBLIC',t);
 END LOOP; END $$;
CREATE POLICY distribution_publication_read ON knowledge.distribution_publication FOR SELECT USING(workspace_id=common.current_workspace_id() AND security.knowledge_can(material_id,'read'));
CREATE POLICY distribution_share_read ON knowledge.distribution_share FOR SELECT USING(workspace_id=common.current_workspace_id() AND security.knowledge_feature('knowledge.read') AND (sales_user_id=common.current_user_ref_id() OR security.kd_admin()));
CREATE POLICY distribution_response_read ON knowledge.distribution_response FOR SELECT USING(workspace_id=common.current_workspace_id() AND EXISTS(SELECT 1 FROM knowledge.distribution_share s WHERE s.id=share_id));
CREATE POLICY distribution_event_read ON knowledge.distribution_event FOR SELECT USING(workspace_id=common.current_workspace_id() AND EXISTS(SELECT 1 FROM knowledge.distribution_share s WHERE s.id=share_id));
REVOKE ALL ON FUNCTION security.kd_admin(),security.kd_publication_live(uuid),security.kd_share_live(uuid),security.kd_files(uuid),
 security.kd_sales_name(uuid),security.kd_publication_visible(uuid),security.kd_publish(uuid,jsonb),security.kd_create_share(uuid,uuid,text),security.kd_revoke(uuid,boolean),
 security.kd_public_metadata(text,text),security.kd_public_response(text,text,uuid,text,integer,jsonb,text),security.kd_public_file(text,text,uuid,text) FROM PUBLIC;
INSERT INTO ops.schema_migration(version,description) VALUES('V417','Explicit knowledge customer publications, questionnaire grants and request analytics');
COMMIT;
