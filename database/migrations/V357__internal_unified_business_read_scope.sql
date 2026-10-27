-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V209__unified_business_read_scope.sql.
-- Original SHA-256: 85d2c1429703e020d903cba4d8d9f03ec72f9d0d873de4c0a57870d8d0e7dd40. See shenma_whole_121_manifest.json.
BEGIN;
-- SB120-AUTH-001: explicit dynamic read templates resolved once into effective grants.
-- Release track: frozen 43a648/V204 + V209. V205-V208 are separately reserved WIP,
-- absent from this reviewed candidate and never recorded as applied. No old checksum changes.
ALTER TABLE config.permission_role_grant DROP CONSTRAINT permission_role_grant_scope_code_check;
ALTER TABLE config.permission_role_grant ADD CONSTRAINT permission_role_grant_scope_code_check
 CHECK(scope_code IN ('inherit','self','assigned','teams','workspace','departments'));
ALTER TABLE config.permission_role ADD COLUMN legacy_fde_visit_guard boolean NOT NULL DEFAULT false;
UPDATE config.permission_role SET legacy_fde_visit_guard=true
 WHERE builtin_role_code IN ('fde','fde_lead') AND version_no=1;
CREATE TABLE config.business_read_policy_upgrade (
 role_id uuid PRIMARY KEY REFERENCES config.permission_role(id), workspace_id uuid NOT NULL,
 policy_version text NOT NULL, before_grants jsonb NOT NULL, after_grants jsonb NOT NULL,
 upgraded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON config.business_read_policy_upgrade FROM PUBLIC,salegent_feishu_worker;
ALTER TABLE config.business_read_policy_upgrade ENABLE ROW LEVEL SECURITY;
ALTER TABLE config.business_read_policy_upgrade FORCE ROW LEVEL SECURITY;

UPDATE config.permission_catalog SET scopes=array_append(scopes,'departments') WHERE code=ANY(ARRAY['directory.read','overview.read','battle_map.read','customer.read','opportunity.read','visit.read','task.read','risk.read','dashboard.read','profile.sales_read','profile.fde_read','profile.fde_activity','actual.read','demo_scene.read','target.read','target.history']) AND NOT ('departments'=ANY(scopes));

INSERT INTO config.permission_catalog(code,label,module,scopes,sensitive) VALUES
 ('visit.export','导出跟进记录','跟进记录',ARRAY['self','assigned','teams','workspace'],true),
 ('report.export','导出或复制经营报告','经营报告',ARRAY['self','assigned','teams','workspace'],true)
 ON CONFLICT(code) DO NOTHING;
INSERT INTO control.permission_module(permission_code,module_code) VALUES('visit.export','core'),('report.export','core')
 ON CONFLICT DO NOTHING;
-- No default export grants: broader reads never confer export authority.

INSERT INTO config.business_read_policy_upgrade(role_id,workspace_id,policy_version,before_grants,after_grants)
 SELECT r.id,r.workspace_id,'business-read-v2',COALESCE(jsonb_agg(to_jsonb(g) ORDER BY g.permission_code),'[]'), '[]'
 FROM config.permission_role r JOIN config.permission_role_grant g ON g.role_id=r.id
 WHERE r.builtin_role_code IN ('sales','supervisor','manager','fde','fde_lead') GROUP BY r.id;
-- Upgrade only grants still carrying their shipped default. Explicit customized scopes,
-- omitted/disabled grants, account denies and custom role templates remain intact.
UPDATE config.permission_role_grant g SET scope_code=CASE WHEN r.builtin_role_code IN ('sales','fde') THEN 'departments' ELSE 'workspace' END
 FROM config.permission_role r,config.permission_role_default d
 WHERE g.role_id=r.id AND d.role_code=r.builtin_role_code
 AND r.builtin_role_code IN ('sales','supervisor','manager','fde','fde_lead')
 AND g.permission_code=ANY(ARRAY['directory.read','overview.read','battle_map.read','customer.read','opportunity.read','visit.read','task.read','risk.read','dashboard.read','profile.sales_read','profile.fde_read','profile.fde_activity','actual.read','demo_scene.read','target.read','target.history']) AND cardinality(g.team_ids)=0
 AND EXISTS(SELECT 1 FROM jsonb_array_elements(d.permissions) p
   WHERE p->>'permission'=g.permission_code AND COALESCE(p->>'scope','inherit')=g.scope_code
     AND COALESCE(p->'team_ids','[]'::jsonb)='[]'::jsonb);
UPDATE config.permission_role_default d SET permissions=(SELECT jsonb_agg(
 CASE WHEN p->>'permission'=ANY(ARRAY['directory.read','overview.read','battle_map.read','customer.read','opportunity.read','visit.read','task.read','risk.read','dashboard.read','profile.sales_read','profile.fde_read','profile.fde_activity','actual.read','demo_scene.read','target.read','target.history']) THEN p||jsonb_build_object('scope',
 CASE WHEN d.role_code IN ('sales','fde') THEN 'departments' ELSE 'workspace' END) ELSE p END ORDER BY ord)
 FROM jsonb_array_elements(d.permissions) WITH ORDINALITY x(p,ord))
 WHERE d.role_code IN ('sales','supervisor','manager','fde','fde_lead');
UPDATE config.business_read_policy_upgrade u SET after_grants=(SELECT jsonb_agg(to_jsonb(g) ORDER BY g.permission_code)
 FROM config.permission_role_grant g WHERE g.role_id=u.role_id);
UPDATE config.permission_role r SET version_no=version_no+1,updated_at=clock_timestamp()
 FROM config.business_read_policy_upgrade u WHERE u.role_id=r.id AND u.before_grants IS DISTINCT FROM u.after_grants;
UPDATE config.authorization_scope_revision SET revision=revision+1;

CREATE OR REPLACE FUNCTION security._authorization_grants_for_state(p_workspace uuid, p_user uuid,p_state text)
 RETURNS TABLE(permission_code text, scope_code text, team_ids uuid[], source_code text, source_id text, effect text)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 WITH authority AS MATERIALIZED (
  SELECT security._authorization_administrator_for_state(p_workspace,p_user,p_state) administrator,
   security.active_authorization_delegation(p_workspace,p_user) delegation
 ), available_permissions AS MATERIALIZED (
  SELECT p.permission_code FROM control.permission_module p LEFT JOIN control.tenant_module t
   ON t.workspace_id=p_workspace AND t.module_code=p.module_code
  GROUP BY p.permission_code HAVING bool_and(t.enabled IS TRUE AND (t.expires_at IS NULL OR t.expires_at>clock_timestamp()))
 ), person AS (
  SELECT u.id FROM platform.user_ref u JOIN platform.workspace w ON w.id=u.workspace_id
  WHERE u.id=p_user AND u.workspace_id=p_workspace AND u.status='active' AND u.deleted_at IS NULL
   AND p_state IN ('active','closed') AND w.status=p_state AND w.deleted_at IS NULL
   AND EXISTS(SELECT 1 FROM platform.role_binding identity_role WHERE identity_role.workspace_id=p_workspace
    AND identity_role.user_ref_id=p_user AND clock_timestamp()>=identity_role.valid_from AND clock_timestamp()<identity_role.valid_to
    AND (identity_role.role_code IN ('manager','operations','administrator') OR EXISTS(
     SELECT 1 FROM security.account_team_scope(p_workspace,p_user,identity_role.role_code))))
 ), appointments AS (
  SELECT DISTINCT b.role_code,b.data_scope_code,
   ARRAY(SELECT s.team_id::uuid FROM security.account_team_scope(p_workspace,p_user,b.role_code) s) teams
  FROM platform.role_binding b JOIN person p ON p.id=b.user_ref_id
  WHERE b.workspace_id=p_workspace AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
   AND (b.role_code IN ('manager','operations','administrator') OR EXISTS(
     SELECT 1 FROM security.account_team_scope(p_workspace,p_user,b.role_code)))
 ), sources AS (
  SELECT r.id,r.version_no,r.legacy_fde_visit_guard,r.builtin_role_code source_code,
   CASE a.data_scope_code WHEN 'team' THEN 'teams' ELSE a.data_scope_code END scope_code,a.teams
  FROM appointments a JOIN config.permission_role r ON r.workspace_id=p_workspace
   AND r.builtin_role_code=a.role_code AND r.status='active' AND (r.expires_at IS NULL OR r.expires_at>clock_timestamp())
  UNION ALL
  SELECT r.id,r.version_no,r.legacy_fde_visit_guard,'assigned_role',a.scope_code,a.team_ids
  FROM config.account_permission_role a JOIN person p ON p.id=a.user_ref_id
  JOIN config.permission_role r ON r.id=a.role_id AND r.workspace_id=a.workspace_id AND r.status='active' AND (r.expires_at IS NULL OR r.expires_at>clock_timestamp())
  WHERE a.workspace_id=p_workspace AND (a.expires_at IS NULL OR a.expires_at>clock_timestamp())
   AND security.delegated_authority_valid(p_workspace,p_user,a.delegation_user_ref_id,a.delegation_version)
   AND (a.delegation_user_ref_id IS NULL OR a.approved_role_version=r.version_no)
 ), combined AS (
  SELECT g.permission_code,CASE g.scope_code WHEN 'departments' THEN 'teams' WHEN 'inherit' THEN s.scope_code ELSE g.scope_code END scope_code,
   CASE WHEN g.scope_code='departments' THEN ARRAY(SELECT DISTINCT a.team_id::uuid FROM platform.role_binding b CROSS JOIN LATERAL security.account_team_scope(p_workspace,p_user,b.role_code) a WHERE b.workspace_id=p_workspace AND b.user_ref_id=p_user AND (s.source_code='assigned_role' OR b.role_code=s.source_code) AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to ORDER BY a.team_id::uuid) WHEN g.scope_code='teams' THEN g.team_ids WHEN g.scope_code='inherit' AND s.scope_code='teams' THEN s.teams ELSE '{}'::uuid[] END teams,
   s.source_code,s.id::text source_id,'allow'::text effect
  FROM sources s JOIN config.permission_role_grant g ON g.role_id=s.id AND g.workspace_id=p_workspace
  WHERE NOT (s.source_code IN ('fde','fde_lead') AND (s.version_no=1 OR s.legacy_fde_visit_guard) AND g.permission_code IN
   ('visit.create','visit.supplement','visit.upload','visit.retry_import','visit.download_original','visit.structure','visit.quality_review'))
   OR security.authorization_legacy_fde_visit(p_workspace,p_user,s.source_code)
  UNION ALL
  SELECT o.permission_code,o.scope_code,o.team_ids,'account_override',p_user::text,o.effect
  FROM config.account_permission_override o JOIN person p ON p.id=o.user_ref_id WHERE o.workspace_id=p_workspace AND (o.expires_at IS NULL OR o.expires_at>clock_timestamp()) AND security.delegated_authority_valid(p_workspace,p_user,o.delegation_user_ref_id,o.delegation_version)
 ), valid AS (
  SELECT c.*,ARRAY(SELECT t.id FROM platform.team t WHERE t.id=ANY(c.teams) AND t.workspace_id=p_workspace
    AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to ORDER BY t.id) active_teams
  FROM combined c JOIN config.permission_catalog d ON d.code=c.permission_code
  WHERE c.effect='deny' OR c.scope_code=ANY(d.scopes)
 )
 SELECT v.permission_code,v.scope_code,v.active_teams,v.source_code,v.source_id,v.effect FROM valid v CROSS JOIN authority a
 WHERE (v.scope_code<>'teams' OR cardinality(v.active_teams)>0 OR v.effect='deny')
 AND (v.effect='deny' OR (v.permission_code IN (SELECT permission_code FROM available_permissions)
  AND (a.administrator OR v.permission_code NOT IN ('authorization.roles_manage','account.create','account.password_policy','organization.manage'))
  AND (a.administrator OR a.delegation IS NOT NULL OR v.permission_code NOT IN ('authorization.accounts_manage','account.update','account.reset_password','account.unlock'))));
$function$
;



CREATE OR REPLACE FUNCTION security.validate_authorization_grants(p_items jsonb,p_overrides boolean DEFAULT false) RETURNS void
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE g jsonb; teams uuid[]; v_code text; v_scope text; denied boolean;
BEGIN
 IF jsonb_typeof(p_items) IS DISTINCT FROM 'array' OR jsonb_array_length(p_items)>300 THEN
  RAISE EXCEPTION '权限列表格式无效' USING ERRCODE='22023';
 END IF;
 IF (SELECT count(*)<>count(DISTINCT x->>'permission') FROM jsonb_array_elements(p_items) x) THEN
  RAISE EXCEPTION '权限不能重复' USING ERRCODE='22023';
 END IF;
 FOR g IN SELECT * FROM jsonb_array_elements(p_items) LOOP
  v_code:=g->>'permission'; v_scope:=COALESCE(g->>'scope','inherit'); denied:=p_overrides AND g->>'effect'='deny';
  IF NOT EXISTS(SELECT 1 FROM config.permission_catalog d WHERE d.code=v_code
   AND (denied OR v_scope='inherit' OR v_scope=ANY(d.scopes)))
   OR (p_overrides AND (g->>'effect' NOT IN ('allow','deny') OR g->>'effect' IS NULL))
   OR (p_overrides AND NOT denied AND v_scope IN ('inherit','departments')) OR (denied AND v_scope<>'inherit') THEN
   RAISE EXCEPTION '权限或范围配置无效' USING ERRCODE='22023';
  END IF;
  IF jsonb_typeof(COALESCE(g->'team_ids','[]'))<>'array' THEN RAISE EXCEPTION '团队列表无效' USING ERRCODE='22023'; END IF;
  teams:=ARRAY(SELECT v::uuid FROM jsonb_array_elements_text(COALESCE(g->'team_ids','[]')) v);
  IF cardinality(teams)>100 OR (v_scope='teams')<>(cardinality(teams)>0)
   OR cardinality(teams)<>(SELECT count(DISTINCT t) FROM unnest(teams) t)
   OR EXISTS(SELECT 1 FROM unnest(teams) tid WHERE NOT EXISTS(
    SELECT 1 FROM platform.team t WHERE t.id=tid AND t.workspace_id=common.current_workspace_id()
     AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to)) THEN
   RAISE EXCEPTION '请选择本公司有效且不重复的授权团队' USING ERRCODE='22023';
  END IF;
 END LOOP;
END $$;

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
   legacy_fde_visit_guard=false,status=p_body->>'status',expires_at=NULLIF(p_body->>'expires_at','')::timestamptz,version_no=version_no+1,updated_at=clock_timestamp() WHERE id=p_id RETURNING version_no INTO next_version;
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


-- The action/record evaluator no longer invokes V202/V203 role adapters.
CREATE OR REPLACE FUNCTION security.authorization_allows(
 p_permission text,p_workspace uuid,p_owner uuid,p_teams uuid[] DEFAULT '{}',p_assigned boolean DEFAULT false)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH grants AS MATERIALIZED (SELECT * FROM security.authorization_current_grants()),
 required AS (
   SELECT p_permission code UNION
   SELECT current_setting('app.authorized_feature',true)
   WHERE p_permission IN ('customer.read','opportunity.read','visit.read','task.read','risk.read')
   AND current_setting('app.authorized_feature',true) IN
    ('overview.read','battle_map.read','dashboard.read','profile.sales_read','profile.fde_read','profile.fde_activity',
     'actual.read','demo_scene.read','customer.export','opportunity.export','visit.export','report.export','visit.download_original')
 )
 SELECT p_workspace=common.current_workspace_id() AND NOT EXISTS(
  SELECT 1 FROM required r WHERE EXISTS(SELECT 1 FROM grants g WHERE g.permission_code=r.code AND g.effect='deny')
   OR NOT EXISTS(SELECT 1 FROM grants g WHERE g.permission_code=r.code AND g.effect='allow' AND (
    g.scope_code='workspace' OR (g.scope_code='self' AND p_owner=common.current_user_ref_id())
    OR (g.scope_code='assigned' AND p_assigned) OR (g.scope_code='teams' AND g.team_ids&&p_teams))));
$$;
-- Compatibility names delegate to the same normalized grants, never inspect role labels.
CREATE OR REPLACE FUNCTION security.authorization_business_read(
 p_permission text,p_workspace uuid,p_owner uuid,p_teams uuid[] DEFAULT '{}',p_assigned boolean DEFAULT false)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_allows(p_permission,p_workspace,p_owner,p_teams,p_assigned);
$$;
CREATE OR REPLACE FUNCTION security.authorization_business_department(p_permission text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT NOT EXISTS(SELECT 1 FROM security.authorization_current_grants() WHERE permission_code=p_permission AND effect='deny')
 AND EXISTS(SELECT 1 FROM security.authorization_current_grants()
 WHERE permission_code=p_permission AND effect='allow' AND scope_code IN ('teams','workspace'));
$$;
CREATE OR REPLACE FUNCTION security.authorization_read_feature(p_resource text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT CASE WHEN current_setting('app.authorized_feature',true)='dashboard.ranking' THEN 'dashboard.ranking'
 ELSE p_resource||'.read' END;
$$;

CREATE OR REPLACE FUNCTION security.authorization_subject(
 p_permission text,p_scope text,p_user uuid,p_team uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE(CASE
  WHEN p_scope='department' AND p_user IS NULL AND p_team IS NULL THEN
   security.authorization_business_department(p_permission)
  WHEN p_scope='team' AND p_user IS NULL THEN EXISTS(
   SELECT 1 FROM platform.team t WHERE t.id=p_team AND t.workspace_id=common.current_workspace_id()
    AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
    AND security.authorization_allows(p_permission,t.workspace_id,NULL,ARRAY[t.id],false))
  WHEN p_scope='person' AND p_team IS NULL THEN EXISTS(
   SELECT 1 FROM platform.user_ref u WHERE u.id=p_user AND u.workspace_id=common.current_workspace_id()
    AND u.status='active' AND u.deleted_at IS NULL
    AND security.authorization_allows(p_permission,u.workspace_id,u.id,ARRAY(
      SELECT tm.team_id FROM platform.team_membership tm JOIN platform.team t
       ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
      WHERE tm.user_ref_id=u.id AND tm.workspace_id=u.workspace_id AND t.status='active' AND t.deleted_at IS NULL
       AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
       AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
    ),u.id=common.current_user_ref_id()))
  ELSE false END,false);
$$;


INSERT INTO ops.schema_migration(version,description) VALUES('V357','Unified explicit business read grants and independent resource gates');
COMMIT;
