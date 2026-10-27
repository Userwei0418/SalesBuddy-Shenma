BEGIN;
-- Backend-owned registration: a separate product module, explicitly disabled.
-- No role defaults or existing tenant grants are expanded by this migration.
INSERT INTO control.module_catalog(code,label,required,default_enabled)
 VALUES('knowledge','知识库',false,false);
INSERT INTO config.permission_catalog(code,label,module,scopes,sensitive) VALUES
 ('knowledge.read','查看知识材料','知识库',ARRAY['self','assigned','teams','workspace'],false),
 ('knowledge.create','贡献知识材料','知识库',ARRAY['self','assigned','teams','workspace'],false),
 ('knowledge.edit','更新知识材料版本','知识库',ARRAY['self','assigned','teams','workspace'],false),
 ('knowledge.manage','管理知识材料权限','知识库',ARRAY['self','assigned','teams','workspace'],true),
 ('knowledge.catalog_manage','维护知识库分类目录','知识库',ARRAY['workspace'],true);
INSERT INTO control.permission_module(permission_code,module_code)
 SELECT code,'knowledge' FROM config.permission_catalog WHERE code LIKE 'knowledge.%';
INSERT INTO control.tenant_module(workspace_id,module_code,enabled,expires_at,source)
 SELECT id,'knowledge',false,NULL,'migration-v258-default-off' FROM platform.workspace;
-- Internal persistence and object authorization remain the Agent-owned G2 SQL.
CREATE SCHEMA knowledge;
REVOKE ALL ON SCHEMA knowledge FROM PUBLIC;

CREATE TABLE knowledge.catalog (
 workspace_id uuid PRIMARY KEY REFERENCES platform.workspace(id),
 revision integer NOT NULL CHECK(revision>0),
 value jsonb NOT NULL CHECK(jsonb_typeof(value)='object'),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE knowledge.material (
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 id uuid NOT NULL DEFAULT gen_random_uuid(),
 created_by uuid NOT NULL REFERENCES platform.user_ref(id),
 content_name text NOT NULL CHECK(length(content_name) BETWEEN 1 AND 2000),
 revision integer NOT NULL DEFAULT 1 CHECK(revision>0),
 latest_version_id uuid, active_version_id uuid,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(), deleted_at timestamptz,
 PRIMARY KEY(workspace_id,id), UNIQUE(id)
);
CREATE TABLE knowledge.material_acl (
 workspace_id uuid NOT NULL, material_id uuid NOT NULL,
 owner_user_id uuid NOT NULL REFERENCES platform.user_ref(id),
 revision integer NOT NULL DEFAULT 1 CHECK(revision>0),
 grants jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(grants)='array'),
 denied_user_ids uuid[] NOT NULL DEFAULT '{}',
 PRIMARY KEY(workspace_id,material_id),
 FOREIGN KEY(workspace_id,material_id) REFERENCES knowledge.material(workspace_id,id)
);
CREATE TABLE knowledge.version (
 workspace_id uuid NOT NULL, material_id uuid NOT NULL,
 id uuid NOT NULL DEFAULT gen_random_uuid(), version_no integer NOT NULL CHECK(version_no>0),
 contribution jsonb NOT NULL CHECK(jsonb_typeof(contribution)='object'),
 state text NOT NULL DEFAULT 'saved' CHECK(state IN ('saved','syncing','parsing','retrievable','failed','deleted')),
 contributor_user_id uuid NOT NULL REFERENCES platform.user_ref(id),
 uploaded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,material_id,id), UNIQUE(id), UNIQUE(workspace_id,material_id,version_no),
 FOREIGN KEY(workspace_id,material_id) REFERENCES knowledge.material(workspace_id,id)
);
ALTER TABLE knowledge.material ADD CONSTRAINT knowledge_latest_version_fk
 FOREIGN KEY(workspace_id,id,latest_version_id) REFERENCES knowledge.version(workspace_id,material_id,id);
ALTER TABLE knowledge.material ADD CONSTRAINT knowledge_active_version_fk
 FOREIGN KEY(workspace_id,id,active_version_id) REFERENCES knowledge.version(workspace_id,material_id,id);
CREATE TABLE knowledge.file (
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 id uuid NOT NULL DEFAULT gen_random_uuid(), uploaded_by uuid NOT NULL REFERENCES platform.user_ref(id),
 filename text NOT NULL CHECK(length(filename) BETWEEN 1 AND 300 AND filename !~ '[[:cntrl:]]'),
 media_type text NOT NULL CHECK(length(media_type) BETWEEN 1 AND 200 AND media_type !~ '[[:cntrl:]]'),
 content bytea NOT NULL CHECK(octet_length(content) BETWEEN 1 AND 104857600),
 sha256 text GENERATED ALWAYS AS (encode(sha256(content),'hex')) STORED,
 size_bytes integer GENERATED ALWAYS AS (octet_length(content)) STORED,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,id), UNIQUE(id)
);
CREATE TABLE knowledge.version_file (
 workspace_id uuid NOT NULL, material_id uuid NOT NULL, version_id uuid NOT NULL, file_id uuid NOT NULL,
 ordinal integer NOT NULL CHECK(ordinal>=0),
 PRIMARY KEY(workspace_id,version_id,file_id),
 FOREIGN KEY(workspace_id,material_id,version_id) REFERENCES knowledge.version(workspace_id,material_id,id),
 FOREIGN KEY(workspace_id,file_id) REFERENCES knowledge.file(workspace_id,id)
);
CREATE TABLE knowledge.audit (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 actor_id uuid NOT NULL REFERENCES platform.user_ref(id), material_id uuid,
 operation text NOT NULL, detail jsonb NOT NULL,
 occurred_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX knowledge_material_order ON knowledge.material(workspace_id,updated_at DESC,id);
CREATE INDEX knowledge_file_membership ON knowledge.version_file(workspace_id,file_id);

-- Re-resolve grants from live tables, not a request-supplied/cached authority GUC.
CREATE FUNCTION security.knowledge_permission(p_code text,p_owner uuid,p_teams uuid[],p_assigned boolean)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
 WITH g AS MATERIALIZED (
  SELECT * FROM security.authorization_grants_for(common.current_workspace_id(),common.current_user_ref_id())
  WHERE permission_code=p_code
 )
 SELECT COALESCE(security.workspace_is_active(common.current_workspace_id())
  AND security.tenant_permission_enabled(common.current_workspace_id(),p_code)
  AND NOT EXISTS(SELECT 1 FROM g WHERE effect='deny')
  AND EXISTS(SELECT 1 FROM g WHERE effect='allow' AND (
   scope_code='workspace' OR (scope_code='self' AND p_owner=common.current_user_ref_id())
   OR (scope_code='assigned' AND p_assigned)
   OR (scope_code='teams' AND team_ids && p_teams))),false);
$$;

CREATE FUNCTION security.knowledge_current_teams(p_user uuid) RETURNS uuid[]
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
 SELECT ARRAY(SELECT DISTINCT t.id FROM platform.team_membership tm JOIN platform.team t
  ON t.workspace_id=tm.workspace_id AND t.id=tm.team_id
  WHERE tm.workspace_id=common.current_workspace_id() AND tm.user_ref_id=p_user
   AND t.status='active' AND t.deleted_at IS NULL
   AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
   AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to);
$$;

CREATE FUNCTION security.knowledge_feature(p_code text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
 -- Feature discovery is independent of an object's owner/team. Object operations
 -- additionally call knowledge_permission/knowledge_can with the actual object.
 WITH g AS MATERIALIZED (
  SELECT * FROM security.authorization_grants_for(common.current_workspace_id(),common.current_user_ref_id())
  WHERE permission_code=p_code
 )
 SELECT NOT EXISTS(SELECT 1 FROM g WHERE effect='deny')
  AND EXISTS(SELECT 1 FROM g WHERE effect='allow' AND (p_code<>'knowledge.catalog_manage' OR scope_code='workspace'));
$$;

CREATE FUNCTION security.knowledge_can(p_material uuid,p_action text) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE a knowledge.material_acl; own boolean; personal boolean; shared boolean; teams uuid[];
BEGIN
 IF p_action NOT IN ('read','edit','manage') THEN RETURN false; END IF;
 SELECT acl.* INTO a FROM knowledge.material_acl acl JOIN knowledge.material m
  ON m.workspace_id=acl.workspace_id AND m.id=acl.material_id
  WHERE m.id=p_material AND m.workspace_id=common.current_workspace_id() AND m.deleted_at IS NULL;
 IF NOT FOUND OR common.current_user_ref_id()=ANY(a.denied_user_ids) THEN RETURN false; END IF;
 own:=a.owner_user_id=common.current_user_ref_id();
 personal:=EXISTS(SELECT 1 FROM jsonb_array_elements(a.grants) g
  WHERE g->'actions' ? p_action AND g->>'subject_kind'='user'
   AND (g->>'subject_id')::uuid=common.current_user_ref_id());
 teams:=ARRAY(SELECT DISTINCT (g->>'subject_id')::uuid FROM jsonb_array_elements(a.grants) g
  WHERE g->'actions' ? p_action AND g->>'subject_kind'='team'
   AND (g->>'subject_id')::uuid=ANY(security.knowledge_current_teams(common.current_user_ref_id())));
 shared:=personal OR cardinality(teams)>0 OR EXISTS(SELECT 1 FROM jsonb_array_elements(a.grants) g
  WHERE g->'actions' ? p_action AND g->>'subject_kind'='company');
 RETURN (own OR shared) AND security.knowledge_permission('knowledge.'||p_action,a.owner_user_id,
  security.knowledge_current_teams(a.owner_user_id)||teams,personal);
END $$;

CREATE FUNCTION security.knowledge_file_read(p_file uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
 SELECT EXISTS(SELECT 1 FROM knowledge.file f WHERE f.id=p_file AND f.workspace_id=common.current_workspace_id()
 AND ((f.uploaded_by=common.current_user_ref_id() AND security.knowledge_feature('knowledge.read')
  AND NOT EXISTS(SELECT 1 FROM knowledge.version_file l WHERE l.workspace_id=f.workspace_id AND l.file_id=f.id))
 OR EXISTS(SELECT 1 FROM knowledge.version_file l JOIN knowledge.version v
  ON v.workspace_id=l.workspace_id AND v.material_id=l.material_id AND v.id=l.version_id
  WHERE l.workspace_id=f.workspace_id AND l.file_id=f.id AND v.state<>'deleted'
   AND security.knowledge_can(l.material_id,'read'))));
$$;

CREATE FUNCTION security.knowledge_file_attach(p_file uuid,p_material uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
 SELECT EXISTS(SELECT 1 FROM knowledge.file f WHERE f.id=p_file AND f.workspace_id=common.current_workspace_id()
  AND ((f.uploaded_by=common.current_user_ref_id() AND NOT EXISTS(
   SELECT 1 FROM knowledge.version_file l WHERE l.workspace_id=f.workspace_id AND l.file_id=f.id))
  OR (p_material IS NOT NULL AND security.knowledge_can(p_material,'read') AND security.knowledge_can(p_material,'edit')
   AND EXISTS(SELECT 1 FROM knowledge.version_file l JOIN knowledge.version v
    ON v.workspace_id=l.workspace_id AND v.material_id=l.material_id AND v.id=l.version_id
    WHERE l.workspace_id=f.workspace_id AND l.file_id=f.id AND l.material_id=p_material AND v.state<>'deleted'))));
$$;

CREATE FUNCTION security.knowledge_store_file(p_id uuid,p_name text,p_type text,p_content bytea)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
BEGIN
 IF NOT (security.knowledge_feature('knowledge.read') AND
  (security.knowledge_feature('knowledge.create') OR security.knowledge_feature('knowledge.edit'))) THEN
  RAISE EXCEPTION 'KNOWLEDGE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 INSERT INTO knowledge.file(workspace_id,id,uploaded_by,filename,media_type,content)
 VALUES(common.current_workspace_id(),p_id,common.current_user_ref_id(),p_name,p_type,p_content);
 INSERT INTO knowledge.audit(workspace_id,actor_id,operation,detail)
 VALUES(common.current_workspace_id(),common.current_user_ref_id(),'file.archive',jsonb_build_object('file_id',p_id));
 RETURN p_id;
END $$;

CREATE FUNCTION security.knowledge_save_catalog(p_expected integer,p_value jsonb) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE current_revision integer; grp text; items jsonb; item jsonb;
BEGIN
 IF NOT security.knowledge_feature('knowledge.catalog_manage') THEN
  RAISE EXCEPTION 'KNOWLEDGE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('knowledge:'||common.current_workspace_id()::text,0));
 SELECT revision INTO current_revision FROM knowledge.catalog WHERE workspace_id=common.current_workspace_id() FOR UPDATE;
 IF p_expected IS NULL OR p_expected<>COALESCE(current_revision,0) THEN
  RAISE EXCEPTION 'KNOWLEDGE_CATALOG_CHANGED' USING ERRCODE='40001'; END IF;
 IF jsonb_typeof(p_value) IS DISTINCT FROM 'object' OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_value) k
  WHERE k NOT IN ('general_product_code','products','industries','reporting_positions','reporting_levels','decision_roles')) THEN
  RAISE EXCEPTION 'KNOWLEDGE_CATALOG_INVALID' USING ERRCODE='22023'; END IF;
 FOREACH grp IN ARRAY ARRAY['products','industries','reporting_positions','reporting_levels','decision_roles'] LOOP
  items:=p_value->grp;
  IF jsonb_typeof(items) IS DISTINCT FROM 'array' OR jsonb_array_length(items)>1000 THEN
   RAISE EXCEPTION 'KNOWLEDGE_CATALOG_INVALID' USING ERRCODE='22023'; END IF;
  IF (SELECT count(*)<>count(DISTINCT i->>'code') FROM jsonb_array_elements(items) i) THEN
   RAISE EXCEPTION 'KNOWLEDGE_CATALOG_INVALID' USING ERRCODE='22023'; END IF;
  FOR item IN SELECT * FROM jsonb_array_elements(items) LOOP
   IF jsonb_typeof(item) IS DISTINCT FROM 'object' OR jsonb_typeof(item->'code') IS DISTINCT FROM 'string' OR COALESCE(item->>'code','') !~ '^[A-Za-z0-9_.-]{1,100}$'
    OR jsonb_typeof(item->'label') IS DISTINCT FROM 'string' OR NULLIF(btrim(item->>'label'),'') IS NULL OR length(item->>'label')>300
    OR jsonb_typeof(item->'active') IS DISTINCT FROM 'boolean'
    OR EXISTS(SELECT 1 FROM jsonb_object_keys(item) k WHERE k NOT IN ('code','label','active')) THEN
    RAISE EXCEPTION 'KNOWLEDGE_CATALOG_INVALID' USING ERRCODE='22023'; END IF;
  END LOOP;
 END LOOP;
 IF jsonb_typeof(p_value->'general_product_code') IS DISTINCT FROM 'string' OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_value->'products') i
  WHERE i->>'code'=p_value->>'general_product_code' AND i->'active'='true'::jsonb) THEN
  RAISE EXCEPTION 'KNOWLEDGE_CATALOG_INVALID' USING ERRCODE='22023'; END IF;
 INSERT INTO knowledge.catalog(workspace_id,revision,value) VALUES(common.current_workspace_id(),1,p_value)
 ON CONFLICT(workspace_id) DO UPDATE SET revision=knowledge.catalog.revision+1,value=EXCLUDED.value,updated_at=clock_timestamp()
 RETURNING revision INTO current_revision;
 INSERT INTO knowledge.audit(workspace_id,actor_id,operation,detail)
 VALUES(common.current_workspace_id(),common.current_user_ref_id(),'catalog.update',jsonb_build_object('revision',current_revision));
 RETURN current_revision;
END $$;

CREATE FUNCTION security.knowledge_enum_label(p_catalog jsonb,p_group text,p_code text) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE label text;
BEGIN
 SELECT i->>'label' INTO label FROM jsonb_array_elements(p_catalog->p_group) i
 WHERE i->>'code'=p_code AND i->'active'='true'::jsonb;
 IF label IS NULL THEN RAISE EXCEPTION 'KNOWLEDGE_ENUM_INVALID' USING ERRCODE='22023'; END IF;
 RETURN label;
END $$;

CREATE FUNCTION security.knowledge_resolve(p_selection jsonb,p_material uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE c knowledge.catalog; category text; audience text; product text; industry text; customer_name text;
 opportunity_name text; title text; field text; file_id uuid; value text; industry_needed boolean; customer_needed boolean;
BEGIN
 IF NOT security.knowledge_feature('knowledge.read') OR
  (p_material IS NULL AND NOT security.knowledge_feature('knowledge.create')) OR
  (p_material IS NOT NULL AND NOT (security.knowledge_can(p_material,'read') AND security.knowledge_can(p_material,'edit'))) THEN
  RAISE EXCEPTION 'KNOWLEDGE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO c FROM knowledge.catalog WHERE workspace_id=common.current_workspace_id();
 IF NOT FOUND THEN RAISE EXCEPTION 'KNOWLEDGE_CATALOG_UNCONFIGURED' USING ERRCODE='55000'; END IF;
 IF jsonb_typeof(p_selection) IS DISTINCT FROM 'object' OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_selection) k
  WHERE k NOT IN ('expected_catalog_revision','content_category','content_scope','product_code','industry_code',
   'customer_id','reporting_position_code','reporting_level_code','decision_role_code','opportunity_id',
   'supplementary_text','content','remarks')) THEN
  RAISE EXCEPTION 'KNOWLEDGE_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 IF jsonb_typeof(p_selection->'expected_catalog_revision') IS DISTINCT FROM 'number'
  OR (p_selection->>'expected_catalog_revision')::integer<>c.revision THEN
  RAISE EXCEPTION 'KNOWLEDGE_CATALOG_CHANGED' USING ERRCODE='40001'; END IF;
 category:=CASE p_selection->>'content_category' WHEN 'overall_solution' THEN '总方案' WHEN 'product_intro' THEN '产品介绍'
  WHEN 'industry_solution' THEN '行业方案' WHEN 'customer_solution' THEN '客户方案' WHEN 'sales_support' THEN '销售支持'
  WHEN 'training_assessment' THEN '培训考核' WHEN 'conference_speech' THEN '会议演讲' END;
 audience:=CASE p_selection->>'content_scope' WHEN 'internal' THEN '仅内部' WHEN 'customer' THEN '对客'
  WHEN 'partner' THEN '对伙伴' WHEN 'public' THEN '完全公开' END;
 IF category IS NULL OR audience IS NULL THEN RAISE EXCEPTION 'KNOWLEDGE_ENUM_INVALID' USING ERRCODE='22023'; END IF;
 product:=security.knowledge_enum_label(c.value,'products',p_selection->>'product_code');
 IF p_selection->>'product_code'=c.value->>'general_product_code' THEN product:=NULL; END IF;
 industry_needed:=p_selection->>'content_category' IN ('industry_solution','customer_solution');
 customer_needed:=p_selection->>'content_category'='customer_solution';
 IF industry_needed IS DISTINCT FROM (p_selection->>'industry_code' IS NOT NULL) THEN
  RAISE EXCEPTION 'KNOWLEDGE_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 IF industry_needed THEN industry:=security.knowledge_enum_label(c.value,'industries',p_selection->>'industry_code'); END IF;
 FOREACH field IN ARRAY ARRAY['customer_id','reporting_position_code','reporting_level_code','decision_role_code'] LOOP
  IF customer_needed IS DISTINCT FROM (p_selection->>field IS NOT NULL) THEN
   RAISE EXCEPTION 'KNOWLEDGE_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF customer_needed THEN
  PERFORM security.knowledge_enum_label(c.value,'reporting_positions',p_selection->>'reporting_position_code');
  PERFORM security.knowledge_enum_label(c.value,'reporting_levels',p_selection->>'reporting_level_code');
  PERFORM security.knowledge_enum_label(c.value,'decision_roles',p_selection->>'decision_role_code');
  SELECT name INTO customer_name FROM crm.customer WHERE id=(p_selection->>'customer_id')::uuid
   AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL
   AND security.authorization_customer('customer.read',id);
  IF customer_name IS NULL THEN RAISE EXCEPTION 'KNOWLEDGE_REFERENCE_INVALID' USING ERRCODE='42501'; END IF;
 END IF;
 IF p_selection->>'opportunity_id' IS NOT NULL THEN
  SELECT name INTO opportunity_name FROM crm.opportunity WHERE id=(p_selection->>'opportunity_id')::uuid
   AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL
   AND security.authorization_opportunity('opportunity.read',id);
  IF opportunity_name IS NULL THEN RAISE EXCEPTION 'KNOWLEDGE_REFERENCE_INVALID' USING ERRCODE='42501'; END IF;
 END IF;
 FOREACH field IN ARRAY ARRAY['supplementary_text','remarks'] LOOP
  IF p_selection ? field AND jsonb_typeof(p_selection->field) IS DISTINCT FROM 'string' THEN
   RAISE EXCEPTION 'KNOWLEDGE_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF length(COALESCE(p_selection->>'supplementary_text',''))>300 OR length(COALESCE(p_selection->>'remarks',''))>2000
  OR jsonb_typeof(p_selection->'content') IS DISTINCT FROM 'object'
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_selection->'content') k WHERE k NOT IN ('file_ids','links')) THEN
  RAISE EXCEPTION 'KNOWLEDGE_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 FOREACH field IN ARRAY ARRAY['file_ids','links'] LOOP
  IF jsonb_typeof(COALESCE(p_selection->'content'->field,'[]'))<>'array'
   OR jsonb_array_length(COALESCE(p_selection->'content'->field,'[]'))>100 THEN
   RAISE EXCEPTION 'KNOWLEDGE_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(p_selection->'content'->field,'[]')) i
   WHERE jsonb_typeof(i) IS DISTINCT FROM 'string')
   OR (SELECT count(*)<>count(DISTINCT i) FROM jsonb_array_elements(COALESCE(p_selection->'content'->field,'[]')) i) THEN
   RAISE EXCEPTION 'KNOWLEDGE_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF jsonb_array_length(COALESCE(p_selection->'content'->'file_ids','[]'))+
  jsonb_array_length(COALESCE(p_selection->'content'->'links','[]'))=0 THEN
  RAISE EXCEPTION 'KNOWLEDGE_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 FOR value IN SELECT * FROM jsonb_array_elements_text(COALESCE(p_selection->'content'->'links','[]')) LOOP
  IF value IS NULL OR value !~* '^https?://[^/@[:space:]]+([/?#].*)?$' THEN
   RAISE EXCEPTION 'KNOWLEDGE_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 END LOOP;
 FOR file_id IN SELECT f.value::uuid FROM jsonb_array_elements_text(COALESCE(p_selection->'content'->'file_ids','[]')) f LOOP
  IF NOT security.knowledge_file_attach(file_id,p_material) THEN
   RAISE EXCEPTION 'KNOWLEDGE_FILE_DENIED' USING ERRCODE='42501'; END IF;
 END LOOP;
 title:=concat_ws('｜',product,category,audience,industry,customer_name,NULLIF(btrim(p_selection->>'supplementary_text'),''));
 RETURN jsonb_build_object('company_id',common.current_workspace_id(),'selection',p_selection,
  'content_name',title,'customer_name',customer_name,'opportunity_name',opportunity_name);
END $$;

CREATE FUNCTION security.knowledge_save_material(p_id uuid,p_expected integer,p_selection jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE m knowledge.material; content jsonb; mid uuid; vid uuid:=gen_random_uuid(); seq integer;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('knowledge:'||common.current_workspace_id()::text,0));
 IF NOT security.knowledge_feature('knowledge.read') THEN
  RAISE EXCEPTION 'KNOWLEDGE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF p_id IS NULL THEN
  IF p_expected IS NOT NULL OR NOT security.knowledge_permission('knowledge.create',common.current_user_ref_id(),
   security.knowledge_current_teams(common.current_user_ref_id()),false)
   OR NOT security.knowledge_permission('knowledge.read',common.current_user_ref_id(),
   security.knowledge_current_teams(common.current_user_ref_id()),false) THEN
   RAISE EXCEPTION 'KNOWLEDGE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  mid:=gen_random_uuid(); seq:=1;
 ELSE
  SELECT * INTO m FROM knowledge.material WHERE id=p_id AND workspace_id=common.current_workspace_id()
   AND deleted_at IS NULL FOR UPDATE;
  IF NOT FOUND OR NOT security.knowledge_can(p_id,'read') OR NOT security.knowledge_can(p_id,'edit') THEN
   RAISE EXCEPTION 'KNOWLEDGE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  IF p_expected IS NULL OR p_expected<>m.revision THEN
   RAISE EXCEPTION 'KNOWLEDGE_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
  mid:=p_id;
  SELECT COALESCE(max(version_no),0)+1 INTO seq FROM knowledge.version WHERE workspace_id=m.workspace_id AND material_id=mid;
 END IF;
 content:=security.knowledge_resolve(p_selection,p_id);
 IF EXISTS(SELECT 1 FROM knowledge.material x WHERE x.workspace_id=common.current_workspace_id() AND x.deleted_at IS NULL
  AND x.id<>mid AND x.content_name=content->>'content_name' AND security.knowledge_can(x.id,'read')) THEN
  RAISE EXCEPTION 'KNOWLEDGE_NAME_CONFLICT' USING ERRCODE='40001'; END IF;
 IF p_id IS NULL THEN
  INSERT INTO knowledge.material(workspace_id,id,created_by,content_name)
   VALUES(common.current_workspace_id(),mid,common.current_user_ref_id(),content->>'content_name');
  INSERT INTO knowledge.material_acl(workspace_id,material_id,owner_user_id)
   VALUES(common.current_workspace_id(),mid,common.current_user_ref_id());
 END IF;
 INSERT INTO knowledge.version(workspace_id,material_id,id,version_no,contribution,contributor_user_id)
 VALUES(common.current_workspace_id(),mid,vid,seq,content,common.current_user_ref_id());
 INSERT INTO knowledge.version_file(workspace_id,material_id,version_id,file_id,ordinal)
 SELECT common.current_workspace_id(),mid,vid,value::uuid,(ordinality-1)::integer
 FROM jsonb_array_elements_text(COALESCE(p_selection->'content'->'file_ids','[]')) WITH ORDINALITY;
 UPDATE knowledge.material SET latest_version_id=vid,content_name=content->>'content_name',
  revision=revision+CASE WHEN p_id IS NULL THEN 0 ELSE 1 END,updated_at=clock_timestamp()
 WHERE workspace_id=common.current_workspace_id() AND id=mid RETURNING * INTO m;
 INSERT INTO knowledge.audit(workspace_id,actor_id,material_id,operation,detail)
 VALUES(common.current_workspace_id(),common.current_user_ref_id(),mid,'material.save',
  jsonb_build_object('version_id',vid,'version_no',seq,'revision',m.revision));
 RETURN jsonb_build_object('material_id',mid,'version_id',vid,'version_no',seq,'material_revision',m.revision,
  'content_name',m.content_name,'state','saved','active_version_id',m.active_version_id);
END $$;

CREATE FUNCTION security.knowledge_save_acl(p_id uuid,p_expected integer,p_grants jsonb,p_denied uuid[]) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE a knowledge.material_acl; g jsonb; subject uuid; current_revision integer;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('knowledge:'||common.current_workspace_id()::text,0));
 SELECT * INTO a FROM knowledge.material_acl WHERE material_id=p_id AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF NOT FOUND OR NOT security.knowledge_can(p_id,'manage') OR NOT security.knowledge_can(p_id,'read') THEN
  RAISE EXCEPTION 'KNOWLEDGE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF p_expected IS NULL OR p_expected<>a.revision THEN RAISE EXCEPTION 'KNOWLEDGE_ACL_CONFLICT' USING ERRCODE='40001'; END IF;
 IF jsonb_typeof(p_grants) IS DISTINCT FROM 'array' OR jsonb_array_length(p_grants)>200
  OR p_denied IS NULL OR cardinality(p_denied)>200 OR array_position(p_denied,NULL) IS NOT NULL THEN
  RAISE EXCEPTION 'KNOWLEDGE_ACL_INVALID' USING ERRCODE='22023'; END IF;
 FOR g IN SELECT * FROM jsonb_array_elements(p_grants) LOOP
  IF jsonb_typeof(g) IS DISTINCT FROM 'object' OR COALESCE(g->>'subject_kind','') NOT IN ('user','team','company')
   OR jsonb_typeof(g->'actions') IS DISTINCT FROM 'array' OR jsonb_array_length(g->'actions')=0
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(g->'actions') action
    WHERE jsonb_typeof(action) IS DISTINCT FROM 'string' OR action#>>'{}' NOT IN ('read','edit','manage'))
   OR EXISTS(SELECT 1 FROM jsonb_object_keys(g) k WHERE k NOT IN ('subject_kind','subject_id','actions')) THEN
   RAISE EXCEPTION 'KNOWLEDGE_ACL_INVALID' USING ERRCODE='22023'; END IF;
  subject:=(g->>'subject_id')::uuid;
  IF g->>'subject_kind'='company' THEN
   IF subject IS NOT NULL THEN RAISE EXCEPTION 'KNOWLEDGE_ACL_INVALID' USING ERRCODE='22023'; END IF;
  ELSIF g->>'subject_kind'='user' THEN
   IF NOT EXISTS(SELECT 1 FROM platform.user_ref WHERE id=subject AND workspace_id=a.workspace_id
    AND status='active' AND deleted_at IS NULL) THEN RAISE EXCEPTION 'KNOWLEDGE_ACL_INVALID' USING ERRCODE='22023'; END IF;
  ELSE
   IF NOT EXISTS(SELECT 1 FROM platform.team WHERE id=subject AND workspace_id=a.workspace_id AND status='active'
    AND deleted_at IS NULL AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to) THEN
    RAISE EXCEPTION 'KNOWLEDGE_ACL_INVALID' USING ERRCODE='22023'; END IF;
  END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM unnest(p_denied) u WHERE NOT EXISTS(SELECT 1 FROM platform.user_ref
  WHERE id=u AND workspace_id=a.workspace_id AND deleted_at IS NULL)) THEN
  RAISE EXCEPTION 'KNOWLEDGE_ACL_INVALID' USING ERRCODE='22023'; END IF;
 UPDATE knowledge.material_acl SET grants=p_grants,denied_user_ids=p_denied,revision=revision+1
 WHERE workspace_id=a.workspace_id AND material_id=p_id RETURNING revision INTO current_revision;
 INSERT INTO knowledge.audit(workspace_id,actor_id,material_id,operation,detail)
 VALUES(a.workspace_id,common.current_user_ref_id(),p_id,'acl.update',jsonb_build_object(
  'before_revision',a.revision,'after_revision',current_revision,'before_grants',a.grants,'after_grants',p_grants,
  'before_denied_user_ids',a.denied_user_ids,'after_denied_user_ids',p_denied));
 RETURN current_revision;
END $$;

-- A projection never returns data for a material the caller cannot currently read.
-- Contributor display names are material attribution, not a general people-directory endpoint.
CREATE FUNCTION security.knowledge_projection(p_material uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
 SELECT jsonb_build_object(
  'contributor_name',u.display_name,
  'personal',a.owner_user_id=common.current_user_ref_id(),
  'team',EXISTS(SELECT 1 FROM jsonb_array_elements(a.grants) g
   WHERE g->>'subject_kind'='team' AND g->'actions' ? 'read'
    AND (g->>'subject_id')::uuid=ANY(security.knowledge_current_teams(common.current_user_ref_id()))),
  'shared',a.owner_user_id<>common.current_user_ref_id(),
  'sharing_label',COALESCE(NULLIF(concat_ws(' / ',
   CASE WHEN EXISTS(SELECT 1 FROM jsonb_array_elements(a.grants) g
    WHERE g->>'subject_kind'='company' AND g->'actions' ? 'read') THEN '企业共享' END,
   CASE WHEN EXISTS(SELECT 1 FROM jsonb_array_elements(a.grants) g
    WHERE g->>'subject_kind'='team' AND g->'actions' ? 'read') THEN '团队共享' END,
   CASE WHEN EXISTS(SELECT 1 FROM jsonb_array_elements(a.grants) g
    WHERE g->>'subject_kind'='user' AND g->'actions' ? 'read'
     AND (g->>'subject_id')::uuid<>a.owner_user_id) THEN '指定成员' END),''),'个人'))
 FROM knowledge.material m JOIN knowledge.material_acl a ON a.workspace_id=m.workspace_id AND a.material_id=m.id
 JOIN knowledge.version v ON v.workspace_id=m.workspace_id AND v.id=m.latest_version_id
 LEFT JOIN platform.user_ref u ON u.workspace_id=m.workspace_id AND u.id=v.contributor_user_id
 WHERE m.workspace_id=common.current_workspace_id() AND m.id=p_material AND v.state<>'deleted'
  AND security.knowledge_can(m.id,'read');
$$;
CREATE FUNCTION security.knowledge_can_create() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
 SELECT security.knowledge_permission('knowledge.create',common.current_user_ref_id(),
  security.knowledge_current_teams(common.current_user_ref_id()),false)
 AND security.knowledge_permission('knowledge.read',common.current_user_ref_id(),
  security.knowledge_current_teams(common.current_user_ref_id()),false);
$$;
REVOKE ALL ON FUNCTION security.knowledge_projection(uuid),security.knowledge_can_create() FROM PUBLIC;

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['catalog','material','material_acl','version','file','version_file','audit'] LOOP
  EXECUTE format('ALTER TABLE knowledge.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE knowledge.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('REVOKE ALL ON knowledge.%I FROM PUBLIC',t);
 END LOOP;
END $$;
CREATE POLICY catalog_read ON knowledge.catalog FOR SELECT USING(workspace_id=common.current_workspace_id()
 AND (security.knowledge_feature('knowledge.read') OR security.knowledge_feature('knowledge.catalog_manage')));
CREATE POLICY material_read ON knowledge.material FOR SELECT USING(workspace_id=common.current_workspace_id()
 AND security.knowledge_can(id,'read'));
CREATE POLICY acl_read ON knowledge.material_acl FOR SELECT USING(workspace_id=common.current_workspace_id()
 AND security.knowledge_can(material_id,'read'));
CREATE POLICY version_read ON knowledge.version FOR SELECT USING(workspace_id=common.current_workspace_id()
 AND state<>'deleted' AND security.knowledge_can(material_id,'read'));
CREATE POLICY file_read ON knowledge.file FOR SELECT USING(workspace_id=common.current_workspace_id()
 AND security.knowledge_file_read(id));
CREATE POLICY link_read ON knowledge.version_file FOR SELECT USING(workspace_id=common.current_workspace_id()
 AND security.knowledge_can(material_id,'read') AND EXISTS(SELECT 1 FROM knowledge.version v
  WHERE v.workspace_id=version_file.workspace_id AND v.id=version_file.version_id AND v.state<>'deleted'));
CREATE POLICY audit_read ON knowledge.audit FOR SELECT USING(workspace_id=common.current_workspace_id()
 AND ((material_id IS NOT NULL AND security.knowledge_can(material_id,'manage'))
  OR (material_id IS NULL AND actor_id=common.current_user_ref_id() AND security.knowledge_feature('knowledge.read'))));

REVOKE ALL ON FUNCTION security.knowledge_permission(text,uuid,uuid[],boolean),
 security.knowledge_current_teams(uuid),security.knowledge_feature(text),security.knowledge_can(uuid,text),
 security.knowledge_file_read(uuid),security.knowledge_file_attach(uuid,uuid),
 security.knowledge_store_file(uuid,text,text,bytea),security.knowledge_save_catalog(integer,jsonb),
 security.knowledge_enum_label(jsonb,text,text),security.knowledge_resolve(jsonb,uuid),
 security.knowledge_save_material(uuid,integer,jsonb),security.knowledge_save_acl(uuid,integer,jsonb,uuid[]) FROM PUBLIC;
INSERT INTO ops.schema_migration(version,description) VALUES('V406','Knowledge originals, immutable versions and explicit material ACL');
COMMIT;
