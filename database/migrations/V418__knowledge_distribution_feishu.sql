BEGIN;
-- One stable material row in the existing Feishu outbox. This migration creates
-- no company connection, remote table, credentials or notification opt-in.
ALTER TABLE ops.feishu_event DROP CONSTRAINT feishu_event_object_kind_check;
ALTER TABLE ops.feishu_event ADD CONSTRAINT feishu_event_object_kind_check CHECK(object_kind IN
 ('customer','opportunity','visit','partner','task','demo_scene','actual','target','member','contact',
  'forecast','period_actual_snapshot','weekly_report','knowledge','refresh'));
ALTER TABLE ops.feishu_event DROP CONSTRAINT feishu_event_notification_snapshot_check;
ALTER TABLE ops.feishu_event ADD CONSTRAINT feishu_event_notification_snapshot_check CHECK (
 notification_snapshot IS NULL OR (
  jsonb_typeof(notification_snapshot)='object'
  AND notification_snapshot->>'schema_version'='1'
  AND notification_snapshot->>'change_id'=id::text
  AND notification_snapshot->>'workspace_id'=workspace_id::text
  AND NOT historical AND queue_origin='business'
  AND ((object_kind='opportunity' AND NOT first_formal_create
    AND notification_snapshot->>'opportunity_id'=object_id::text
    AND notification_snapshot ?& ARRAY['opportunity_name','customer_name','owner_name',
      'department_id','actor_name','changed_at','currency','changes'])
   OR (object_kind='knowledge' AND notification_snapshot->>'material_id'=object_id::text
    AND notification_snapshot->>'publication_id'=id::text
    AND notification_snapshot->>'event_kind' IN ('published','version_published')
    AND first_formal_create=(notification_snapshot->>'event_kind'='published')
    AND notification_snapshot ?& ARRAY['version_id','publication_revision','content_name','version_no']))) IS TRUE);

-- Preserve the source entrypoint OID/owner/ACL. Its private predecessor remains
-- callable only by the definer, not an additional worker entrypoint.
DO $$ DECLARE original_owner name; BEGIN
 SELECT pg_get_userbyid(proowner) INTO original_owner FROM pg_proc WHERE oid='ops.feishu_source(uuid,text,uuid)'::regprocedure;
 EXECUTE replace(pg_get_functiondef('ops.feishu_source(uuid,text,uuid)'::regprocedure),
  'CREATE OR REPLACE FUNCTION ops.feishu_source(', 'CREATE OR REPLACE FUNCTION ops.feishu_source_before_knowledge(');
 EXECUTE format('ALTER FUNCTION ops.feishu_source_before_knowledge(uuid,text,uuid) OWNER TO %I',original_owner);
END $$;
REVOKE ALL ON FUNCTION ops.feishu_source_before_knowledge(uuid,text,uuid) FROM PUBLIC,salegent_feishu_worker;

CREATE OR REPLACE FUNCTION ops.feishu_source(p_connection uuid,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE ws uuid; result jsonb; p knowledge.distribution_publication;
BEGIN
 IF p_kind<>'knowledge' THEN RETURN ops.feishu_source_before_knowledge(p_connection,p_kind,p_id); END IF;
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 IF ws IS NULL OR NOT security.workspace_is_active(ws) OR NOT security.workspace_module_enabled(ws,'feishu') THEN
  RAISE insufficient_privilege; END IF;
 SELECT * INTO p FROM knowledge.distribution_publication
  WHERE workspace_id=ws AND material_id=p_id ORDER BY revision DESC LIMIT 1;
 -- Group visibility must be company-wide with no personal deny. A public usage
 -- tag alone never widens a private material's actual ACL.
 IF p.id IS NULL OR NOT security.kd_publication_live(p.id) OR NOT EXISTS(
  SELECT 1 FROM knowledge.material_acl a WHERE a.workspace_id=ws AND a.material_id=p_id
   AND cardinality(a.denied_user_ids)=0 AND EXISTS(SELECT 1 FROM jsonb_array_elements(a.grants) g
    WHERE g->>'subject_kind'='company' AND g->'actions' ? 'read')) THEN
  RETURN jsonb_build_object('id',p_id,'excluded',true);
 END IF;
 SELECT jsonb_build_object('id',m.id,'content_name',v.contribution->>'content_name','created_at',m.created_at,'updated_at',p.published_at,
  'publication_id',p.id,'publication_revision',p.revision,'version_id',v.id,'version_no',v.version_no,
  'product',v.contribution#>>'{selection,product_code}',
  'category',v.contribution#>>'{selection,content_category}',
  'content_format',(SELECT string_agg(DISTINCT f.media_type,'、') FROM knowledge.version_file vf JOIN knowledge.file f ON f.workspace_id=vf.workspace_id AND f.id=vf.file_id WHERE vf.workspace_id=ws AND vf.version_id=v.id),
  'provider_name',u.display_name,'published_at',p.published_at,'publication_status','已发布',
  'visits',COALESCE(stats.visits,0),'unique_visitors_estimate',COALESCE(stats.visitors,0),
  'previews',COALESCE(stats.previews,0),'download_requests',COALESCE(stats.downloads,0),
  'questionnaire_submissions',(SELECT count(*) FROM knowledge.distribution_response r
    JOIN knowledge.distribution_share ds ON ds.id=r.share_id AND ds.workspace_id=ws
    JOIN knowledge.distribution_publication dp ON dp.id=ds.publication_id AND dp.workspace_id=ws
    WHERE r.workspace_id=ws AND dp.material_id=p_id AND dp.questionnaire->>'enabled'='true')) INTO result
 FROM knowledge.material m JOIN knowledge.version v ON v.workspace_id=m.workspace_id AND v.material_id=m.id AND v.id=p.version_id
 LEFT JOIN platform.user_ref u ON u.workspace_id=m.workspace_id AND u.id=v.contributor_user_id
 LEFT JOIN LATERAL(SELECT count(*) FILTER(WHERE e.kind='visit') visits,
   count(DISTINCT e.browser_hash) FILTER(WHERE e.kind='visit' AND e.browser_hash<>'') visitors,
   count(*) FILTER(WHERE e.kind='preview') previews,count(*) FILTER(WHERE e.kind='download') downloads
   FROM knowledge.distribution_event e JOIN knowledge.distribution_share ds ON ds.id=e.share_id AND ds.workspace_id=ws
   JOIN knowledge.distribution_publication dp ON dp.id=ds.publication_id AND dp.workspace_id=ws
   WHERE e.workspace_id=ws AND dp.material_id=p_id) stats ON true
 WHERE m.workspace_id=ws AND m.id=p_id;
 RETURN COALESCE(result,jsonb_build_object('id',p_id,'excluded',true));
END $$;

CREATE FUNCTION security.knowledge_distribution_enqueue(p_workspace uuid,p_material uuid,p_publication uuid,
 p_version uuid,p_kind text,p_revision integer,p_actor uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE c config.feishu_connection; p knowledge.distribution_publication; previous knowledge.distribution_publication; snapshot jsonb; raw jsonb; n integer; expected_kind text;
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
 SELECT * INTO c FROM config.feishu_connection WHERE workspace_id=p_workspace
  AND settings#>>'{mappings,knowledge,enabled}'='true';
 IF c.id IS NULL OR NOT security.workspace_is_active(p_workspace)
  OR NOT security.workspace_module_enabled(p_workspace,'feishu') THEN RETURN 0; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(c.id::text||':knowledge:'||p_material::text,0));
 -- Publishing/withdrawal must not wait behind the delayed statistics batch.
 UPDATE ops.feishu_event SET available_at=LEAST(available_at,clock_timestamp())
  WHERE connection_id=c.id AND object_kind='knowledge' AND object_id=p_material AND historical AND status='pending';
 IF c.enabled AND p_kind IN ('published','version_published') THEN
  raw:=ops.feishu_source(c.id,'knowledge',p_material);
  IF raw->>'excluded' IS DISTINCT FROM 'true' THEN
   snapshot:=raw||jsonb_build_object('schema_version',1,'change_id',p.id,'workspace_id',p_workspace,
    'material_id',p_material,'publication_id',p.id,'event_kind',p_kind);
  END IF;
 END IF;
 INSERT INTO ops.feishu_event(id,connection_id,workspace_id,object_kind,object_id,first_formal_create,
  historical,queue_origin,notification_snapshot)
 VALUES(p.id,c.id,p_workspace,'knowledge',p_material,p_kind='published',snapshot IS NULL,'business',snapshot)
 ON CONFLICT(id) DO NOTHING;
 GET DIAGNOSTICS n=ROW_COUNT; RETURN n;
END $$;
REVOKE ALL ON FUNCTION security.knowledge_distribution_enqueue(uuid,uuid,uuid,uuid,text,integer,uuid)
 FROM PUBLIC,salegent_feishu_worker;

-- Revocations and source changes repair the directory without announcements.
CREATE FUNCTION ops.capture_knowledge_distribution_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE ws uuid; mid uuid; cid uuid;
BEGIN
 ws:=NEW.workspace_id;
 IF TG_TABLE_NAME='material' THEN mid:=NEW.id; ELSE mid:=NEW.material_id; END IF;
 IF TG_TABLE_NAME='material' THEN
  IF NEW.deleted_at IS NOT DISTINCT FROM OLD.deleted_at AND NEW.latest_version_id IS NOT DISTINCT FROM OLD.latest_version_id
   AND NEW.content_name IS NOT DISTINCT FROM OLD.content_name THEN RETURN NULL; END IF;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM knowledge.distribution_publication WHERE workspace_id=ws AND material_id=mid) THEN RETURN NULL; END IF;
 SELECT id INTO cid FROM config.feishu_connection WHERE workspace_id=ws
  AND settings#>>'{mappings,knowledge,enabled}'='true';
 IF cid IS NULL THEN RETURN NULL; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(cid::text||':knowledge:'||mid::text,0));
 UPDATE ops.feishu_event SET available_at=LEAST(available_at,clock_timestamp())
  WHERE connection_id=cid AND object_kind='knowledge' AND object_id=mid AND historical AND status='pending';
 INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,historical,queue_origin)
 SELECT cid,ws,'knowledge',mid,true,'business' WHERE NOT EXISTS(SELECT 1 FROM ops.feishu_event
  WHERE connection_id=cid AND object_kind='knowledge' AND object_id=mid AND historical AND status='pending');
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION ops.capture_knowledge_distribution_change() FROM PUBLIC,salegent_feishu_worker;
CREATE TRIGGER knowledge_feishu_material AFTER UPDATE ON knowledge.material
 FOR EACH ROW EXECUTE FUNCTION ops.capture_knowledge_distribution_change();
CREATE TRIGGER knowledge_feishu_acl AFTER UPDATE ON knowledge.material_acl
 FOR EACH ROW EXECUTE FUNCTION ops.capture_knowledge_distribution_change();
CREATE TRIGGER knowledge_feishu_version AFTER UPDATE OF state ON knowledge.version
 FOR EACH ROW WHEN(OLD.state IS DISTINCT FROM NEW.state) EXECUTE FUNCTION ops.capture_knowledge_distribution_change();

-- Customer activity schedules only a coalesced silent aggregate refresh. The
-- existing worker processes it after one minute; no full scans or visitor cards.
CREATE FUNCTION ops.capture_knowledge_distribution_statistics() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE mid uuid; cid uuid;
BEGIN
 IF TG_TABLE_NAME='distribution_event' THEN
  IF NEW.kind='cover' THEN RETURN NULL; END IF;
 END IF;
 SELECT p.material_id INTO mid FROM knowledge.distribution_share s
  JOIN knowledge.distribution_publication p ON p.workspace_id=s.workspace_id AND p.id=s.publication_id
  WHERE s.workspace_id=NEW.workspace_id AND s.id=NEW.share_id;
 SELECT id INTO cid FROM config.feishu_connection WHERE workspace_id=NEW.workspace_id
  AND settings#>>'{mappings,knowledge,enabled}'='true';
 IF cid IS NULL OR mid IS NULL THEN RETURN NULL; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(cid::text||':knowledge:'||mid::text,0));
 INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,historical,queue_origin,available_at)
 SELECT cid,NEW.workspace_id,'knowledge',mid,true,'business',clock_timestamp()+interval '60 seconds'
  WHERE NOT EXISTS(SELECT 1 FROM ops.feishu_event WHERE connection_id=cid
   AND object_kind='knowledge' AND object_id=mid AND historical AND status='pending');
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION ops.capture_knowledge_distribution_statistics() FROM PUBLIC,salegent_feishu_worker;
CREATE TRIGGER knowledge_feishu_activity AFTER INSERT ON knowledge.distribution_event
 FOR EACH ROW EXECUTE FUNCTION ops.capture_knowledge_distribution_statistics();
CREATE TRIGGER knowledge_feishu_response AFTER INSERT ON knowledge.distribution_response
 FOR EACH ROW EXECUTE FUNCTION ops.capture_knowledge_distribution_statistics();

-- Extend both existing reconciliation functions with a published-material source.
-- Their authorization gates and force/manual-recovery semantics remain intact.
DO $$ DECLARE signature text; definition text; old_text text:=
 '[''crm.opportunity_period_actual_snapshot'',''period_actual_snapshot'']]';
BEGIN
 FOREACH signature IN ARRAY ARRAY['ops.feishu_reconcile(uuid)','security.feishu_initialize(uuid)'] LOOP
  definition:=pg_get_functiondef(signature::regprocedure);
  IF strpos(definition,old_text)=0 THEN RAISE EXCEPTION 'KNOWLEDGE_FEISHU_RECONCILE_BASELINE_CHANGED'; END IF;
  definition:=replace(definition,old_text,
   '[''crm.opportunity_period_actual_snapshot'',''period_actual_snapshot''],[''knowledge.material'',''knowledge'']]');
  -- Only stable IDs with publication history, never unshared drafts.
  definition:=replace(definition,'FROM %s s WHERE workspace_id=$2 AND NOT EXISTS',
   'FROM %s s WHERE workspace_id=$2 AND ($3<>''knowledge'' OR EXISTS(SELECT 1 FROM knowledge.distribution_publication p WHERE p.workspace_id=$2 AND p.material_id=s.id)) AND NOT EXISTS');
  -- The dynamic statement is itself a SQL string in the function definition.
  definition:=replace(definition,'$3<>''knowledge'' OR EXISTS', '$3<>''''knowledge'''' OR EXISTS');
  EXECUTE definition;
 END LOOP;
END $$;
INSERT INTO ops.schema_migration(version,description) VALUES('V418','Knowledge publication Feishu directory outbox');
COMMIT;
