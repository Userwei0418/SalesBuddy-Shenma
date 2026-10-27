BEGIN;
-- Material ACL administration has its own directory authority. Do not borrow
-- customer/task person scopes or a sales-role whitelist from general directories.
CREATE FUNCTION security.knowledge_acl_candidates(
 p_material uuid,p_kind text,p_query text,p_limit integer,p_offset integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE result jsonb;
BEGIN
 IF NOT security.knowledge_can(p_material,'read') THEN
  RAISE EXCEPTION 'KNOWLEDGE_MATERIAL_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF NOT security.knowledge_can(p_material,'manage') THEN
  RAISE EXCEPTION 'KNOWLEDGE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF p_kind IS NULL OR p_kind NOT IN ('user','team')
  OR p_query IS NULL OR length(p_query)>100
  OR p_limit IS NULL OR p_limit<1 OR p_limit>100 OR p_offset IS NULL OR p_offset<0 OR p_offset>100000 THEN
  RAISE EXCEPTION 'KNOWLEDGE_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 -- These validity predicates intentionally match V406 knowledge_save_acl:
 -- grant-user requires active and not deleted;
 -- grant-team additionally requires an active validity interval.
 WITH candidates AS MATERIALIZED (
  SELECT u.id AS subject_id,u.display_name FROM platform.user_ref u
   WHERE p_kind='user' AND u.workspace_id=common.current_workspace_id() AND u.deleted_at IS NULL
    AND u.status='active'
  UNION ALL
  SELECT t.id,t.name FROM platform.team t
   WHERE p_kind='team' AND t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL
    AND t.status='active' AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
 ), matched AS MATERIALIZED (
  SELECT * FROM candidates WHERE p_query='' OR strpos(lower(display_name),lower(p_query))>0
 ), page AS (
  SELECT * FROM matched ORDER BY display_name,subject_id LIMIT p_limit OFFSET p_offset
 ) SELECT jsonb_build_object('material_id',p_material,'subject_kind',p_kind,
  'items',COALESCE((SELECT jsonb_agg(to_jsonb(p) ORDER BY display_name,subject_id) FROM page p),'[]'::jsonb),
  'total',(SELECT count(*) FROM matched),'limit',p_limit,'offset',p_offset) INTO result;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION security.knowledge_acl_candidates(uuid,text,text,integer,integer)
 FROM PUBLIC,salegent_feishu_worker;
INSERT INTO ops.schema_migration(version,description)
 VALUES('V409','Material-authorized knowledge ACL candidate directory');
COMMIT;
