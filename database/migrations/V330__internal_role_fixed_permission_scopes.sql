-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V182__role_fixed_permission_scopes.sql.
-- Original SHA-256: 86a62eece202ac9741a960ced149bf6220ab47d6036fcebd08ef038c4cf4fc1a. See shenma_whole_121_manifest.json.
-- Canonicalize single-scope grants only when a role is explicitly saved.
-- Existing roles, assignments, resolvers, denies and ACLs remain unchanged.
CREATE OR REPLACE FUNCTION security.save_permission_role(p_id uuid, p_body jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE ws uuid:=common.current_workspace_id(); old jsonb:='{}'; result jsonb; rid uuid:=COALESCE(p_id,gen_random_uuid());
 g jsonb; next_version integer;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('account-management:'||ws::text,0));
 IF NOT security.authorization_has('authorization.roles_manage') THEN RAISE insufficient_privilege; END IF;
 IF length(btrim(COALESCE(p_body->>'name',''))) NOT BETWEEN 1 AND 80
  OR length(btrim(COALESCE(p_body->>'reason',''))) NOT BETWEEN 1 AND 500
  OR p_body->>'status' NOT IN ('active','inactive') OR p_body->>'status' IS NULL THEN
  RAISE EXCEPTION '请填写有效的角色名称、状态和变更原因' USING ERRCODE='22023';
 END IF;
 PERFORM security.validate_authorization_grants(p_body->'permissions');
 -- Validate explicit inputs first: illegal scopes still fail instead of
 -- being promoted. Only a normal role save canonicalizes legacy inheritance.
 SELECT jsonb_set(p_body,'{permissions}',COALESCE(jsonb_agg(
   CASE WHEN COALESCE(entry->>'scope','inherit')='inherit' AND cardinality(c.scopes)=1
    THEN entry||jsonb_build_object('scope',c.scopes[1]) ELSE entry END ORDER BY ord),'[]'::jsonb))
  INTO p_body
  FROM jsonb_array_elements(p_body->'permissions') WITH ORDINALITY AS grants(entry,ord)
  JOIN config.permission_catalog c ON c.code=entry->>'permission';
 IF p_id IS NOT NULL THEN
  SELECT to_jsonb(r)||jsonb_build_object('permissions',(SELECT COALESCE(jsonb_agg(to_jsonb(prg)),'[]') FROM config.permission_role_grant prg WHERE prg.role_id=r.id))
   INTO old FROM config.permission_role r WHERE r.id=p_id AND r.workspace_id=ws FOR UPDATE;
  IF old IS NULL THEN RAISE EXCEPTION '角色不存在或不属于当前公司' USING ERRCODE='42501'; END IF;
  IF (old->>'version_no')::integer IS DISTINCT FROM (p_body->>'version_no')::integer THEN
   RAISE EXCEPTION '权限配置已变化，请刷新后重试' USING ERRCODE='40001';
  END IF;
  UPDATE config.permission_role SET name=btrim(p_body->>'name'),description=COALESCE(p_body->>'description',''),
   status=p_body->>'status',expires_at=NULLIF(p_body->>'expires_at','')::timestamptz,version_no=version_no+1,updated_at=clock_timestamp() WHERE id=p_id RETURNING version_no INTO next_version;
  DELETE FROM config.permission_role_grant WHERE role_id=p_id AND workspace_id=ws;
 ELSE
  INSERT INTO config.permission_role(id,workspace_id,name,description,status,expires_at)
   VALUES(rid,ws,btrim(p_body->>'name'),COALESCE(p_body->>'description',''),p_body->>'status',NULLIF(p_body->>'expires_at','')::timestamptz) RETURNING version_no INTO next_version;
 END IF;
 FOR g IN SELECT * FROM jsonb_array_elements(p_body->'permissions') LOOP
  INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code,team_ids)
   VALUES(ws,rid,g->>'permission',COALESCE(g->>'scope','inherit'),ARRAY(SELECT v::uuid FROM jsonb_array_elements_text(COALESCE(g->'team_ids','[]')) v));
 END LOOP;
 IF EXISTS(SELECT 1 FROM config.permission_role WHERE id=rid AND builtin_role_code='administrator' AND expires_at IS NOT NULL) THEN
  RAISE EXCEPTION '公司内置管理员角色不能设置到期时间' USING ERRCODE='22023'; END IF;
 PERFORM security.require_permission_administrator();
 result:=jsonb_build_object('id',rid,'version_no',next_version,'name',btrim(p_body->>'name'),'status',p_body->>'status','permissions',p_body->'permissions','expires_at',p_body->'expires_at');
 INSERT INTO config.authorization_audit(workspace_id,actor_user_ref_id,subject_type,subject_id,before_snapshot,after_snapshot,reason)
  VALUES(ws,common.current_user_ref_id(),'role',rid,old,result,btrim(p_body->>'reason'));
 PERFORM set_config('app.authorization_context','',true);
 RETURN result;
END $function$
;
