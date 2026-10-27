BEGIN;
-- One imported source row is one material; source absence never deletes it.
CREATE TABLE knowledge.source_binding (
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 base_id text NOT NULL, table_id text NOT NULL, record_id text NOT NULL,
 material_id uuid NOT NULL,
 source_revision bigint NOT NULL CHECK(source_revision>=0),
 source_hash text NOT NULL CHECK(source_hash ~ '^[0-9a-f]{64}$'),
 imported_material_revision integer NOT NULL,
 imported_acl_revision integer NOT NULL,
 imported_version_id uuid NOT NULL,
 imported_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,base_id,table_id,record_id),
 UNIQUE(workspace_id,material_id),
 FOREIGN KEY(workspace_id,material_id) REFERENCES knowledge.material(workspace_id,id),
 FOREIGN KEY(workspace_id,material_id,imported_version_id) REFERENCES knowledge.version(workspace_id,material_id,id)
);
ALTER TABLE knowledge.source_binding ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.source_binding FORCE ROW LEVEL SECURITY;
REVOKE ALL ON knowledge.source_binding FROM PUBLIC;
CREATE POLICY source_read ON knowledge.source_binding FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.knowledge_can(material_id,'read'));

CREATE FUNCTION security.knowledge_import_content_valid(p jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE s jsonb; a jsonb; l jsonb; k text; limit_length integer; port_text text;
BEGIN
 s:=p->'source';
 IF jsonb_typeof(s) IS DISTINCT FROM 'object'
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(s) x WHERE x NOT IN (
   'provider','base_id','table_id','record_id','source_revision','original_name','product_label','category_label',
   'scope_label','source_status','provider_name','original_content_text','links','attachments','remarks','validation_issues')) THEN RETURN false; END IF;
 FOREACH k IN ARRAY ARRAY['provider','base_id','table_id','record_id','original_name','product_label','category_label',
  'scope_label','source_status','provider_name','original_content_text','remarks'] LOOP
  limit_length:=CASE WHEN k='original_content_text' THEN 12000 WHEN k IN ('original_name','provider_name','remarks')
    THEN 2000 ELSE 300 END;
  IF jsonb_typeof(s->k) IS DISTINCT FROM 'string' OR length(s->>k)>limit_length
   OR ((s->>k) ~ '[[:cntrl:]]' AND k NOT IN ('original_content_text','remarks')) THEN RETURN false; END IF;
  IF k NOT IN ('provider_name','original_content_text','remarks') AND length(btrim(s->>k))=0 THEN RETURN false; END IF;
 END LOOP;
 IF translate(s->>'remarks',E'\n\t','') ~ '[[:cntrl:]]' THEN RETURN false; END IF;
 IF jsonb_typeof(s->'source_revision') IS DISTINCT FROM 'number' OR (s->>'source_revision') !~ '^[0-9]+$'
  OR jsonb_typeof(s->'links') IS DISTINCT FROM 'array' OR jsonb_array_length(s->'links')>100
  OR jsonb_typeof(s->'attachments') IS DISTINCT FROM 'array' OR jsonb_array_length(s->'attachments')>100
  OR jsonb_typeof(s->'validation_issues') IS DISTINCT FROM 'array'
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(s->'validation_issues') v
    WHERE jsonb_typeof(v)<>'string' OR v#>>'{}' NOT IN ('SOURCE_REVIEW_REQUIRED','SOURCE_DISABLED',
     'MATERIAL_REFERENCE_MISSING','INDUSTRY_NOT_MAPPED','CUSTOMER_NOT_MAPPED','REPORTING_ROLES_NOT_MAPPED')) THEN RETURN false; END IF;
 FOR l IN SELECT * FROM jsonb_array_elements(s->'links') LOOP
  IF jsonb_typeof(l)<>'string' OR length(l#>>'{}')>4000
   OR (l#>>'{}') !~ '^https?://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?([/?#][^[:space:]]*)?$'
   OR (l#>>'{}') ~ '[[:cntrl:]]' THEN RETURN false; END IF;
  port_text:=substring(l#>>'{}' FROM '^https?://[^/:?#]+:([0-9]+)');
  IF port_text IS NOT NULL AND port_text::integer NOT BETWEEN 1 AND 65535 THEN RETURN false; END IF;
 END LOOP;
 IF (SELECT count(*)<>count(DISTINCT v) FROM jsonb_array_elements(s->'links') v) THEN RETURN false; END IF;
 FOR a IN SELECT * FROM jsonb_array_elements(s->'attachments') LOOP
  IF jsonb_typeof(a)<>'object' OR jsonb_typeof(a->'name') IS DISTINCT FROM 'string'
   OR length(btrim(a->>'name')) NOT BETWEEN 1 AND 300 OR a->>'name' ~ '[[:cntrl:]]'
   OR jsonb_typeof(a->'source_token') IS DISTINCT FROM 'string' OR a->>'source_token' !~ '^[A-Za-z0-9_-]{1,100}$'
   OR EXISTS(SELECT 1 FROM jsonb_object_keys(a) x WHERE x NOT IN ('name','source_token','size_bytes','media_type'))
   OR (a->'size_bytes' IS NOT NULL AND a->'size_bytes'<>'null'::jsonb
    AND (jsonb_typeof(a->'size_bytes')<>'number' OR a->>'size_bytes' !~ '^[0-9]+$'))
   OR (a->'media_type' IS NOT NULL AND a->'media_type'<>'null'::jsonb
    AND (jsonb_typeof(a->'media_type')<>'string' OR length(a->>'media_type')>200)) THEN RETURN false; END IF;
 END LOOP;
 IF (SELECT count(*)<>count(DISTINCT v->>'source_token') FROM jsonb_array_elements(s->'attachments') v) THEN RETURN false; END IF;
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;

CREATE FUNCTION security.knowledge_import_authorize(p_workspace uuid,p_owner uuid,p_grants jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE g jsonb; subject uuid; action text;
BEGIN
 IF p_workspace IS DISTINCT FROM common.current_workspace_id()
  OR NOT security.knowledge_feature('knowledge.catalog_manage')
  OR NOT security.knowledge_permission('knowledge.read',p_owner,'{}',false)
  OR NOT security.knowledge_permission('knowledge.create',p_owner,'{}',false)
  OR NOT security.knowledge_permission('knowledge.manage',p_owner,'{}',false) THEN
  RAISE EXCEPTION 'KNOWLEDGE_IMPORT_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.user_ref WHERE id=p_owner AND workspace_id=p_workspace
  AND status='active' AND deleted_at IS NULL) THEN
  RAISE EXCEPTION 'KNOWLEDGE_IMPORT_OWNER_INVALID' USING ERRCODE='22023'; END IF;
 IF p_grants IS NULL OR jsonb_typeof(p_grants)<>'array' OR jsonb_array_length(p_grants)>200 THEN
  RAISE EXCEPTION 'KNOWLEDGE_IMPORT_ACL_INVALID' USING ERRCODE='22023'; END IF;
 IF (SELECT count(*)<>count(DISTINCT (entry->>'subject_kind',entry->>'subject_id'))
  FROM jsonb_array_elements(p_grants) entry) THEN
  RAISE EXCEPTION 'KNOWLEDGE_IMPORT_ACL_INVALID' USING ERRCODE='22023'; END IF;
 FOR g IN SELECT * FROM jsonb_array_elements(p_grants) LOOP
  IF jsonb_typeof(g)<>'object' OR COALESCE(g->>'subject_kind','') NOT IN ('user','team','company')
   OR jsonb_typeof(g->'actions') IS DISTINCT FROM 'array' OR jsonb_array_length(g->'actions')=0
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(g->'actions') a
    WHERE jsonb_typeof(a)<>'string' OR a#>>'{}' NOT IN ('read','edit','manage'))
   OR EXISTS(SELECT 1 FROM jsonb_object_keys(g) k WHERE k NOT IN ('subject_kind','subject_id','actions')) THEN
   RAISE EXCEPTION 'KNOWLEDGE_IMPORT_ACL_INVALID' USING ERRCODE='22023'; END IF;
  subject:=(g->>'subject_id')::uuid;
  IF g->>'subject_kind'='company' THEN
   IF subject IS NOT NULL THEN RAISE EXCEPTION 'KNOWLEDGE_IMPORT_ACL_INVALID' USING ERRCODE='22023'; END IF;
  ELSIF g->>'subject_kind'='user' THEN
   IF NOT EXISTS(SELECT 1 FROM platform.user_ref WHERE workspace_id=p_workspace AND id=subject
    AND status='active' AND deleted_at IS NULL) THEN
    RAISE EXCEPTION 'KNOWLEDGE_IMPORT_ACL_INVALID' USING ERRCODE='22023'; END IF;
  ELSIF NOT EXISTS(SELECT 1 FROM platform.team WHERE workspace_id=p_workspace AND id=subject
   AND status='active' AND deleted_at IS NULL AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to) THEN
   RAISE EXCEPTION 'KNOWLEDGE_IMPORT_ACL_INVALID' USING ERRCODE='22023'; END IF;
 END LOOP;
 -- The import actor must also be able to inspect/manage the resulting objects.
 -- Reject this during dry-run rather than inventing an implicit extra ACL grant.
 IF p_owner<>common.current_user_ref_id() THEN
  FOREACH action IN ARRAY ARRAY['read','manage'] LOOP
   IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_grants) entry
    WHERE entry->'actions' ? action AND (
     entry->>'subject_kind'='company'
     OR (entry->>'subject_kind'='user' AND (entry->>'subject_id')::uuid=common.current_user_ref_id())
     OR (entry->>'subject_kind'='team' AND (entry->>'subject_id')::uuid=ANY(
       security.knowledge_current_teams(common.current_user_ref_id()))))) THEN
    RAISE EXCEPTION 'KNOWLEDGE_IMPORT_ACTOR_ACL_REQUIRED' USING ERRCODE='42501'; END IF;
  END LOOP;
 END IF;
END $$;

CREATE FUNCTION security.knowledge_import_record(p_workspace uuid,p_owner uuid,p_grants jsonb,
 p_base text,p_table text,p_record text,p_source_revision bigint,p_content jsonb,p_dry_run boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE b knowledge.source_binding; m knowledge.material; a knowledge.material_acl;
 mid uuid; vid uuid; seq integer; material_rev integer; outcome text; issue text; content_hash text; state text;
 s jsonb; sel jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('knowledge:'||common.current_workspace_id()::text,0));
 PERFORM security.knowledge_import_authorize(p_workspace,p_owner,p_grants);
 s:=p_content->'source'; sel:=p_content->'selection';
 IF p_base IS NULL OR p_base !~ '^[A-Za-z0-9_-]{1,100}$' OR p_table IS NULL OR p_table !~ '^tbl[A-Za-z0-9]+$'
  OR p_record IS NULL OR p_record !~ '^[A-Za-z0-9_-]{1,100}$' OR p_source_revision IS NULL OR p_source_revision<0
  OR p_dry_run IS NULL OR jsonb_typeof(p_content) IS DISTINCT FROM 'object'
  OR p_content->>'kind' IS DISTINCT FROM 'legacy_import'
  OR p_content->>'company_id' IS DISTINCT FROM p_workspace::text
  OR jsonb_typeof(p_content->'content_name') IS DISTINCT FROM 'string'
  OR length(btrim(p_content->>'content_name')) NOT BETWEEN 1 AND 2000
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_content) k WHERE k NOT IN ('kind','company_id','content_name','selection','source'))
  OR jsonb_typeof(s) IS DISTINCT FROM 'object' OR jsonb_typeof(sel) IS DISTINCT FROM 'object'
  OR s->>'provider' IS DISTINCT FROM 'feishu_base' OR s->>'base_id' IS DISTINCT FROM p_base
  OR s->>'table_id' IS DISTINCT FROM p_table OR s->>'record_id' IS DISTINCT FROM p_record
  OR (s->>'source_revision')::bigint IS DISTINCT FROM p_source_revision
  OR s->>'original_name' IS DISTINCT FROM p_content->>'content_name'
  OR COALESCE(s->>'source_status','') NOT IN ('可用','待复核','已停用')
  OR jsonb_typeof(s->'validation_issues') IS DISTINCT FROM 'array'
  OR jsonb_typeof(s->'links') IS DISTINCT FROM 'array' OR jsonb_typeof(s->'attachments') IS DISTINCT FROM 'array'
  OR COALESCE(sel->>'product_code','') !~ '^legacy_[a-f0-9]{24}$'
  OR COALESCE(sel->>'content_category','') NOT IN ('overall_solution','product_intro','industry_solution','customer_solution',
   'sales_support','training_assessment','conference_speech')
  OR COALESCE(sel->>'content_scope','') NOT IN ('internal','customer','partner','public')
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(sel) k WHERE k NOT IN ('product_code','content_category','content_scope'))
  OR NOT security.knowledge_import_content_valid(p_content) THEN
  RAISE EXCEPTION 'KNOWLEDGE_IMPORT_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 -- Revision-only changes do not create another version or increase directory totals.
 content_hash:=encode(sha256(convert_to((p_content #- '{source,source_revision}')::text,'UTF8')),'hex');
 SELECT * INTO b FROM knowledge.source_binding WHERE workspace_id=p_workspace AND base_id=p_base
  AND table_id=p_table AND record_id=p_record FOR UPDATE;
 IF FOUND THEN
  mid:=b.material_id;
  SELECT * INTO m FROM knowledge.material WHERE workspace_id=p_workspace AND id=mid FOR UPDATE;
  SELECT * INTO a FROM knowledge.material_acl WHERE workspace_id=p_workspace AND material_id=mid FOR UPDATE;
  vid:=m.latest_version_id; material_rev:=m.revision;
  SELECT version_no INTO seq FROM knowledge.version WHERE workspace_id=p_workspace AND id=vid;
  IF NOT security.knowledge_can(mid,'read') OR NOT security.knowledge_can(mid,'manage') THEN
   RAISE EXCEPTION 'KNOWLEDGE_IMPORT_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  IF m.deleted_at IS NOT NULL OR m.revision<>b.imported_material_revision
   OR m.latest_version_id<>b.imported_version_id OR a.revision<>b.imported_acl_revision THEN
   outcome:='conflict'; issue:='LOCAL_CHANGES';
  ELSIF a.owner_user_id<>p_owner OR a.grants<>p_grants THEN
   outcome:='conflict'; issue:='IMPORT_ACL_CHANGED';
  ELSIF p_source_revision<b.source_revision THEN outcome:='conflict'; issue:='SOURCE_REVISION_OLDER';
  ELSIF p_source_revision=b.source_revision AND content_hash<>b.source_hash THEN
   outcome:='conflict'; issue:='SOURCE_REVISION_REUSED';
  ELSIF content_hash=b.source_hash THEN outcome:='unchanged';
  ELSE outcome:='updated'; END IF;
 ELSE outcome:='created'; END IF;
 IF NOT p_dry_run AND outcome IN ('created','updated') THEN
  state:='saved'; -- Imported references have not undergone parsing; issues are independent metadata.
  IF outcome='created' THEN
   mid:=gen_random_uuid(); material_rev:=1; seq:=1;
   INSERT INTO knowledge.material(workspace_id,id,created_by,content_name)
    VALUES(p_workspace,mid,common.current_user_ref_id(),p_content->>'content_name');
   INSERT INTO knowledge.material_acl(workspace_id,material_id,owner_user_id,grants)
    VALUES(p_workspace,mid,p_owner,p_grants);
  ELSE material_rev:=m.revision+1; seq:=seq+1; END IF;
  vid:=gen_random_uuid();
  INSERT INTO knowledge.version(workspace_id,material_id,id,version_no,contribution,state,contributor_user_id)
   VALUES(p_workspace,mid,vid,seq,p_content,state,common.current_user_ref_id());
  UPDATE knowledge.material SET latest_version_id=vid,active_version_id=NULL,revision=material_rev,
   content_name=p_content->>'content_name',updated_at=clock_timestamp() WHERE workspace_id=p_workspace AND id=mid;
  INSERT INTO knowledge.source_binding(workspace_id,base_id,table_id,record_id,material_id,source_revision,
   source_hash,imported_material_revision,imported_acl_revision,imported_version_id)
   VALUES(p_workspace,p_base,p_table,p_record,mid,p_source_revision,content_hash,material_rev,1,vid)
  ON CONFLICT(workspace_id,base_id,table_id,record_id) DO UPDATE SET
   source_revision=EXCLUDED.source_revision,source_hash=EXCLUDED.source_hash,
   imported_material_revision=EXCLUDED.imported_material_revision,imported_version_id=EXCLUDED.imported_version_id,
   imported_at=clock_timestamp();
  INSERT INTO knowledge.audit(workspace_id,actor_id,material_id,operation,detail)
   VALUES(p_workspace,common.current_user_ref_id(),mid,'source.import',jsonb_build_object(
    'base_id',p_base,'table_id',p_table,'record_id',p_record,'source_revision',p_source_revision,
    'version_id',vid,'outcome',outcome));
 ELSIF NOT p_dry_run AND outcome='unchanged' AND p_source_revision>b.source_revision THEN
  UPDATE knowledge.source_binding SET source_revision=p_source_revision,imported_at=clock_timestamp()
   WHERE workspace_id=p_workspace AND base_id=p_base AND table_id=p_table AND record_id=p_record;
 END IF;
 RETURN jsonb_build_object('record_id',p_record,'outcome',outcome,'material_id',mid,
  'material_revision',material_rev,'version_id',vid,'version_no',seq,
  'validation_issues',s->'validation_issues','conflict_code',issue);
END $$;
REVOKE ALL ON FUNCTION security.knowledge_import_authorize(uuid,uuid,jsonb),
 security.knowledge_import_record(uuid,uuid,jsonb,text,text,text,bigint,jsonb,boolean),
 security.knowledge_import_content_valid(jsonb) FROM PUBLIC;
INSERT INTO ops.schema_migration(version,description) VALUES('V415','Explicit idempotent Feishu knowledge directory source import');
COMMIT;
