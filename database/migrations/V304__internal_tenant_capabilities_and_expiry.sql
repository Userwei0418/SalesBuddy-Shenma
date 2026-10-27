-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V156__tenant_capabilities_and_expiry.sql.
-- Original SHA-256: 83ac00c3c5ff68eb2cbe446ab88d5d52a0bf99b4c7412c0f6934e2166b95390b. See shenma_whole_121_manifest.json.
BEGIN;
-- Stable product capabilities are separate from display labels and company roles.
CREATE TABLE control.module_catalog(
 code text PRIMARY KEY,label text NOT NULL,required boolean NOT NULL DEFAULT false,
 default_enabled boolean NOT NULL DEFAULT true
);
INSERT INTO control.module_catalog VALUES
 ('core','账号、组织与基础管理',true,true),('customer','客户与作战地图',false,true),
 ('opportunity','商机管理',false,true),('visit','跟进记录与材料',false,true),
 ('task','待办事项',false,true),('insight','实绩、看板、目标与画像',false,true),
 ('partner','伙伴及演示方案',false,true),('ai','AI 分析与模型配置',false,false),
 ('feishu','飞书同步',false,false),('history','历史数据导入',false,false);
CREATE TABLE control.permission_module(
 permission_code text NOT NULL REFERENCES config.permission_catalog(code),
 module_code text NOT NULL REFERENCES control.module_catalog(code),PRIMARY KEY(permission_code,module_code)
);
INSERT INTO control.permission_module SELECT code,CASE split_part(code,'.',1)
 WHEN 'customer' THEN 'customer' WHEN 'battle_map' THEN 'customer' WHEN 'overview' THEN 'customer'
 WHEN 'opportunity' THEN 'opportunity' WHEN 'visit' THEN 'visit' WHEN 'task' THEN 'task'
 WHEN 'advice' THEN 'ai' WHEN 'risk' THEN 'ai' WHEN 'agent' THEN 'ai' WHEN 'ai' THEN 'ai'
 WHEN 'actual' THEN 'insight' WHEN 'dashboard' THEN 'insight' WHEN 'profile' THEN 'insight' WHEN 'target' THEN 'insight'
 WHEN 'partner' THEN 'partner' WHEN 'demo_scene' THEN 'partner' WHEN 'feishu' THEN 'feishu' WHEN 'history' THEN 'history'
 ELSE 'core' END FROM config.permission_catalog;
INSERT INTO control.permission_module VALUES
 ('visit.transcribe','ai'),('visit.structure','ai'),('visit.quality_review','ai'),
 ('profile.sales_review','ai'),('profile.fde_review','ai'),('task.create_customer','opportunity'),
 ('advice.customer','customer'),('advice.opportunity','opportunity'),('advice.visit','visit');
CREATE TABLE control.tenant_module(
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),module_code text NOT NULL REFERENCES control.module_catalog(code),
 enabled boolean NOT NULL,expires_at timestamptz,source text NOT NULL,
 PRIMARY KEY(workspace_id,module_code),CHECK(expires_at IS NULL OR isfinite(expires_at))
);
-- Explicit compatibility snapshot, not an implicit allow for missing settings.
INSERT INTO control.tenant_module SELECT w.id,m.code,true,NULL,'migration-v156-compatibility'
 FROM platform.workspace w CROSS JOIN control.module_catalog m;
CREATE FUNCTION control.initialize_tenant_modules() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 INSERT INTO control.tenant_module SELECT NEW.id,code,default_enabled,NULL,'product-v1' FROM control.module_catalog;
 RETURN NEW;
END $$;
CREATE TRIGGER initialize_tenant_modules AFTER INSERT ON platform.workspace
 FOR EACH ROW EXECUTE FUNCTION control.initialize_tenant_modules();
CREATE FUNCTION security.tenant_permission_enabled(p_workspace uuid,p_permission text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM control.permission_module WHERE permission_code=p_permission)
 AND NOT EXISTS(SELECT 1 FROM control.permission_module p LEFT JOIN control.tenant_module t
  ON t.workspace_id=p_workspace AND t.module_code=p.module_code WHERE p.permission_code=p_permission
  AND (t.enabled IS DISTINCT FROM true OR (t.expires_at IS NOT NULL AND t.expires_at<=clock_timestamp())));
$$;
CREATE FUNCTION control.module_settings(p_session text,p_workspace uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
 PERFORM control.require_actor(p_session);
 SELECT jsonb_build_object('workspace_id',w.id,'version_no',w.version_no,'modules',
 (SELECT jsonb_agg(jsonb_build_object('code',m.code,'label',m.label,'required',m.required,
   'enabled',COALESCE(t.enabled,false),'expires_at',t.expires_at,'source',t.source) ORDER BY m.code)
  FROM control.module_catalog m LEFT JOIN control.tenant_module t ON t.workspace_id=w.id AND t.module_code=m.code))
 INTO result FROM platform.workspace w WHERE id=p_workspace AND deleted_at IS NULL;
 IF result IS NULL THEN RAISE EXCEPTION 'TENANT_NOT_FOUND' USING ERRCODE='P7404'; END IF;
 RETURN result;
END $$;
CREATE FUNCTION control.save_modules(p_session text,p_workspace uuid,p_version integer,p_modules jsonb,p_reason text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=control.require_actor(p_session); current_version integer; old jsonb; item jsonb; expiry timestamptz;
BEGIN
 SELECT version_no INTO current_version FROM platform.workspace WHERE id=p_workspace AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'TENANT_NOT_FOUND' USING ERRCODE='P7404'; END IF;
 IF current_version IS DISTINCT FROM p_version THEN RAISE EXCEPTION 'VERSION_CONFLICT' USING ERRCODE='P7409'; END IF;
 IF COALESCE(length(btrim(p_reason)),0) NOT BETWEEN 1 AND 500 OR jsonb_typeof(p_modules) IS DISTINCT FROM 'array'
  OR (SELECT count(*) FROM control.module_catalog)<>jsonb_array_length(p_modules)
  OR (SELECT count(DISTINCT m->>'code') FROM jsonb_array_elements(p_modules) m)<>jsonb_array_length(p_modules) THEN
  RAISE EXCEPTION 'INVALID_MODULE_SETTINGS' USING ERRCODE='P7422'; END IF;
 old:=control.module_settings(p_session,p_workspace);
 FOR item IN SELECT * FROM jsonb_array_elements(p_modules) LOOP
  IF jsonb_typeof(item->'enabled') IS DISTINCT FROM 'boolean' OR NOT EXISTS(
    SELECT 1 FROM control.module_catalog WHERE code=item->>'code') THEN
   RAISE EXCEPTION 'INVALID_MODULE_SETTINGS' USING ERRCODE='P7422'; END IF;
  expiry:=NULLIF(item->>'expires_at','')::timestamptz;
  IF expiry IS NOT NULL AND NOT isfinite(expiry) THEN RAISE EXCEPTION 'INVALID_MODULE_EXPIRY' USING ERRCODE='P7422'; END IF;
  IF EXISTS(SELECT 1 FROM control.module_catalog WHERE code=item->>'code' AND required)
   AND ((item->>'enabled')::boolean IS NOT TRUE OR expiry IS NOT NULL) THEN
   RAISE EXCEPTION 'CORE_MODULE_REQUIRED' USING ERRCODE='P7422'; END IF;
  INSERT INTO control.tenant_module VALUES(p_workspace,item->>'code',(item->>'enabled')::boolean,expiry,'platform-configured')
   ON CONFLICT(workspace_id,module_code) DO UPDATE SET enabled=EXCLUDED.enabled,expires_at=EXCLUDED.expires_at,source=EXCLUDED.source;
 END LOOP;
 UPDATE ops.job SET status='failed',lease_token=NULL,locked_until=NULL,locked_by=NULL,attempts=greatest(0,attempts-1)
  WHERE workspace_id=p_workspace AND status='running' AND NOT security.workspace_job_enabled(p_workspace,job_type);
 UPDATE ops.feishu_event SET status='pending',lease_token=NULL,locked_until=NULL,attempts=greatest(0,attempts-1)
  WHERE workspace_id=p_workspace AND status='running' AND NOT security.workspace_module_enabled(p_workspace,'feishu');
 UPDATE platform.workspace SET version_no=version_no+1,updated_at=clock_timestamp() WHERE id=p_workspace;
 INSERT INTO control.audit(operator_id,workspace_id,action,reason,before_state,after_state)
  VALUES(actor,p_workspace,'tenant.modules',btrim(p_reason),old,control.module_settings(p_session,p_workspace));
 RETURN control.module_settings(p_session,p_workspace);
END $$;
CREATE FUNCTION security.tenant_modules() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE(jsonb_agg(jsonb_build_object('code',m.code,'label',m.label,'enabled',
  t.enabled AND (t.expires_at IS NULL OR t.expires_at>clock_timestamp()),'expires_at',t.expires_at) ORDER BY m.code),'[]')
 FROM control.tenant_module t JOIN control.module_catalog m ON m.code=t.module_code
 WHERE t.workspace_id=common.current_workspace_id();
$$;
REVOKE ALL ON ALL TABLES IN SCHEMA control FROM PUBLIC;
REVOKE ALL ON FUNCTION control.initialize_tenant_modules(),control.module_settings(text,uuid),control.save_modules(text,uuid,integer,jsonb,text),security.tenant_permission_enabled(uuid,text),security.tenant_modules() FROM PUBLIC,salegent_feishu_worker;
ALTER TABLE config.permission_role ADD COLUMN expires_at timestamptz CHECK(expires_at IS NULL OR isfinite(expires_at));
ALTER TABLE config.account_permission_role ADD COLUMN expires_at timestamptz CHECK(expires_at IS NULL OR isfinite(expires_at));
ALTER TABLE config.account_permission_override ADD COLUMN expires_at timestamptz CHECK(expires_at IS NULL OR isfinite(expires_at));
CREATE OR REPLACE FUNCTION security.authorization_grants_for(p_workspace uuid, p_user uuid)
 RETURNS TABLE(permission_code text, scope_code text, team_ids uuid[], source_code text, source_id text, effect text)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 WITH person AS (
  SELECT u.id FROM platform.user_ref u JOIN platform.workspace w ON w.id=u.workspace_id
  WHERE u.id=p_user AND u.workspace_id=p_workspace AND u.status='active' AND u.deleted_at IS NULL
   AND w.status='active' AND w.deleted_at IS NULL
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
  SELECT r.id,r.version_no,r.builtin_role_code source_code,
   CASE a.data_scope_code WHEN 'team' THEN 'teams' ELSE a.data_scope_code END scope_code,a.teams
  FROM appointments a JOIN config.permission_role r ON r.workspace_id=p_workspace
   AND r.builtin_role_code=a.role_code AND r.status='active' AND (r.expires_at IS NULL OR r.expires_at>clock_timestamp())
  UNION ALL
  SELECT r.id,r.version_no,'assigned_role',a.scope_code,a.team_ids
  FROM config.account_permission_role a JOIN person p ON p.id=a.user_ref_id
  JOIN config.permission_role r ON r.id=a.role_id AND r.workspace_id=a.workspace_id AND r.status='active' AND (r.expires_at IS NULL OR r.expires_at>clock_timestamp())
  WHERE a.workspace_id=p_workspace AND (a.expires_at IS NULL OR a.expires_at>clock_timestamp())
 ), combined AS (
  SELECT g.permission_code,CASE g.scope_code WHEN 'inherit' THEN s.scope_code ELSE g.scope_code END scope_code,
   CASE WHEN g.scope_code='teams' THEN g.team_ids WHEN g.scope_code='inherit' AND s.scope_code='teams' THEN s.teams ELSE '{}'::uuid[] END teams,
   s.source_code,s.id::text source_id,'allow'::text effect
  FROM sources s JOIN config.permission_role_grant g ON g.role_id=s.id AND g.workspace_id=p_workspace
  WHERE NOT (s.source_code IN ('fde','fde_lead') AND s.version_no=1 AND g.permission_code IN
   ('visit.create','visit.supplement','visit.upload','visit.retry_import','visit.download_original','visit.structure','visit.quality_review'))
   OR security.authorization_legacy_fde_visit(p_workspace,p_user,s.source_code)
  UNION ALL
  SELECT o.permission_code,o.scope_code,o.team_ids,'account_override',p_user::text,o.effect
  FROM config.account_permission_override o JOIN person p ON p.id=o.user_ref_id WHERE o.workspace_id=p_workspace AND (o.expires_at IS NULL OR o.expires_at>clock_timestamp())
 ), valid AS (
  SELECT c.*,ARRAY(SELECT t.id FROM platform.team t WHERE t.id=ANY(c.teams) AND t.workspace_id=p_workspace
    AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to ORDER BY t.id) active_teams
  FROM combined c JOIN config.permission_catalog d ON d.code=c.permission_code
  WHERE c.effect='deny' OR c.scope_code=ANY(d.scopes)
 )
 SELECT v.permission_code,v.scope_code,v.active_teams,v.source_code,v.source_id,v.effect FROM valid v
 WHERE (v.scope_code<>'teams' OR cardinality(v.active_teams)>0 OR v.effect='deny')
 AND (v.effect='deny' OR security.tenant_permission_enabled(p_workspace,v.permission_code));
$function$
;
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
CREATE OR REPLACE FUNCTION security.save_account_authorization(p_user uuid, p_body jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE ws uuid:=common.current_workspace_id(); old jsonb; item jsonb; teams uuid[]; next_version integer; result jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('account-management:'||ws::text,0));
 IF NOT security.authorization_has('authorization.accounts_manage') THEN RAISE insufficient_privilege; END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.user_ref WHERE id=p_user AND workspace_id=ws AND deleted_at IS NULL) THEN RAISE insufficient_privilege; END IF;
 IF length(btrim(COALESCE(p_body->>'reason',''))) NOT BETWEEN 1 AND 500
  OR jsonb_typeof(p_body->'roles') IS DISTINCT FROM 'array' OR jsonb_array_length(p_body->'roles')>100 THEN
  RAISE EXCEPTION '账号授权格式或原因无效' USING ERRCODE='22023';
 END IF;
 PERFORM security.validate_authorization_grants(p_body->'overrides',true);
 SELECT COALESCE((SELECT version_no FROM config.account_authorization WHERE workspace_id=ws AND user_ref_id=p_user),0) INTO next_version;
 IF next_version IS DISTINCT FROM (p_body->>'version_no')::integer THEN
  RAISE EXCEPTION '账号权限已变化，请刷新后重试' USING ERRCODE='40001';
 END IF;
 old:=jsonb_build_object('version_no',next_version,
  'roles',(SELECT COALESCE(jsonb_agg(to_jsonb(a)),'[]') FROM config.account_permission_role a WHERE a.workspace_id=ws AND a.user_ref_id=p_user),
  'overrides',(SELECT COALESCE(jsonb_agg(to_jsonb(o)),'[]') FROM config.account_permission_override o WHERE o.workspace_id=ws AND o.user_ref_id=p_user));
 INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES(ws,p_user)
 ON CONFLICT(workspace_id,user_ref_id) DO UPDATE SET version_no=config.account_authorization.version_no+1,updated_at=clock_timestamp()
 RETURNING version_no INTO next_version;
 DELETE FROM config.account_permission_role WHERE workspace_id=ws AND user_ref_id=p_user;
 DELETE FROM config.account_permission_override WHERE workspace_id=ws AND user_ref_id=p_user;
 FOR item IN SELECT * FROM jsonb_array_elements(p_body->'roles') LOOP
  IF item->>'scope' NOT IN ('self','assigned','teams','workspace') OR item->>'scope' IS NULL
    OR jsonb_typeof(COALESCE(item->'team_ids','[]'))<>'array'
    OR NOT EXISTS(SELECT 1 FROM config.permission_role r WHERE r.id=(item->>'role_id')::uuid AND r.workspace_id=ws AND r.status='active') THEN
   RAISE EXCEPTION '请选择本公司有效权限角色和范围' USING ERRCODE='22023';
  END IF;
  teams:=ARRAY(SELECT v::uuid FROM jsonb_array_elements_text(COALESCE(item->'team_ids','[]')) v);
  IF cardinality(teams)>100 OR (item->>'scope'='teams')<>(cardinality(teams)>0)
   OR cardinality(teams)<>(SELECT count(DISTINCT t) FROM unnest(teams) t)
   OR EXISTS(SELECT 1 FROM unnest(teams) tid WHERE NOT EXISTS(SELECT 1 FROM platform.team t
    WHERE t.id=tid AND t.workspace_id=ws AND t.status='active' AND t.deleted_at IS NULL
    AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to)) THEN
   RAISE EXCEPTION '请选择本公司有效授权团队' USING ERRCODE='22023';
  END IF;
  INSERT INTO config.account_permission_role(workspace_id,user_ref_id,role_id,scope_code,team_ids,expires_at)
   VALUES(ws,p_user,(item->>'role_id')::uuid,item->>'scope',teams,NULLIF(item->>'expires_at','')::timestamptz);
 END LOOP;
 FOR item IN SELECT * FROM jsonb_array_elements(p_body->'overrides') LOOP
  INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code,team_ids,expires_at)
  VALUES(ws,p_user,item->>'permission',item->>'effect',CASE WHEN item->>'effect'='allow' THEN item->>'scope' END,
   ARRAY(SELECT v::uuid FROM jsonb_array_elements_text(COALESCE(item->'team_ids','[]')) v),NULLIF(item->>'expires_at','')::timestamptz);
 END LOOP;
 PERFORM security.require_permission_administrator();
 result:=jsonb_build_object('user_id',p_user,'version_no',next_version,'roles',p_body->'roles','overrides',p_body->'overrides');
 INSERT INTO config.authorization_audit(workspace_id,actor_user_ref_id,subject_type,subject_id,before_snapshot,after_snapshot,reason)
  VALUES(ws,common.current_user_ref_id(),'account',p_user,old,result,btrim(p_body->>'reason'));
 PERFORM set_config('app.authorization_context','',true);
 RETURN result;
END $function$
;
CREATE FUNCTION security.workspace_module_enabled(p_workspace uuid,p_module text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM control.tenant_module WHERE workspace_id=p_workspace AND module_code=p_module
 AND enabled AND (expires_at IS NULL OR expires_at>clock_timestamp()));
$$;
CREATE FUNCTION security.workspace_job_enabled(p_workspace uuid,p_type text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.workspace_module_enabled(p_workspace,CASE WHEN p_type='visit.import' THEN 'visit'
 WHEN p_type IN ('ai.connectivity','agent.run','sales_competency.review','battle_map.review',
 'customer.risk.review','business.advice','opportunity.change.review') THEN 'ai' ELSE 'core' END);
$$;
REVOKE ALL ON FUNCTION security.workspace_module_enabled(uuid,text),security.workspace_job_enabled(uuid,text) FROM PUBLIC;
-- In-flight writes take the same workspace lock as capability changes. They
-- either commit before the change, or observe it and refuse the old lease.
CREATE OR REPLACE FUNCTION security.feishu_lock_lease(p_event uuid,p_token uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; state text;
BEGIN
 SELECT workspace_id INTO ws FROM ops.feishu_event WHERE id=p_event;
 SELECT status INTO state FROM platform.workspace WHERE id=ws AND deleted_at IS NULL FOR SHARE;
 IF state IS DISTINCT FROM 'active' OR NOT security.workspace_module_enabled(ws,'feishu') THEN
  RAISE EXCEPTION 'SYNC_LEASE_LOST' USING ERRCODE='P7403'; END IF;
 PERFORM 1 FROM ops.feishu_event WHERE id=p_event AND lease_token=p_token AND status='running'
  AND locked_until>clock_timestamp() FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'SYNC_LEASE_LOST' USING ERRCODE='P7403'; END IF;
END $$;
CREATE OR REPLACE FUNCTION ops.claim_job(p_worker_id text,p_lock_seconds integer,
 p_job_types text[],p_exclude_types boolean DEFAULT false) RETURNS SETOF ops.job
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ops AS $$
DECLARE expired record;
BEGIN
 IF p_lock_seconds IS NULL OR p_exclude_types IS NULL
  OR (p_job_types IS NOT NULL AND array_position(p_job_types,NULL) IS NOT NULL)
  OR p_lock_seconds < 3 OR p_lock_seconds > 3600 OR NULLIF(btrim(p_worker_id),'') IS NULL THEN
  RAISE EXCEPTION 'invalid worker lease settings';
 END IF;
 -- A process may stop after the result commits but before acknowledging its queue row.
 UPDATE ops.job j SET status='succeeded',completed_at=COALESCE(j.completed_at,clock_timestamp()),
   locked_by=NULL,locked_until=NULL,lease_token=NULL,updated_at=clock_timestamp()
 WHERE security.workspace_is_active(j.workspace_id) AND security.workspace_job_enabled(j.workspace_id,j.job_type) AND j.status IN ('queued','failed','running')
 AND (p_job_types IS NULL OR (j.job_type=ANY(p_job_types))<>p_exclude_types)
 AND (j.locked_until IS NULL OR j.locked_until<=clock_timestamp())
 AND (EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=j.id)
   OR (j.job_type='agent.run' AND EXISTS(SELECT 1 FROM agent.run r
       WHERE r.id=j.aggregate_id AND r.status IN ('succeeded','waiting_human')))
   OR (j.job_type='visit.import' AND EXISTS(SELECT 1 FROM activity.visit_import i
       WHERE i.id=j.aggregate_id AND i.status='succeeded'))
   OR (j.job_type='sales_competency.review' AND EXISTS(SELECT 1 FROM insight.sales_competency_review r
       WHERE r.id=j.aggregate_id AND r.status='succeeded')));
 -- Bounded attempts also cover processes that disappeared without an exception handler.
 FOR expired IN
  UPDATE ops.job SET status='dead_letter',completed_at=clock_timestamp(),locked_by=NULL,
   locked_until=NULL,lease_token=NULL,last_error_code='LEASE_ATTEMPTS_EXHAUSTED',
   last_error_detail='后台任务中断且已达到重试上限，请重试或联系管理员',updated_at=clock_timestamp()
  WHERE security.workspace_is_active(workspace_id) AND security.workspace_job_enabled(workspace_id,job_type) AND status IN ('queued','failed','running') AND attempts>=max_attempts
    AND (p_job_types IS NULL OR (job_type=ANY(p_job_types))<>p_exclude_types)
    AND (locked_until IS NULL OR locked_until<=clock_timestamp()) RETURNING *
 LOOP
  IF expired.job_type='agent.run' THEN
   UPDATE agent.run SET status='failed',completed_at=clock_timestamp(),
    error_code='LEASE_ATTEMPTS_EXHAUSTED',error_detail=expired.last_error_detail
    WHERE id=expired.aggregate_id AND status IN ('queued','running');
  ELSIF expired.job_type='visit.import' THEN
   UPDATE activity.visit_import SET status='failed',error_message=expired.last_error_detail,updated_at=clock_timestamp()
    WHERE id=expired.aggregate_id AND status IN ('queued','processing');
  ELSIF expired.job_type='business.advice' THEN
   UPDATE insight.business_advice SET status='failed',error_code='LEASE_ATTEMPTS_EXHAUSTED',
     completed_at=clock_timestamp() WHERE id=expired.aggregate_id AND status IN ('queued','running');
  ELSIF expired.job_type='sales_competency.review' THEN
   UPDATE insight.sales_competency_review SET status='failed',error_code='LEASE_ATTEMPTS_EXHAUSTED',
    error_detail=expired.last_error_detail WHERE id=expired.aggregate_id AND status IN ('queued','running');
  END IF;
 END LOOP;
 RETURN QUERY
 UPDATE ops.job j SET status='running',attempts=j.attempts+1,locked_by=p_worker_id,
  lease_token=gen_random_uuid(),locked_until=clock_timestamp()+make_interval(secs=>p_lock_seconds),
  started_at=COALESCE(j.started_at,clock_timestamp()),updated_at=clock_timestamp()
 WHERE j.id=(SELECT candidate.id FROM ops.job candidate
  WHERE security.workspace_is_active(candidate.workspace_id) AND security.workspace_job_enabled(candidate.workspace_id,candidate.job_type) AND candidate.status IN ('queued','failed','running') AND candidate.attempts<candidate.max_attempts
   AND (p_job_types IS NULL OR (candidate.job_type=ANY(p_job_types))<>p_exclude_types)
   AND candidate.available_at<=clock_timestamp()
   AND (candidate.locked_until IS NULL OR candidate.locked_until<=clock_timestamp())
   AND NOT EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=candidate.id)
  ORDER BY candidate.priority DESC,candidate.available_at,candidate.created_at,candidate.id
  FOR UPDATE SKIP LOCKED LIMIT 1)
 RETURNING j.*;
END $$;


CREATE OR REPLACE FUNCTION ops.feishu_source(p_connection uuid,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; result jsonb; extra jsonb; original_value jsonb; original_source text; opportunity_meta jsonb;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 IF ws IS NULL OR NOT security.workspace_is_active(ws) OR NOT security.workspace_module_enabled(ws,'feishu') THEN RAISE insufficient_privilege; END IF;
 IF p_kind='period_actual_snapshot' THEN
  SELECT jsonb_build_object('id',s.id,'opportunity_id',s.opportunity_id,'customer_id',o.customer_id,
   'opportunity_name',o.name,'customer_name',c.name,'year',s.year,'quarter',s.quarter,'kind',s.kind,
   'source_field',s.source_field,'raw_amount',s.raw_amount,'source_unit',s.source_unit,'tax_basis',s.tax_basis,
   'source_record_id',s.source_record_id,'import_batch_id',s.import_batch_id,'created_at',s.created_at,
   'source_system',b.source_system,'source_base_id',b.source_base_id,
   'source_table_id',r.source_table_id,'source_external_record_id',r.source_record_id,
   'company_name',(SELECT name FROM platform.workspace WHERE id=ws)) INTO result
  FROM crm.opportunity_period_actual_snapshot s
  JOIN crm.opportunity o ON o.id=s.opportunity_id AND o.workspace_id=s.workspace_id
  JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
  JOIN ops.crm_import_record r ON r.id=s.source_record_id AND r.workspace_id=s.workspace_id
  JOIN ops.crm_import_batch b ON b.id=s.import_batch_id AND b.workspace_id=s.workspace_id
  WHERE s.id=p_id AND s.workspace_id=ws AND o.deleted_at IS NULL AND c.deleted_at IS NULL AND c.data_kind='production';
  IF result IS NULL THEN RETURN jsonb_build_object('id',p_id,'excluded',true); END IF;
  RETURN result;
 END IF;
 result:=ops.feishu_source_v120(p_connection,p_kind,p_id);
 IF result->>'excluded'='true' OR result->>'deleted'='true' THEN RETURN result; END IF;
 IF p_kind='opportunity' THEN
  SELECT jsonb_build_object('original_owner_name',original_owner_name,'ownership_resolution',ownership_resolution,
   'ownership_resolution_evidence',ownership_resolution_evidence,'expected_close_year',expected_close_year,
   'expected_close_quarter',expected_close_quarter,
   'associated_partner_ids',(SELECT COALESCE(jsonb_agg(r.partner_id ORDER BY r.partner_id),'[]'::jsonb)
    FROM crm.opportunity_related_partner r WHERE r.workspace_id=ws AND r.opportunity_id=p_id),
   'associated_partner_names',(SELECT COALESCE(jsonb_agg(p.name ORDER BY r.partner_id),'[]'::jsonb)
    FROM crm.opportunity_related_partner r JOIN crm.partner p ON p.id=r.partner_id AND p.workspace_id=r.workspace_id
    WHERE r.workspace_id=ws AND r.opportunity_id=p_id)) INTO extra FROM crm.opportunity WHERE id=p_id AND workspace_id=ws;
  -- Additional raw provenance only. Never replace the existing system creation
  -- timestamp, parse dates, or use an import timestamp as missing historical data.
  IF result->>'deleted_at' IS NULL THEN
   SELECT import_meta INTO opportunity_meta FROM crm.opportunity WHERE id=p_id AND workspace_id=ws;
   IF opportunity_meta->>'import_type'='crm_history' THEN
    original_value:=opportunity_meta->'source_fields'->'商机创建时间';
    original_source:='history_source_fields';
    IF original_value IS NULL OR original_value='null'::jsonb OR
      (jsonb_typeof(original_value)='string' AND (original_value #>> '{}') !~ '[^[:space:]]') THEN
     original_value:=opportunity_meta->'raw_fields'->'商机创建时间';
     original_source:='history_legacy_raw_fields';
    END IF;
    IF original_value IS NULL OR original_value='null'::jsonb OR
      (jsonb_typeof(original_value)='string' AND (original_value #>> '{}') !~ '[^[:space:]]') THEN
     original_value:=NULL; original_source:='historical_unknown';
    END IF;
   ELSE
    original_value:=result->'created_at'; original_source:='system_entry';
   END IF;
   -- A text target preserves strings byte-for-byte; unexpected structured source
   -- values remain JSON text instead of being silently coerced into a date.
   extra:=extra||jsonb_build_object('original_created_at_raw',
    CASE WHEN original_value IS NULL THEN NULL
      WHEN jsonb_typeof(original_value)='string' THEN original_value #>> '{}'
      ELSE original_value::text END,'original_created_at_source',original_source);
  END IF;
 ELSIF p_kind='forecast' THEN
  SELECT jsonb_build_object('collection_confidence',collection_confidence) INTO extra
   FROM crm.opportunity_forecast WHERE id=p_id AND workspace_id=ws;
 ELSIF p_kind='partner' THEN
  SELECT jsonb_build_object('short_name',p.short_name,'principal_name',p.principal_name,
   'channel_manager_user_ref_id',p.channel_manager_user_ref_id,'channel_manager_name',u.display_name,
   'original_channel_manager_name',p.original_channel_manager_name,'priority',p.priority,'progress',p.progress,
   'grade',p.grade,'signed_on',p.signed_on,'partner_type',p.partner_type,'region',p.region,'province',p.province,'note',p.note,
   'last_interaction_at',(SELECT max(v.interaction_at) FROM activity.visit v WHERE v.workspace_id=ws
    AND v.partner_id=p.id AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')))
   INTO extra FROM crm.partner p LEFT JOIN platform.user_ref u ON u.id=p.channel_manager_user_ref_id AND u.workspace_id=p.workspace_id
   WHERE p.id=p_id AND p.workspace_id=ws;
 ELSIF p_kind='visit' THEN
  -- A multi-link narrative is not exported if any linked parent is excluded.
  IF EXISTS(SELECT 1 FROM activity.visit_opportunity x LEFT JOIN crm.opportunity o
    ON o.id=x.opportunity_id AND o.workspace_id=x.workspace_id
    LEFT JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
    WHERE x.visit_id=p_id AND x.workspace_id=ws AND
     (o.id IS NULL OR o.deleted_at IS NOT NULL OR c.id IS NULL OR c.deleted_at IS NOT NULL OR c.data_kind<>'production')) THEN
   RETURN jsonb_build_object('id',p_id,'excluded',true);
  END IF;
  SELECT jsonb_build_object('partner_id',v.partner_id,'partner_name',p.name,
   'original_recorder_name',v.original_recorder_name,'manager_user_ref_id',v.manager_user_ref_id,
   'manager_name',u.display_name,'manager_account',u.account_code,
   'opportunity_ids',(SELECT COALESCE(jsonb_agg(x.opportunity_id ORDER BY x.opportunity_id),'[]'::jsonb)
    FROM activity.visit_opportunity x WHERE x.visit_id=v.id AND x.workspace_id=ws),
   'opportunity_names',(SELECT COALESCE(jsonb_agg(o.name ORDER BY o.id),'[]'::jsonb)
    FROM activity.visit_opportunity x JOIN crm.opportunity o ON o.id=x.opportunity_id AND o.workspace_id=x.workspace_id
    WHERE x.visit_id=v.id AND x.workspace_id=ws),
   'customer_ids',(SELECT COALESCE(jsonb_agg(cid ORDER BY cid),'[]'::jsonb) FROM
    (SELECT v.customer_id AS cid WHERE v.customer_id IS NOT NULL UNION
     SELECT o.customer_id FROM activity.visit_opportunity x JOIN crm.opportunity o
     ON o.id=x.opportunity_id AND o.workspace_id=x.workspace_id WHERE x.visit_id=v.id AND x.workspace_id=ws) customers),
   'customer_names',(SELECT COALESCE(jsonb_agg(c.name ORDER BY c.id),'[]'::jsonb) FROM crm.customer c
    WHERE c.workspace_id=ws AND c.id IN (
     SELECT v.customer_id WHERE v.customer_id IS NOT NULL UNION
     SELECT o.customer_id FROM activity.visit_opportunity x JOIN crm.opportunity o
      ON o.id=x.opportunity_id AND o.workspace_id=x.workspace_id WHERE x.visit_id=v.id AND x.workspace_id=ws)))
   INTO extra FROM activity.visit v LEFT JOIN crm.partner p ON p.id=v.partner_id AND p.workspace_id=v.workspace_id
   LEFT JOIN platform.user_ref u ON u.id=v.manager_user_ref_id AND u.workspace_id=v.workspace_id WHERE v.id=p_id AND v.workspace_id=ws;
 END IF;
 RETURN result||COALESCE(extra,'{}'::jsonb);
END $$;

CREATE OR REPLACE FUNCTION ops.feishu_reconcile(p_connection uuid) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; item text[]; total bigint:=0; n bigint;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection AND enabled AND security.workspace_is_active(workspace_id) AND security.workspace_module_enabled(workspace_id,'feishu');
 IF ws IS NULL THEN RETURN 0; END IF;
 FOREACH item SLICE 1 IN ARRAY ARRAY[['crm.customer','customer'],['crm.opportunity','opportunity'],['activity.visit','visit'],['crm.partner','partner'],['workflow.task','task'],['crm.opportunity_demo_scenes','demo_scene'],['crm.customer_actual','actual'],['crm.sales_target','target'],['platform.user_ref','member'],['crm.contact','contact'],['crm.opportunity_forecast','forecast'],['crm.opportunity_period_actual_snapshot','period_actual_snapshot']] LOOP
  IF NOT EXISTS(SELECT 1 FROM config.feishu_connection c WHERE c.id=p_connection AND (c.settings->'mappings'->item[2]->>'enabled')::boolean IS TRUE) THEN CONTINUE; END IF;
  EXECUTE format('INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,historical,queue_origin) SELECT $1,$2,$3,id,true,''reconcile'' FROM %s s WHERE workspace_id=$2 AND NOT EXISTS(SELECT 1 FROM ops.feishu_event e WHERE e.connection_id=$1 AND e.object_kind=$3 AND e.object_id=s.id AND e.status IN (''pending'',''running'',''failed''))',item[1]) USING p_connection,ws,item[2];
  GET DIAGNOSTICS n=ROW_COUNT; total:=total+n;
 END LOOP;
 RETURN total;
END $$;


ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_internal_v155;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_internal_v155();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND rolname NOT LIKE 'pg\_%' ESCAPE '\'
  AND has_schema_privilege(oid,'security','USAGE')
  AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.tenant_modules(),security.workspace_module_enabled(uuid,text),security.workspace_job_enabled(uuid,text) TO %I',r.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION security.tenant_permission_enabled(uuid,text),security.reconcile_runtime_grants_internal_v155() FROM %I',r.rolname);
 END LOOP;
 GRANT EXECUTE ON FUNCTION security.workspace_module_enabled(uuid,text) TO salegent_feishu_worker;
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'control','USAGE')
  AND has_function_privilege(oid,'control.tenants(text)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION control.module_settings(text,uuid),control.save_modules(text,uuid,integer,jsonb,text) TO %I',r.rolname);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_internal_v155() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V304','Tenant capability ceilings and expiring authorization');
COMMIT;
