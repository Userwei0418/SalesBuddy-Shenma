BEGIN;

-- Generated files retain their exact source-version dependencies. Sharing a
-- work grants no permission to any source material, now or in the future.
CREATE TABLE knowledge.work (
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id), id uuid NOT NULL,
 owner_user_id uuid NOT NULL REFERENCES platform.user_ref(id),
 parent_work_id uuid, version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 manifest jsonb NOT NULL CHECK(jsonb_typeof(manifest)='object'),
 revision integer NOT NULL DEFAULT 1 CHECK(revision>0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,id), UNIQUE(id), UNIQUE(workspace_id,parent_work_id),
 FOREIGN KEY(workspace_id,parent_work_id) REFERENCES knowledge.work(workspace_id,id)
);
CREATE TABLE knowledge.work_source (
 workspace_id uuid NOT NULL,work_id uuid NOT NULL,material_id uuid NOT NULL,version_id uuid NOT NULL,
 PRIMARY KEY(workspace_id,work_id,material_id),
 FOREIGN KEY(workspace_id,work_id) REFERENCES knowledge.work(workspace_id,id),
 FOREIGN KEY(workspace_id,material_id,version_id) REFERENCES knowledge.version(workspace_id,material_id,id)
);
CREATE TABLE knowledge.work_file (
 workspace_id uuid NOT NULL,work_id uuid NOT NULL,file_id uuid NOT NULL,
 PRIMARY KEY(workspace_id,work_id,file_id), UNIQUE(workspace_id,file_id),
 FOREIGN KEY(workspace_id,work_id) REFERENCES knowledge.work(workspace_id,id),
 FOREIGN KEY(workspace_id,file_id) REFERENCES knowledge.file(workspace_id,id)
);
CREATE TABLE knowledge.work_grant (
 workspace_id uuid NOT NULL,work_id uuid NOT NULL,
 subject_kind text NOT NULL CHECK(subject_kind IN ('user','team')),subject_id uuid NOT NULL,
 PRIMARY KEY(workspace_id,work_id,subject_kind,subject_id),
 FOREIGN KEY(workspace_id,work_id) REFERENCES knowledge.work(workspace_id,id)
);
CREATE INDEX knowledge_work_order ON knowledge.work(workspace_id,created_at DESC,id);
CREATE INDEX knowledge_work_recipient ON knowledge.work_grant(workspace_id,subject_kind,subject_id,work_id);

CREATE FUNCTION security.knowledge_work_can(p_work uuid,p_manage boolean DEFAULT false) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE w knowledge.work; own boolean; personal boolean; teams uuid[];
BEGIN
 SELECT * INTO w FROM knowledge.work WHERE workspace_id=common.current_workspace_id() AND id=p_work;
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.id=common.current_user_ref_id()
  AND u.workspace_id=w.workspace_id AND u.status='active' AND u.deleted_at IS NULL) THEN RETURN false; END IF;
 own:=w.owner_user_id=common.current_user_ref_id();
 IF p_manage AND NOT own THEN RETURN false; END IF;
 personal:=EXISTS(SELECT 1 FROM knowledge.work_grant g WHERE g.workspace_id=w.workspace_id AND g.work_id=w.id
  AND g.subject_kind='user' AND g.subject_id=common.current_user_ref_id());
 teams:=ARRAY(SELECT g.subject_id FROM knowledge.work_grant g WHERE g.workspace_id=w.workspace_id
  AND g.work_id=w.id AND g.subject_kind='team'
  AND g.subject_id=ANY(security.knowledge_current_teams(common.current_user_ref_id())));
 IF NOT (own OR personal OR cardinality(teams)>0) OR NOT security.knowledge_permission(
  'knowledge.read',w.owner_user_id,security.knowledge_current_teams(w.owner_user_id)||teams,personal) THEN RETURN false; END IF;
 -- An absent/deleted version or a revoked source blocks the entire derivative,
 -- including its author. A newer active source does not rewrite old evidence.
 RETURN EXISTS(SELECT 1 FROM knowledge.work_source s WHERE s.workspace_id=w.workspace_id AND s.work_id=w.id)
  AND NOT EXISTS(SELECT 1 FROM knowledge.work_source s LEFT JOIN knowledge.version v
   ON v.workspace_id=s.workspace_id AND v.material_id=s.material_id AND v.id=s.version_id
   WHERE s.workspace_id=w.workspace_id AND s.work_id=w.id
    AND (v.id IS NULL OR v.state='deleted' OR NOT security.knowledge_can(s.material_id,'read')));
END $$;

CREATE FUNCTION security.knowledge_self_teams() RETURNS uuid[]
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
 SELECT security.knowledge_current_teams(common.current_user_ref_id());
$$;

CREATE FUNCTION security.knowledge_lock_work_parent(p_id uuid,p_expected integer) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE w knowledge.work;
BEGIN
 SELECT * INTO w FROM knowledge.work WHERE workspace_id=common.current_workspace_id() AND id=p_id FOR UPDATE;
 IF NOT FOUND OR NOT security.knowledge_work_can(p_id) THEN
  RAISE EXCEPTION 'KNOWLEDGE_WORK_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF NOT security.knowledge_work_can(p_id,true) THEN
  RAISE EXCEPTION 'KNOWLEDGE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF p_expected IS NULL OR p_expected<>w.revision OR EXISTS(SELECT 1 FROM knowledge.work
  WHERE workspace_id=w.workspace_id AND parent_work_id=w.id) THEN
  RAISE EXCEPTION 'KNOWLEDGE_WORK_CHANGED' USING ERRCODE='40001'; END IF;
 RETURN w.version_no;
END $$;

CREATE FUNCTION security.knowledge_create_work(p_id uuid,p_manifest jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE source jsonb; file jsonb; parent knowledge.work; parent_id uuid;
BEGIN
 IF NOT (security.knowledge_feature('knowledge.read') AND
   (security.knowledge_feature('knowledge.create') OR security.knowledge_feature('knowledge.edit'))) THEN
  RAISE EXCEPTION 'KNOWLEDGE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF jsonb_typeof(p_manifest) IS DISTINCT FROM 'object'
  OR jsonb_typeof(p_manifest->'sources') IS DISTINCT FROM 'array'
  OR jsonb_typeof(p_manifest->'files') IS DISTINCT FROM 'array'
  OR jsonb_array_length(p_manifest->'sources') NOT BETWEEN 1 AND 20
  OR jsonb_array_length(p_manifest->'files') NOT BETWEEN 1 AND 10 THEN
  RAISE EXCEPTION 'KNOWLEDGE_WORK_INVALID' USING ERRCODE='22023'; END IF;
 parent_id:=(p_manifest->>'parent_work_id')::uuid;
 IF parent_id IS NOT NULL THEN
  SELECT * INTO parent FROM knowledge.work WHERE workspace_id=common.current_workspace_id() AND id=parent_id FOR UPDATE;
  IF NOT FOUND OR NOT security.knowledge_work_can(parent_id,true) THEN
   RAISE EXCEPTION 'KNOWLEDGE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  IF (p_manifest->>'expected_parent_revision')::integer IS DISTINCT FROM parent.revision
   OR EXISTS(SELECT 1 FROM knowledge.work WHERE workspace_id=parent.workspace_id AND parent_work_id=parent.id) THEN
   RAISE EXCEPTION 'KNOWLEDGE_WORK_CHANGED' USING ERRCODE='40001'; END IF;
 END IF;
 FOR source IN SELECT * FROM jsonb_array_elements(p_manifest->'sources') LOOP
  IF NOT EXISTS(SELECT 1 FROM knowledge.version v JOIN knowledge.material m
   ON m.workspace_id=v.workspace_id AND m.id=v.material_id
   WHERE v.workspace_id=common.current_workspace_id() AND v.id=(source->>'version_id')::uuid
    AND v.material_id=(source->>'material_id')::uuid AND v.state<>'deleted'
    AND security.knowledge_can(m.id,'read')) THEN
   RAISE EXCEPTION 'KNOWLEDGE_VERSION_UNAVAILABLE' USING ERRCODE='P0002'; END IF;
 END LOOP;
 FOR file IN SELECT * FROM jsonb_array_elements(p_manifest->'files') LOOP
  IF NOT EXISTS(SELECT 1 FROM knowledge.file f WHERE f.workspace_id=common.current_workspace_id()
   AND f.id=(file->>'file_id')::uuid AND f.uploaded_by=common.current_user_ref_id()
   AND NOT EXISTS(SELECT 1 FROM knowledge.version_file l WHERE l.workspace_id=f.workspace_id AND l.file_id=f.id)
   AND NOT EXISTS(SELECT 1 FROM knowledge.work_file l WHERE l.workspace_id=f.workspace_id AND l.file_id=f.id)) THEN
   RAISE EXCEPTION 'KNOWLEDGE_FILE_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 END LOOP;
 INSERT INTO knowledge.work(workspace_id,id,owner_user_id,manifest,parent_work_id,version_no)
 VALUES(common.current_workspace_id(),p_id,common.current_user_ref_id(),p_manifest,parent_id,COALESCE(parent.version_no,0)+1);
 IF parent_id IS NOT NULL THEN
  UPDATE knowledge.work SET revision=revision+1,updated_at=clock_timestamp()
   WHERE workspace_id=parent.workspace_id AND id=parent.id;
 END IF;
 INSERT INTO knowledge.work_source SELECT common.current_workspace_id(),p_id,
  (s->>'material_id')::uuid,(s->>'version_id')::uuid FROM jsonb_array_elements(p_manifest->'sources') s;
 INSERT INTO knowledge.work_file SELECT common.current_workspace_id(),p_id,
  (f->>'file_id')::uuid FROM jsonb_array_elements(p_manifest->'files') f;
 INSERT INTO knowledge.audit(workspace_id,actor_id,operation,detail)
 VALUES(common.current_workspace_id(),common.current_user_ref_id(),'work.generate',jsonb_build_object('work_id',p_id));
 RETURN p_id;
END $$;

CREATE FUNCTION security.knowledge_publish_work(p_id uuid,p_expected integer,p_recipients jsonb) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE w knowledge.work; recipient jsonb; sid uuid;
BEGIN
 SELECT * INTO w FROM knowledge.work WHERE workspace_id=common.current_workspace_id() AND id=p_id FOR UPDATE;
 IF NOT FOUND OR NOT security.knowledge_work_can(p_id) THEN
  RAISE EXCEPTION 'KNOWLEDGE_WORK_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF NOT security.knowledge_work_can(p_id,true) THEN
  RAISE EXCEPTION 'KNOWLEDGE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF p_expected IS NULL OR p_expected<>w.revision THEN
  RAISE EXCEPTION 'KNOWLEDGE_WORK_CHANGED' USING ERRCODE='40001'; END IF;
 IF jsonb_typeof(p_recipients) IS DISTINCT FROM 'array' OR jsonb_array_length(p_recipients)>200 THEN
  RAISE EXCEPTION 'KNOWLEDGE_RECIPIENT_INVALID' USING ERRCODE='22023'; END IF;
 IF (SELECT count(*)<>count(DISTINCT (g->>'subject_kind',g->>'subject_id')) FROM jsonb_array_elements(p_recipients) g) THEN
  RAISE EXCEPTION 'KNOWLEDGE_RECIPIENT_DUPLICATE' USING ERRCODE='22023'; END IF;
 FOR recipient IN SELECT * FROM jsonb_array_elements(p_recipients) LOOP
  sid:=(recipient->>'subject_id')::uuid;
  IF sid IS NULL OR recipient->>'subject_kind' IS NULL OR recipient->>'subject_kind' NOT IN ('user','team')
   OR (recipient->>'subject_kind'='user' AND NOT EXISTS(SELECT 1 FROM platform.user_ref
    WHERE workspace_id=w.workspace_id AND id=sid AND status='active' AND deleted_at IS NULL))
   OR (recipient->>'subject_kind'='team' AND NOT EXISTS(SELECT 1 FROM platform.team
    WHERE workspace_id=w.workspace_id AND id=sid AND status='active' AND deleted_at IS NULL
     AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to)) THEN
   RAISE EXCEPTION 'KNOWLEDGE_RECIPIENT_INVALID' USING ERRCODE='22023'; END IF;
 END LOOP;
 DELETE FROM knowledge.work_grant WHERE workspace_id=w.workspace_id AND work_id=w.id;
 INSERT INTO knowledge.work_grant SELECT w.workspace_id,w.id,r->>'subject_kind',(r->>'subject_id')::uuid
  FROM jsonb_array_elements(p_recipients) r;
 UPDATE knowledge.work SET revision=revision+1,updated_at=clock_timestamp() WHERE workspace_id=w.workspace_id AND id=w.id;
 INSERT INTO knowledge.audit(workspace_id,actor_id,operation,detail)
 VALUES(w.workspace_id,common.current_user_ref_id(),'work.share',jsonb_build_object(
  'work_id',w.id,'previous_revision',w.revision,'revision',w.revision+1,'recipients',p_recipients));
 RETURN w.revision+1;
END $$;

CREATE FUNCTION security.knowledge_work_recipients(p_id uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
 SELECT CASE WHEN security.knowledge_work_can(p_id,true) THEN COALESCE((
  SELECT jsonb_agg(jsonb_build_object('subject_kind',g.subject_kind,'subject_id',g.subject_id,
   'display_name',CASE WHEN g.subject_kind='user' THEN u.display_name ELSE t.name END)
   ORDER BY g.subject_kind,g.subject_id)
  FROM knowledge.work_grant g LEFT JOIN platform.user_ref u ON g.subject_kind='user'
   AND u.workspace_id=g.workspace_id AND u.id=g.subject_id
  LEFT JOIN platform.team t ON g.subject_kind='team' AND t.workspace_id=g.workspace_id AND t.id=g.subject_id
  WHERE g.workspace_id=common.current_workspace_id() AND g.work_id=p_id),'[]'::jsonb) ELSE '[]'::jsonb END;
$$;

CREATE FUNCTION security.knowledge_work_candidates(p_id uuid,p_kind text,p_query text,p_limit integer,p_offset integer)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE result jsonb;
BEGIN
 IF NOT security.knowledge_work_can(p_id) THEN
  RAISE EXCEPTION 'KNOWLEDGE_WORK_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF NOT security.knowledge_work_can(p_id,true) THEN
  RAISE EXCEPTION 'KNOWLEDGE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF p_kind IS NULL OR p_kind NOT IN ('user','team') OR p_query IS NULL OR length(p_query)>100
  OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 OR p_offset IS NULL OR p_offset NOT BETWEEN 0 AND 100000 THEN
  RAISE EXCEPTION 'KNOWLEDGE_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 WITH candidates AS MATERIALIZED (
  SELECT id AS subject_id,display_name FROM platform.user_ref WHERE p_kind='user'
   AND workspace_id=common.current_workspace_id() AND status='active' AND deleted_at IS NULL
  UNION ALL
  SELECT id,name FROM platform.team WHERE p_kind='team' AND workspace_id=common.current_workspace_id()
   AND status='active' AND deleted_at IS NULL AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to
 ), matched AS MATERIALIZED (SELECT * FROM candidates WHERE p_query='' OR strpos(lower(display_name),lower(p_query))>0),
 page AS (SELECT * FROM matched ORDER BY display_name,subject_id LIMIT p_limit OFFSET p_offset)
 SELECT jsonb_build_object('work_id',p_id,'subject_kind',p_kind,
  'items',COALESCE((SELECT jsonb_agg(to_jsonb(p) ORDER BY display_name,subject_id) FROM page p),'[]'::jsonb),
  'total',(SELECT count(*) FROM matched),'limit',p_limit,'offset',p_offset) INTO result;
 RETURN result;
END $$;

-- Preserve valid pre-V414 immutable manifests; incomplete legacy receipts stay
-- untouched as audit evidence and are not exposed as readable works.
DO $$
DECLARE receipt record; manifest jsonb; wid uuid;
BEGIN
 FOR receipt IN SELECT workspace_id,actor_id,response,committed_at FROM ops.mutation_receipt
  WHERE operation='knowledge.work.generate' AND response->>'status'='succeeded'
   AND jsonb_typeof(response->'_manifest')='object' LOOP
  BEGIN
   manifest:=receipt.response->'_manifest'; wid:=(manifest->>'work_id')::uuid;
   IF wid IS NULL OR jsonb_typeof(manifest->'sources') IS DISTINCT FROM 'array'
    OR jsonb_typeof(manifest->'files') IS DISTINCT FROM 'array'
    OR jsonb_array_length(manifest->'sources') NOT BETWEEN 1 AND 20
    OR jsonb_array_length(manifest->'files') NOT BETWEEN 1 AND 10 THEN CONTINUE; END IF;
   IF EXISTS(SELECT 1 FROM jsonb_array_elements(manifest->'files') f LEFT JOIN knowledge.file k
    ON k.workspace_id=receipt.workspace_id AND k.id=(f->>'file_id')::uuid
    WHERE k.id IS NULL OR k.uploaded_by<>receipt.actor_id OR EXISTS(SELECT 1 FROM knowledge.version_file l
     WHERE l.workspace_id=k.workspace_id AND l.file_id=k.id)) THEN CONTINUE; END IF;
   INSERT INTO knowledge.work(workspace_id,id,owner_user_id,manifest,created_at,updated_at)
    VALUES(receipt.workspace_id,wid,receipt.actor_id,manifest,receipt.committed_at,receipt.committed_at);
   INSERT INTO knowledge.work_source SELECT receipt.workspace_id,wid,(s->>'material_id')::uuid,
    (s->>'version_id')::uuid FROM jsonb_array_elements(manifest->'sources') s;
   INSERT INTO knowledge.work_file SELECT receipt.workspace_id,wid,(f->>'file_id')::uuid
    FROM jsonb_array_elements(manifest->'files') f;
  EXCEPTION WHEN invalid_text_representation OR foreign_key_violation OR unique_violation OR not_null_violation THEN
   RAISE NOTICE 'Skipped invalid legacy knowledge work manifest';
  END;
 END LOOP;
END $$;

-- Generated files are never unbound uploads and cannot be attached to a new
-- ordinary material to circumvent derivative/source revocation.
CREATE OR REPLACE FUNCTION security.knowledge_file_read(p_file uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
 SELECT EXISTS(SELECT 1 FROM knowledge.file f WHERE f.id=p_file AND f.workspace_id=common.current_workspace_id()
 AND ((f.uploaded_by=common.current_user_ref_id() AND security.knowledge_feature('knowledge.read')
  AND NOT EXISTS(SELECT 1 FROM knowledge.version_file l WHERE l.workspace_id=f.workspace_id AND l.file_id=f.id)
  AND NOT EXISTS(SELECT 1 FROM knowledge.work_file l WHERE l.workspace_id=f.workspace_id AND l.file_id=f.id))
 OR EXISTS(SELECT 1 FROM knowledge.version_file l JOIN knowledge.version v ON v.workspace_id=l.workspace_id
  AND v.material_id=l.material_id AND v.id=l.version_id WHERE l.workspace_id=f.workspace_id AND l.file_id=f.id
  AND v.state<>'deleted' AND security.knowledge_can(l.material_id,'read'))
 OR EXISTS(SELECT 1 FROM knowledge.work_file l WHERE l.workspace_id=f.workspace_id AND l.file_id=f.id
  AND security.knowledge_work_can(l.work_id))));
$$;
CREATE OR REPLACE FUNCTION security.knowledge_file_attach(p_file uuid,p_material uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
 SELECT EXISTS(SELECT 1 FROM knowledge.file f WHERE f.id=p_file AND f.workspace_id=common.current_workspace_id()
  AND NOT EXISTS(SELECT 1 FROM knowledge.work_file w WHERE w.workspace_id=f.workspace_id AND w.file_id=f.id)
  AND ((f.uploaded_by=common.current_user_ref_id() AND NOT EXISTS(
   SELECT 1 FROM knowledge.version_file l WHERE l.workspace_id=f.workspace_id AND l.file_id=f.id))
  OR (p_material IS NOT NULL AND security.knowledge_can(p_material,'read') AND security.knowledge_can(p_material,'edit')
   AND EXISTS(SELECT 1 FROM knowledge.version_file l JOIN knowledge.version v
    ON v.workspace_id=l.workspace_id AND v.material_id=l.material_id AND v.id=l.version_id
    WHERE l.workspace_id=f.workspace_id AND l.file_id=f.id AND l.material_id=p_material AND v.state<>'deleted'))));
$$;

ALTER TABLE knowledge.work ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.work FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge.work_source ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.work_source FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge.work_file ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.work_file FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge.work_grant ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.work_grant FORCE ROW LEVEL SECURITY;
CREATE POLICY work_read ON knowledge.work FOR SELECT USING(workspace_id=common.current_workspace_id() AND security.knowledge_work_can(id));
CREATE POLICY work_source_read ON knowledge.work_source FOR SELECT USING(workspace_id=common.current_workspace_id() AND security.knowledge_work_can(work_id));
CREATE POLICY work_file_read ON knowledge.work_file FOR SELECT USING(workspace_id=common.current_workspace_id() AND security.knowledge_work_can(work_id));
CREATE POLICY work_grant_read ON knowledge.work_grant FOR SELECT USING(workspace_id=common.current_workspace_id() AND security.knowledge_work_can(work_id,true));
REVOKE ALL ON knowledge.work,knowledge.work_source,knowledge.work_file,knowledge.work_grant FROM PUBLIC;
REVOKE ALL ON FUNCTION security.knowledge_self_teams(),security.knowledge_lock_work_parent(uuid,integer),
 security.knowledge_work_can(uuid,boolean),security.knowledge_create_work(uuid,jsonb),
 security.knowledge_publish_work(uuid,integer,jsonb),security.knowledge_work_recipients(uuid),
 security.knowledge_work_candidates(uuid,text,text,integer,integer) FROM PUBLIC,salegent_feishu_worker;
INSERT INTO ops.schema_migration(version,description) VALUES('V414','Generated knowledge works with revocable recipient and source ACL');
COMMIT;
