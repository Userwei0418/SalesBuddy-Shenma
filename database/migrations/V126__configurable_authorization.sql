BEGIN;

-- Permission configuration is separate from organization appointments. No user,
-- team membership, business role binding or customer ownership is modified here.
CREATE TABLE config.permission_catalog (
 code text PRIMARY KEY, label text NOT NULL, module text NOT NULL,
 scopes text[] NOT NULL, sensitive boolean NOT NULL DEFAULT false
);
CREATE TABLE config.permission_role_default (
 role_code text PRIMARY KEY, name text NOT NULL, permissions jsonb NOT NULL
 CHECK(jsonb_typeof(permissions)='array')
);
CREATE TABLE config.permission_role (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 name text NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 80),
 description text NOT NULL DEFAULT '',
 builtin_role_code text CHECK(builtin_role_code IN ('sales','supervisor','manager','fde','fde_lead','operations','administrator')),
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
 version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(workspace_id,builtin_role_code), UNIQUE(id,workspace_id)
);
CREATE TABLE config.permission_role_grant (
 workspace_id uuid NOT NULL, role_id uuid NOT NULL,
 permission_code text NOT NULL REFERENCES config.permission_catalog(code),
 scope_code text NOT NULL CHECK(scope_code IN ('inherit','self','assigned','teams','workspace')),
 team_ids uuid[] NOT NULL DEFAULT '{}',
 PRIMARY KEY(role_id,permission_code),
 FOREIGN KEY(role_id,workspace_id) REFERENCES config.permission_role(id,workspace_id),
 CHECK((scope_code='teams')=(cardinality(team_ids)>0))
);
CREATE TABLE config.account_authorization (
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 user_ref_id uuid NOT NULL REFERENCES platform.user_ref(id),
 version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,user_ref_id)
);
CREATE TABLE config.account_permission_role (
 workspace_id uuid NOT NULL, user_ref_id uuid NOT NULL, role_id uuid NOT NULL,
 scope_code text NOT NULL CHECK(scope_code IN ('self','assigned','teams','workspace')),
 team_ids uuid[] NOT NULL DEFAULT '{}',
 PRIMARY KEY(workspace_id,user_ref_id,role_id),
 FOREIGN KEY(workspace_id,user_ref_id) REFERENCES config.account_authorization(workspace_id,user_ref_id),
 FOREIGN KEY(role_id,workspace_id) REFERENCES config.permission_role(id,workspace_id),
 CHECK((scope_code='teams')=(cardinality(team_ids)>0))
);
CREATE TABLE config.account_permission_override (
 workspace_id uuid NOT NULL, user_ref_id uuid NOT NULL,
 permission_code text NOT NULL REFERENCES config.permission_catalog(code),
 effect text NOT NULL CHECK(effect IN ('allow','deny')),
 scope_code text CHECK(scope_code IN ('self','assigned','teams','workspace')),
 team_ids uuid[] NOT NULL DEFAULT '{}',
 PRIMARY KEY(workspace_id,user_ref_id,permission_code),
 FOREIGN KEY(workspace_id,user_ref_id) REFERENCES config.account_authorization(workspace_id,user_ref_id),
 CHECK((effect='deny' AND scope_code IS NULL AND cardinality(team_ids)=0)
    OR (effect='allow' AND scope_code IS NOT NULL AND ((scope_code='teams')=(cardinality(team_ids)>0))))
);
CREATE TABLE config.authorization_audit (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 actor_user_ref_id uuid NOT NULL REFERENCES platform.user_ref(id),
 subject_type text NOT NULL CHECK(subject_type IN ('role','account')),
 subject_id uuid NOT NULL,
 before_snapshot jsonb NOT NULL, after_snapshot jsonb NOT NULL,
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 500),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX authorization_audit_recent ON config.authorization_audit(workspace_id,created_at DESC,id DESC);

-- The internal evaluator has no public/app EXECUTE grant. Public wrappers derive
-- the caller from authenticated transaction context and validate preview rights.
-- Keep previously configured FDE visit switches until this template is explicitly
-- edited. A sales appointment or explicit account grant still contributes by union.
CREATE FUNCTION security.authorization_legacy_fde_visit(p_workspace uuid,p_user uuid,p_role text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE((SELECT COALESCE(
  CASE WHEN jsonb_typeof(definition->'user_overrides'->p_user::text)='boolean' THEN (definition->'user_overrides'->>p_user::text)::boolean END,
  CASE WHEN jsonb_typeof(definition->'role_overrides'->p_role)='boolean' THEN (definition->'role_overrides'->>p_role)::boolean END,
  CASE WHEN jsonb_typeof(definition->'visit_entry_enabled')='boolean' THEN (definition->>'visit_entry_enabled')::boolean END,false)
 FROM config.rule_set WHERE rule_code='fde_capabilities' AND status='active'
 AND effective_from<=clock_timestamp() AND effective_to>clock_timestamp()
 AND (workspace_id=p_workspace OR workspace_id IS NULL)
 ORDER BY (workspace_id IS NOT NULL) DESC,version_no DESC LIMIT 1),false);
$$;
REVOKE ALL ON FUNCTION security.authorization_legacy_fde_visit(uuid,uuid,text) FROM PUBLIC;

CREATE FUNCTION security.authorization_grants_for(p_workspace uuid,p_user uuid)
RETURNS TABLE(permission_code text,scope_code text,team_ids uuid[],source_code text,source_id text,effect text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
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
   AND r.builtin_role_code=a.role_code AND r.status='active'
  UNION ALL
  SELECT r.id,r.version_no,'assigned_role',a.scope_code,a.team_ids
  FROM config.account_permission_role a JOIN person p ON p.id=a.user_ref_id
  JOIN config.permission_role r ON r.id=a.role_id AND r.workspace_id=a.workspace_id AND r.status='active'
  WHERE a.workspace_id=p_workspace
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
  FROM config.account_permission_override o JOIN person p ON p.id=o.user_ref_id WHERE o.workspace_id=p_workspace
 ), valid AS (
  SELECT c.*,ARRAY(SELECT t.id FROM platform.team t WHERE t.id=ANY(c.teams) AND t.workspace_id=p_workspace
    AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to ORDER BY t.id) active_teams
  FROM combined c JOIN config.permission_catalog d ON d.code=c.permission_code
  WHERE c.effect='deny' OR c.scope_code=ANY(d.scopes)
 )
 SELECT v.permission_code,v.scope_code,v.active_teams,v.source_code,v.source_id,v.effect FROM valid v
 WHERE v.scope_code<>'teams' OR cardinality(v.active_teams)>0 OR v.effect='deny';
$$;
REVOKE ALL ON FUNCTION security.authorization_grants_for(uuid,uuid) FROM PUBLIC;

-- The authenticated transaction establishes this context together with the actor
-- GUCs. It is never taken from tokens or request bodies and ends at COMMIT/ROLLBACK.
CREATE FUNCTION security.authorization_refresh() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE grants jsonb;
BEGIN
 SELECT COALESCE(jsonb_agg(to_jsonb(g) ORDER BY g.permission_code,g.effect,g.source_id,g.scope_code,g.team_ids),'[]') INTO grants
 FROM security.authorization_grants_for(common.current_workspace_id(),common.current_user_ref_id()) g;
 PERFORM set_config('app.authorization_context',jsonb_build_object('workspace_id',common.current_workspace_id(),
  'user_id',common.current_user_ref_id(),'grants',grants)::text,true);
END $$;
REVOKE ALL ON FUNCTION security.authorization_refresh() FROM PUBLIC;

CREATE FUNCTION security.authorization_current_grants()
RETURNS TABLE(permission_code text,scope_code text,team_ids uuid[],source_code text,source_id text,effect text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE context jsonb:=NULLIF(current_setting('app.authorization_context',true),'')::jsonb;
BEGIN
 IF context->>'workspace_id'=common.current_workspace_id()::text AND context->>'user_id'=common.current_user_ref_id()::text THEN
  RETURN QUERY SELECT g.* FROM jsonb_to_recordset(context->'grants')
   AS g(permission_code text,scope_code text,team_ids uuid[],source_code text,source_id text,effect text);
 ELSE
  RETURN QUERY SELECT * FROM security.authorization_grants_for(common.current_workspace_id(),common.current_user_ref_id());
 END IF;
END $$;
REVOKE ALL ON FUNCTION security.authorization_current_grants() FROM PUBLIC;

CREATE FUNCTION security.authorization_has(p_permission text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH grants AS (SELECT * FROM security.authorization_current_grants()
   WHERE permission_code=p_permission)
 SELECT EXISTS(SELECT 1 FROM grants WHERE effect='allow') AND NOT EXISTS(SELECT 1 FROM grants WHERE effect='deny');
$$;

CREATE FUNCTION security.authorization_snapshot(p_user uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE target uuid:=COALESCE(p_user,common.current_user_ref_id()); result jsonb;
BEGIN
 IF target<>common.current_user_ref_id() AND NOT security.authorization_has('authorization.read') THEN
  RAISE EXCEPTION '无权查看此账号权限' USING ERRCODE='42501';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.user_ref WHERE id=target AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL) THEN
  RAISE EXCEPTION '账号不存在或不属于当前公司' USING ERRCODE='42501';
 END IF;
 SELECT COALESCE(jsonb_agg(to_jsonb(g) ORDER BY g.permission_code,g.effect,g.source_id,g.scope_code,g.team_ids),'[]'::jsonb) INTO result
 FROM (SELECT * FROM security.authorization_current_grants() WHERE target=common.current_user_ref_id()
 UNION ALL SELECT * FROM security.authorization_grants_for(common.current_workspace_id(),target) WHERE target<>common.current_user_ref_id()) g;
 RETURN jsonb_build_object('workspace_id',common.current_workspace_id(),'user_id',target,'grants',result,
   'permission_version','rbac-v1:'||md5(result::text));
END;
$$;

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['permission_role','permission_role_grant','account_authorization','account_permission_role','account_permission_override'] LOOP
  EXECUTE format('ALTER TABLE config.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE config.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY authorization_read ON config.%I FOR SELECT USING (workspace_id=common.current_workspace_id() AND security.authorization_has(''authorization.read''))',t);
 END LOOP;
END $$;
ALTER TABLE config.authorization_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE config.authorization_audit FORCE ROW LEVEL SECURITY;
CREATE POLICY authorization_audit_read ON config.authorization_audit FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.authorization_has('authorization.audit'));
-- Mutations enter only through audited, compare-and-swap SECURITY DEFINER functions.

-- Catalog/default rows and workspace bootstrap are appended below from the
-- versioned source catalog; the migration owns a fixed snapshot, never runtime code.

INSERT INTO config.permission_catalog SELECT * FROM jsonb_to_recordset($catalog$[{"code":"access.mini_program","label":"使用小程序","module":"使用入口","scopes":["workspace"],"sensitive":false},{"code":"access.console","label":"使用运营后台","module":"使用入口","scopes":["workspace"],"sensitive":false},{"code":"overview.read","label":"查看总览","module":"总览与经营","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"customer.reference","label":"搜索与选择客户基本信息","module":"客户管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"customer.read","label":"查看客户详情","module":"客户管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"customer.create","label":"新建客户","module":"客户管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"customer.update","label":"修改客户资料","module":"客户管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"customer.claim","label":"申请认领客户","module":"客户管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"customer.claim_directory","label":"查看公司客户认领目录","module":"客户管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"customer.claim_review","label":"审批客户认领","module":"客户管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"customer.release","label":"释放客户","module":"客户管理","scopes":["self","assigned","teams","workspace"],"sensitive":true},{"code":"customer.resolve_owner","label":"核对历史客户归属","module":"客户管理","scopes":["self","assigned","teams","workspace"],"sensitive":true},{"code":"customer.export","label":"导出客户","module":"客户管理","scopes":["self","assigned","teams","workspace"],"sensitive":true},{"code":"battle_map.read","label":"查看客户作战地图","module":"作战地图","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"opportunity.read","label":"查看商机","module":"商机管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"opportunity.create","label":"创建商机","module":"商机管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"opportunity.update","label":"修改商机","module":"商机管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"opportunity.close","label":"确认赢单或丢单","module":"商机管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"opportunity.reopen","label":"重新打开商机","module":"商机管理","scopes":["self","assigned","teams","workspace"],"sensitive":true},{"code":"opportunity.create_for_others","label":"代他人创建商机","module":"商机管理","scopes":["self","assigned","teams","workspace"],"sensitive":true},{"code":"opportunity.quote_create","label":"记录报价","module":"商机管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"opportunity.fde_members","label":"维护商机协作 FDE","module":"商机管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"opportunity.export","label":"导出商机","module":"商机管理","scopes":["self","assigned","teams","workspace"],"sensitive":true},{"code":"visit.read","label":"查看跟进记录","module":"跟进记录","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"visit.create","label":"新增跟进记录","module":"跟进记录","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"visit.supplement","label":"补充跟进记录","module":"跟进记录","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"visit.upload","label":"上传跟进材料","module":"跟进记录","scopes":["self"],"sensitive":false},{"code":"visit.transcribe","label":"语音转写","module":"跟进记录","scopes":["self"],"sensitive":false},{"code":"visit.retry_import","label":"重试材料识别","module":"跟进记录","scopes":["self"],"sensitive":false},{"code":"visit.download_original","label":"下载原始材料","module":"跟进记录","scopes":["self","assigned","teams","workspace"],"sensitive":true},{"code":"visit.structure","label":"AI 整理跟进原文","module":"跟进记录","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"visit.quality_review","label":"AI 质检跟进记录","module":"跟进记录","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"visit.first_visit","label":"登记首次拜访信息","module":"跟进记录","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"visit.attendance_manage","label":"登记协同人与参与 FDE","module":"跟进记录","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"task.read","label":"查看待办","module":"待办事项","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"task.create_daily","label":"创建日常待办","module":"待办事项","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"task.create_customer","label":"创建客户待办","module":"待办事项","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"task.assign","label":"向他人下发待办","module":"待办事项","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"task.accept","label":"接收待办","module":"待办事项","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"task.decline","label":"拒绝待办","module":"待办事项","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"task.complete","label":"完成待办","module":"待办事项","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"task.cancel","label":"取消待办","module":"待办事项","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"task.coordinate","label":"协调岗位待办","module":"待办事项","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"task.review","label":"处理待办结果","module":"待办事项","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"advice.request","label":"生成经营建议","module":"经营建议与风险","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"advice.read","label":"查看经营建议","module":"经营建议与风险","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"advice.decide","label":"采纳或忽略建议","module":"经营建议与风险","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"advice.customer","label":"分析客户整体经营","module":"经营建议与风险","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"advice.opportunity","label":"分析商机经营","module":"经营建议与风险","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"advice.visit","label":"分析单次跟进","module":"经营建议与风险","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"risk.read","label":"查看风险","module":"经营建议与风险","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"risk.resolve","label":"处理风险","module":"经营建议与风险","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"risk.auto_review","label":"自动评估客户风险","module":"经营建议与风险","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"actual.read","label":"查看客户经营实绩","module":"客户经营实绩","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"actual.create","label":"登记确收与回款","module":"客户经营实绩","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"actual.void","label":"作废实绩登记","module":"客户经营实绩","scopes":["self","assigned","teams","workspace"],"sensitive":true},{"code":"dashboard.read","label":"查看经营看板","module":"经营看板","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"dashboard.ranking","label":"查看销售排名","module":"经营看板","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"profile.sales_read","label":"查看销售画像","module":"个人与团队画像","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"profile.sales_review","label":"生成销售成长评估","module":"个人与团队画像","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"profile.fde_read","label":"查看 FDE 画像","module":"个人与团队画像","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"profile.fde_review","label":"生成 FDE 成长评估","module":"个人与团队画像","scopes":["self"],"sensitive":false},{"code":"profile.fde_activity","label":"查看 FDE 协作记录","module":"个人与团队画像","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"target.read","label":"查看目标","module":"目标管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"target.submit","label":"设置目标或提交变更","module":"目标管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"target.manage","label":"运营维护目标","module":"目标管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"target.approve","label":"审批目标变更","module":"目标管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"target.history","label":"查看目标变更历史","module":"目标管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"target.fde_department","label":"访问 FDE 部门汇总目标","module":"目标管理","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"agent.chatbi","label":"公司数据问答","module":"智能助手","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"agent.customer_chatbi","label":"客户数据问答","module":"智能助手","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"agent.today_tasks","label":"生成今日待办建议","module":"智能助手","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"agent.personal_risks","label":"分析个人经营风险","module":"智能助手","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"agent.operating_report","label":"生成经营报告","module":"智能助手","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"agent.opportunity_draft","label":"起草商机建议","module":"智能助手","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"agent.management_task","label":"起草管理任务","module":"智能助手","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"agent.history","label":"查看本人智能助手会话","module":"智能助手","scopes":["self"],"sensitive":false},{"code":"partner.read","label":"查看伙伴目录","module":"伙伴与演示方案","scopes":["workspace"],"sensitive":false},{"code":"partner.manage","label":"维护伙伴目录","module":"伙伴与演示方案","scopes":["workspace"],"sensitive":false},{"code":"demo_scene.read","label":"查看商机演示方案","module":"伙伴与演示方案","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"demo_scene.create","label":"新增演示方案","module":"伙伴与演示方案","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"demo_scene.update","label":"修改演示方案","module":"伙伴与演示方案","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"demo_scene.delete","label":"删除演示方案","module":"伙伴与演示方案","scopes":["self","assigned","teams","workspace"],"sensitive":true},{"code":"organization.read","label":"查看组织与账号","module":"组织与账号","scopes":["workspace"],"sensitive":false},{"code":"organization.manage","label":"维护部门与任职","module":"组织与账号","scopes":["workspace"],"sensitive":false},{"code":"account.create","label":"开通账号","module":"组织与账号","scopes":["workspace"],"sensitive":true},{"code":"account.update","label":"修改或停用账号","module":"组织与账号","scopes":["workspace"],"sensitive":true},{"code":"account.reset_password","label":"设置或重置账号密码","module":"组织与账号","scopes":["workspace"],"sensitive":true},{"code":"account.unlock","label":"解除登录限制","module":"组织与账号","scopes":["workspace"],"sensitive":true},{"code":"account.password_policy","label":"维护公司密码策略","module":"组织与账号","scopes":["workspace"],"sensitive":true},{"code":"authorization.read","label":"查看角色与有效权限","module":"权限管理","scopes":["workspace"],"sensitive":false},{"code":"authorization.roles_manage","label":"维护权限角色模板","module":"权限管理","scopes":["workspace"],"sensitive":true},{"code":"authorization.accounts_manage","label":"配置账号角色与额外权限","module":"权限管理","scopes":["workspace"],"sensitive":true},{"code":"authorization.audit","label":"查看授权变更记录","module":"权限管理","scopes":["workspace"],"sensitive":false},{"code":"history.import","label":"执行已审核历史数据导入","module":"历史数据导入","scopes":["workspace"],"sensitive":true},{"code":"company.read","label":"查看本公司资料","module":"公司管理","scopes":["workspace"],"sensitive":false},{"code":"company.update","label":"修改本公司资料","module":"公司管理","scopes":["workspace"],"sensitive":false},{"code":"rule.read","label":"查看业务规则","module":"业务规则","scopes":["workspace"],"sensitive":false},{"code":"rule.draft","label":"编辑业务规则草稿","module":"业务规则","scopes":["workspace"],"sensitive":false},{"code":"rule.preview","label":"预览规则影响","module":"业务规则","scopes":["workspace"],"sensitive":false},{"code":"rule.publish","label":"发布业务规则","module":"业务规则","scopes":["workspace"],"sensitive":true},{"code":"rule.restore","label":"恢复历史规则","module":"业务规则","scopes":["workspace"],"sensitive":true},{"code":"ai.usage_rules_manage","label":"维护 AI 用量提醒规则","module":"AI 配置与运行","scopes":["workspace"],"sensitive":false},{"code":"ai.usage_read","label":"查看 AI 调用统计","module":"AI 配置与运行","scopes":["workspace"],"sensitive":false},{"code":"ai.usage_export","label":"导出 AI 调用统计","module":"AI 配置与运行","scopes":["workspace"],"sensitive":true},{"code":"ai.run_read","label":"查看 Agent 运行审计","module":"AI 配置与运行","scopes":["workspace"],"sensitive":false},{"code":"ai.run_export","label":"导出 Agent 运行审计","module":"AI 配置与运行","scopes":["workspace"],"sensitive":true},{"code":"ai.execution_read","label":"查看 Agent 运行规则","module":"AI 配置与运行","scopes":["workspace"],"sensitive":false},{"code":"ai.config_read","label":"查看模型与 Agent 配置","module":"AI 配置与运行","scopes":["workspace"],"sensitive":false},{"code":"ai.config_test","label":"测试模型与连接","module":"AI 配置与运行","scopes":["workspace"],"sensitive":false},{"code":"ai.config_publish","label":"发布模型与 Agent 配置","module":"AI 配置与运行","scopes":["workspace"],"sensitive":true},{"code":"ai.config_rollback","label":"回退 Agent 配置","module":"AI 配置与运行","scopes":["workspace"],"sensitive":true},{"code":"feishu.read","label":"查看飞书同步配置和状态","module":"飞书同步","scopes":["workspace"],"sensitive":false},{"code":"feishu.configure","label":"修改飞书同步配置","module":"飞书同步","scopes":["workspace"],"sensitive":true},{"code":"feishu.control","label":"启动或暂停飞书同步","module":"飞书同步","scopes":["workspace"],"sensitive":true},{"code":"feishu.recover","label":"重试飞书同步","module":"飞书同步","scopes":["workspace"],"sensitive":true},{"code":"audit.read","label":"查看操作审计","module":"审计与系统","scopes":["workspace"],"sensitive":false},{"code":"audit.export","label":"导出操作审计","module":"审计与系统","scopes":["workspace"],"sensitive":true},{"code":"audit.events_read","label":"查看系统事件","module":"审计与系统","scopes":["workspace"],"sensitive":false},{"code":"audit.events_export","label":"导出系统事件","module":"审计与系统","scopes":["workspace"],"sensitive":true},{"code":"audit.business_read","label":"查看业务活动","module":"审计与系统","scopes":["workspace"],"sensitive":false},{"code":"audit.business_export","label":"导出业务活动","module":"审计与系统","scopes":["workspace"],"sensitive":true},{"code":"directory.read","label":"查看授权团队与成员目录","module":"团队与通知","scopes":["self","assigned","teams","workspace"],"sensitive":false},{"code":"notification.read","label":"查看本人通知","module":"团队与通知","scopes":["self"],"sensitive":false},{"code":"notification.mark_read","label":"将本人通知标为已读","module":"团队与通知","scopes":["self"],"sensitive":false}]$catalog$::jsonb) AS x(code text,label text,module text,scopes text[],sensitive boolean);
INSERT INTO config.permission_role_default SELECT * FROM jsonb_to_recordset($defaults$[{"role_code":"sales","name":"销售","permissions":[{"permission":"access.mini_program","scope":"workspace","team_ids":[]},{"permission":"actual.read","scope":"inherit","team_ids":[]},{"permission":"advice.customer","scope":"inherit","team_ids":[]},{"permission":"advice.decide","scope":"inherit","team_ids":[]},{"permission":"advice.opportunity","scope":"inherit","team_ids":[]},{"permission":"advice.read","scope":"inherit","team_ids":[]},{"permission":"advice.request","scope":"inherit","team_ids":[]},{"permission":"advice.visit","scope":"inherit","team_ids":[]},{"permission":"agent.chatbi","scope":"inherit","team_ids":[]},{"permission":"agent.customer_chatbi","scope":"inherit","team_ids":[]},{"permission":"agent.history","scope":"self","team_ids":[]},{"permission":"agent.operating_report","scope":"inherit","team_ids":[]},{"permission":"agent.opportunity_draft","scope":"inherit","team_ids":[]},{"permission":"agent.personal_risks","scope":"inherit","team_ids":[]},{"permission":"agent.today_tasks","scope":"inherit","team_ids":[]},{"permission":"battle_map.read","scope":"inherit","team_ids":[]},{"permission":"customer.claim","scope":"self","team_ids":[]},{"permission":"customer.claim_directory","scope":"workspace","team_ids":[]},{"permission":"customer.read","scope":"inherit","team_ids":[]},{"permission":"customer.reference","scope":"workspace","team_ids":[]},{"permission":"customer.update","scope":"inherit","team_ids":[]},{"permission":"dashboard.ranking","scope":"workspace","team_ids":[]},{"permission":"dashboard.read","scope":"inherit","team_ids":[]},{"permission":"demo_scene.create","scope":"inherit","team_ids":[]},{"permission":"demo_scene.delete","scope":"inherit","team_ids":[]},{"permission":"demo_scene.read","scope":"inherit","team_ids":[]},{"permission":"demo_scene.update","scope":"inherit","team_ids":[]},{"permission":"directory.read","scope":"inherit","team_ids":[]},{"permission":"notification.mark_read","scope":"self","team_ids":[]},{"permission":"notification.read","scope":"self","team_ids":[]},{"permission":"opportunity.close","scope":"inherit","team_ids":[]},{"permission":"opportunity.create","scope":"inherit","team_ids":[]},{"permission":"opportunity.fde_members","scope":"inherit","team_ids":[]},{"permission":"opportunity.read","scope":"inherit","team_ids":[]},{"permission":"opportunity.reopen","scope":"inherit","team_ids":[]},{"permission":"opportunity.update","scope":"inherit","team_ids":[]},{"permission":"overview.read","scope":"inherit","team_ids":[]},{"permission":"partner.read","scope":"workspace","team_ids":[]},{"permission":"profile.sales_read","scope":"inherit","team_ids":[]},{"permission":"profile.sales_review","scope":"inherit","team_ids":[]},{"permission":"risk.auto_review","scope":"inherit","team_ids":[]},{"permission":"risk.read","scope":"inherit","team_ids":[]},{"permission":"risk.resolve","scope":"inherit","team_ids":[]},{"permission":"target.history","scope":"inherit","team_ids":[]},{"permission":"target.read","scope":"inherit","team_ids":[]},{"permission":"target.submit","scope":"self","team_ids":[]},{"permission":"task.accept","scope":"inherit","team_ids":[]},{"permission":"task.assign","scope":"inherit","team_ids":[]},{"permission":"task.cancel","scope":"inherit","team_ids":[]},{"permission":"task.complete","scope":"inherit","team_ids":[]},{"permission":"task.coordinate","scope":"inherit","team_ids":[]},{"permission":"task.create_customer","scope":"inherit","team_ids":[]},{"permission":"task.create_daily","scope":"inherit","team_ids":[]},{"permission":"task.decline","scope":"inherit","team_ids":[]},{"permission":"task.read","scope":"inherit","team_ids":[]},{"permission":"task.review","scope":"inherit","team_ids":[]},{"permission":"visit.attendance_manage","scope":"inherit","team_ids":[]},{"permission":"visit.create","scope":"inherit","team_ids":[]},{"permission":"visit.download_original","scope":"inherit","team_ids":[]},{"permission":"visit.first_visit","scope":"inherit","team_ids":[]},{"permission":"visit.quality_review","scope":"inherit","team_ids":[]},{"permission":"visit.read","scope":"inherit","team_ids":[]},{"permission":"visit.retry_import","scope":"self","team_ids":[]},{"permission":"visit.structure","scope":"inherit","team_ids":[]},{"permission":"visit.supplement","scope":"inherit","team_ids":[]},{"permission":"visit.transcribe","scope":"self","team_ids":[]},{"permission":"visit.upload","scope":"self","team_ids":[]}]},{"role_code":"supervisor","name":"销售主管","permissions":[{"permission":"access.mini_program","scope":"workspace","team_ids":[]},{"permission":"actual.create","scope":"inherit","team_ids":[]},{"permission":"actual.read","scope":"inherit","team_ids":[]},{"permission":"actual.void","scope":"inherit","team_ids":[]},{"permission":"advice.customer","scope":"inherit","team_ids":[]},{"permission":"advice.decide","scope":"inherit","team_ids":[]},{"permission":"advice.opportunity","scope":"inherit","team_ids":[]},{"permission":"advice.read","scope":"inherit","team_ids":[]},{"permission":"advice.request","scope":"inherit","team_ids":[]},{"permission":"advice.visit","scope":"inherit","team_ids":[]},{"permission":"agent.chatbi","scope":"inherit","team_ids":[]},{"permission":"agent.customer_chatbi","scope":"inherit","team_ids":[]},{"permission":"agent.history","scope":"self","team_ids":[]},{"permission":"agent.management_task","scope":"inherit","team_ids":[]},{"permission":"agent.operating_report","scope":"inherit","team_ids":[]},{"permission":"agent.opportunity_draft","scope":"inherit","team_ids":[]},{"permission":"battle_map.read","scope":"inherit","team_ids":[]},{"permission":"customer.claim","scope":"self","team_ids":[]},{"permission":"customer.claim_directory","scope":"workspace","team_ids":[]},{"permission":"customer.read","scope":"inherit","team_ids":[]},{"permission":"customer.reference","scope":"workspace","team_ids":[]},{"permission":"customer.update","scope":"inherit","team_ids":[]},{"permission":"dashboard.ranking","scope":"workspace","team_ids":[]},{"permission":"dashboard.read","scope":"inherit","team_ids":[]},{"permission":"demo_scene.create","scope":"inherit","team_ids":[]},{"permission":"demo_scene.delete","scope":"inherit","team_ids":[]},{"permission":"demo_scene.read","scope":"inherit","team_ids":[]},{"permission":"demo_scene.update","scope":"inherit","team_ids":[]},{"permission":"directory.read","scope":"inherit","team_ids":[]},{"permission":"notification.mark_read","scope":"self","team_ids":[]},{"permission":"notification.read","scope":"self","team_ids":[]},{"permission":"opportunity.close","scope":"inherit","team_ids":[]},{"permission":"opportunity.create","scope":"inherit","team_ids":[]},{"permission":"opportunity.fde_members","scope":"inherit","team_ids":[]},{"permission":"opportunity.read","scope":"inherit","team_ids":[]},{"permission":"opportunity.reopen","scope":"inherit","team_ids":[]},{"permission":"opportunity.update","scope":"inherit","team_ids":[]},{"permission":"overview.read","scope":"inherit","team_ids":[]},{"permission":"partner.read","scope":"workspace","team_ids":[]},{"permission":"profile.sales_read","scope":"inherit","team_ids":[]},{"permission":"profile.sales_review","scope":"inherit","team_ids":[]},{"permission":"risk.auto_review","scope":"inherit","team_ids":[]},{"permission":"risk.read","scope":"inherit","team_ids":[]},{"permission":"risk.resolve","scope":"inherit","team_ids":[]},{"permission":"target.history","scope":"inherit","team_ids":[]},{"permission":"target.read","scope":"inherit","team_ids":[]},{"permission":"target.submit","scope":"self","team_ids":[]},{"permission":"task.accept","scope":"inherit","team_ids":[]},{"permission":"task.assign","scope":"inherit","team_ids":[]},{"permission":"task.cancel","scope":"inherit","team_ids":[]},{"permission":"task.complete","scope":"inherit","team_ids":[]},{"permission":"task.coordinate","scope":"inherit","team_ids":[]},{"permission":"task.create_customer","scope":"inherit","team_ids":[]},{"permission":"task.create_daily","scope":"inherit","team_ids":[]},{"permission":"task.decline","scope":"inherit","team_ids":[]},{"permission":"task.read","scope":"inherit","team_ids":[]},{"permission":"task.review","scope":"inherit","team_ids":[]},{"permission":"visit.attendance_manage","scope":"inherit","team_ids":[]},{"permission":"visit.create","scope":"inherit","team_ids":[]},{"permission":"visit.download_original","scope":"inherit","team_ids":[]},{"permission":"visit.first_visit","scope":"inherit","team_ids":[]},{"permission":"visit.quality_review","scope":"inherit","team_ids":[]},{"permission":"visit.read","scope":"inherit","team_ids":[]},{"permission":"visit.retry_import","scope":"self","team_ids":[]},{"permission":"visit.structure","scope":"inherit","team_ids":[]},{"permission":"visit.supplement","scope":"inherit","team_ids":[]},{"permission":"visit.transcribe","scope":"self","team_ids":[]},{"permission":"visit.upload","scope":"self","team_ids":[]}]},{"role_code":"manager","name":"销售总经理","permissions":[{"permission":"access.mini_program","scope":"workspace","team_ids":[]},{"permission":"actual.create","scope":"inherit","team_ids":[]},{"permission":"actual.read","scope":"inherit","team_ids":[]},{"permission":"actual.void","scope":"inherit","team_ids":[]},{"permission":"advice.customer","scope":"inherit","team_ids":[]},{"permission":"advice.decide","scope":"inherit","team_ids":[]},{"permission":"advice.opportunity","scope":"inherit","team_ids":[]},{"permission":"advice.read","scope":"inherit","team_ids":[]},{"permission":"advice.request","scope":"inherit","team_ids":[]},{"permission":"advice.visit","scope":"inherit","team_ids":[]},{"permission":"agent.chatbi","scope":"inherit","team_ids":[]},{"permission":"agent.customer_chatbi","scope":"inherit","team_ids":[]},{"permission":"agent.history","scope":"self","team_ids":[]},{"permission":"agent.management_task","scope":"inherit","team_ids":[]},{"permission":"agent.operating_report","scope":"inherit","team_ids":[]},{"permission":"agent.opportunity_draft","scope":"inherit","team_ids":[]},{"permission":"ai.usage_read","scope":"workspace","team_ids":[]},{"permission":"battle_map.read","scope":"inherit","team_ids":[]},{"permission":"customer.claim","scope":"self","team_ids":[]},{"permission":"customer.claim_directory","scope":"workspace","team_ids":[]},{"permission":"customer.read","scope":"inherit","team_ids":[]},{"permission":"customer.reference","scope":"workspace","team_ids":[]},{"permission":"customer.update","scope":"inherit","team_ids":[]},{"permission":"dashboard.ranking","scope":"workspace","team_ids":[]},{"permission":"dashboard.read","scope":"inherit","team_ids":[]},{"permission":"demo_scene.create","scope":"inherit","team_ids":[]},{"permission":"demo_scene.delete","scope":"inherit","team_ids":[]},{"permission":"demo_scene.read","scope":"inherit","team_ids":[]},{"permission":"demo_scene.update","scope":"inherit","team_ids":[]},{"permission":"directory.read","scope":"inherit","team_ids":[]},{"permission":"notification.mark_read","scope":"self","team_ids":[]},{"permission":"notification.read","scope":"self","team_ids":[]},{"permission":"opportunity.close","scope":"inherit","team_ids":[]},{"permission":"opportunity.create","scope":"inherit","team_ids":[]},{"permission":"opportunity.fde_members","scope":"inherit","team_ids":[]},{"permission":"opportunity.read","scope":"inherit","team_ids":[]},{"permission":"opportunity.reopen","scope":"inherit","team_ids":[]},{"permission":"opportunity.update","scope":"inherit","team_ids":[]},{"permission":"overview.read","scope":"inherit","team_ids":[]},{"permission":"partner.read","scope":"workspace","team_ids":[]},{"permission":"profile.sales_read","scope":"inherit","team_ids":[]},{"permission":"profile.sales_review","scope":"inherit","team_ids":[]},{"permission":"risk.auto_review","scope":"inherit","team_ids":[]},{"permission":"risk.read","scope":"inherit","team_ids":[]},{"permission":"risk.resolve","scope":"inherit","team_ids":[]},{"permission":"target.history","scope":"inherit","team_ids":[]},{"permission":"target.read","scope":"inherit","team_ids":[]},{"permission":"target.submit","scope":"self","team_ids":[]},{"permission":"task.accept","scope":"inherit","team_ids":[]},{"permission":"task.assign","scope":"inherit","team_ids":[]},{"permission":"task.cancel","scope":"inherit","team_ids":[]},{"permission":"task.complete","scope":"inherit","team_ids":[]},{"permission":"task.coordinate","scope":"inherit","team_ids":[]},{"permission":"task.create_customer","scope":"inherit","team_ids":[]},{"permission":"task.create_daily","scope":"inherit","team_ids":[]},{"permission":"task.decline","scope":"inherit","team_ids":[]},{"permission":"task.read","scope":"inherit","team_ids":[]},{"permission":"task.review","scope":"inherit","team_ids":[]},{"permission":"visit.attendance_manage","scope":"inherit","team_ids":[]},{"permission":"visit.create","scope":"inherit","team_ids":[]},{"permission":"visit.download_original","scope":"inherit","team_ids":[]},{"permission":"visit.first_visit","scope":"inherit","team_ids":[]},{"permission":"visit.quality_review","scope":"inherit","team_ids":[]},{"permission":"visit.read","scope":"inherit","team_ids":[]},{"permission":"visit.retry_import","scope":"self","team_ids":[]},{"permission":"visit.structure","scope":"inherit","team_ids":[]},{"permission":"visit.supplement","scope":"inherit","team_ids":[]},{"permission":"visit.transcribe","scope":"self","team_ids":[]},{"permission":"visit.upload","scope":"self","team_ids":[]}]},{"role_code":"fde","name":"FDE","permissions":[{"permission":"access.mini_program","scope":"workspace","team_ids":[]},{"permission":"actual.read","scope":"assigned","team_ids":[]},{"permission":"advice.decide","scope":"assigned","team_ids":[]},{"permission":"advice.opportunity","scope":"assigned","team_ids":[]},{"permission":"advice.read","scope":"assigned","team_ids":[]},{"permission":"advice.request","scope":"assigned","team_ids":[]},{"permission":"advice.visit","scope":"assigned","team_ids":[]},{"permission":"agent.chatbi","scope":"assigned","team_ids":[]},{"permission":"agent.customer_chatbi","scope":"assigned","team_ids":[]},{"permission":"agent.history","scope":"self","team_ids":[]},{"permission":"agent.operating_report","scope":"assigned","team_ids":[]},{"permission":"battle_map.read","scope":"assigned","team_ids":[]},{"permission":"customer.read","scope":"assigned","team_ids":[]},{"permission":"customer.reference","scope":"workspace","team_ids":[]},{"permission":"dashboard.ranking","scope":"workspace","team_ids":[]},{"permission":"demo_scene.create","scope":"assigned","team_ids":[]},{"permission":"demo_scene.delete","scope":"assigned","team_ids":[]},{"permission":"demo_scene.read","scope":"assigned","team_ids":[]},{"permission":"demo_scene.update","scope":"assigned","team_ids":[]},{"permission":"directory.read","scope":"inherit","team_ids":[]},{"permission":"notification.mark_read","scope":"self","team_ids":[]},{"permission":"notification.read","scope":"self","team_ids":[]},{"permission":"opportunity.read","scope":"assigned","team_ids":[]},{"permission":"overview.read","scope":"assigned","team_ids":[]},{"permission":"partner.read","scope":"workspace","team_ids":[]},{"permission":"profile.fde_activity","scope":"assigned","team_ids":[]},{"permission":"profile.fde_read","scope":"assigned","team_ids":[]},{"permission":"profile.fde_review","scope":"self","team_ids":[]},{"permission":"risk.read","scope":"assigned","team_ids":[]},{"permission":"task.accept","scope":"inherit","team_ids":[]},{"permission":"task.assign","scope":"inherit","team_ids":[]},{"permission":"task.cancel","scope":"inherit","team_ids":[]},{"permission":"task.complete","scope":"inherit","team_ids":[]},{"permission":"task.coordinate","scope":"inherit","team_ids":[]},{"permission":"task.create_customer","scope":"inherit","team_ids":[]},{"permission":"task.create_daily","scope":"inherit","team_ids":[]},{"permission":"task.decline","scope":"inherit","team_ids":[]},{"permission":"task.read","scope":"assigned","team_ids":[]},{"permission":"task.review","scope":"inherit","team_ids":[]},{"permission":"visit.create","scope":"assigned","team_ids":[]},{"permission":"visit.download_original","scope":"inherit","team_ids":[]},{"permission":"visit.quality_review","scope":"assigned","team_ids":[]},{"permission":"visit.read","scope":"assigned","team_ids":[]},{"permission":"visit.retry_import","scope":"self","team_ids":[]},{"permission":"visit.structure","scope":"assigned","team_ids":[]},{"permission":"visit.supplement","scope":"assigned","team_ids":[]},{"permission":"visit.transcribe","scope":"self","team_ids":[]},{"permission":"visit.upload","scope":"self","team_ids":[]}]},{"role_code":"fde_lead","name":"FDE 主管","permissions":[{"permission":"access.mini_program","scope":"workspace","team_ids":[]},{"permission":"actual.read","scope":"inherit","team_ids":[]},{"permission":"advice.decide","scope":"inherit","team_ids":[]},{"permission":"advice.opportunity","scope":"inherit","team_ids":[]},{"permission":"advice.read","scope":"inherit","team_ids":[]},{"permission":"advice.request","scope":"inherit","team_ids":[]},{"permission":"advice.visit","scope":"inherit","team_ids":[]},{"permission":"agent.chatbi","scope":"inherit","team_ids":[]},{"permission":"agent.customer_chatbi","scope":"inherit","team_ids":[]},{"permission":"agent.history","scope":"self","team_ids":[]},{"permission":"agent.operating_report","scope":"inherit","team_ids":[]},{"permission":"battle_map.read","scope":"inherit","team_ids":[]},{"permission":"customer.read","scope":"inherit","team_ids":[]},{"permission":"customer.reference","scope":"workspace","team_ids":[]},{"permission":"dashboard.ranking","scope":"workspace","team_ids":[]},{"permission":"demo_scene.create","scope":"inherit","team_ids":[]},{"permission":"demo_scene.delete","scope":"inherit","team_ids":[]},{"permission":"demo_scene.read","scope":"inherit","team_ids":[]},{"permission":"demo_scene.update","scope":"inherit","team_ids":[]},{"permission":"directory.read","scope":"inherit","team_ids":[]},{"permission":"notification.mark_read","scope":"self","team_ids":[]},{"permission":"notification.read","scope":"self","team_ids":[]},{"permission":"opportunity.fde_members","scope":"inherit","team_ids":[]},{"permission":"opportunity.read","scope":"inherit","team_ids":[]},{"permission":"overview.read","scope":"inherit","team_ids":[]},{"permission":"partner.read","scope":"workspace","team_ids":[]},{"permission":"profile.fde_activity","scope":"inherit","team_ids":[]},{"permission":"profile.fde_read","scope":"inherit","team_ids":[]},{"permission":"profile.fde_review","scope":"self","team_ids":[]},{"permission":"risk.read","scope":"inherit","team_ids":[]},{"permission":"task.accept","scope":"inherit","team_ids":[]},{"permission":"task.assign","scope":"inherit","team_ids":[]},{"permission":"task.cancel","scope":"inherit","team_ids":[]},{"permission":"task.complete","scope":"inherit","team_ids":[]},{"permission":"task.coordinate","scope":"inherit","team_ids":[]},{"permission":"task.create_customer","scope":"inherit","team_ids":[]},{"permission":"task.create_daily","scope":"inherit","team_ids":[]},{"permission":"task.decline","scope":"inherit","team_ids":[]},{"permission":"task.read","scope":"inherit","team_ids":[]},{"permission":"task.review","scope":"inherit","team_ids":[]},{"permission":"visit.create","scope":"assigned","team_ids":[]},{"permission":"visit.download_original","scope":"inherit","team_ids":[]},{"permission":"visit.quality_review","scope":"assigned","team_ids":[]},{"permission":"visit.read","scope":"inherit","team_ids":[]},{"permission":"visit.retry_import","scope":"self","team_ids":[]},{"permission":"visit.structure","scope":"assigned","team_ids":[]},{"permission":"visit.supplement","scope":"assigned","team_ids":[]},{"permission":"visit.transcribe","scope":"self","team_ids":[]},{"permission":"visit.upload","scope":"self","team_ids":[]}]},{"role_code":"operations","name":"运营","permissions":[{"permission":"access.console","scope":"workspace","team_ids":[]},{"permission":"account.create","scope":"workspace","team_ids":[]},{"permission":"account.reset_password","scope":"workspace","team_ids":[]},{"permission":"account.unlock","scope":"workspace","team_ids":[]},{"permission":"account.update","scope":"workspace","team_ids":[]},{"permission":"actual.create","scope":"inherit","team_ids":[]},{"permission":"actual.read","scope":"inherit","team_ids":[]},{"permission":"actual.void","scope":"inherit","team_ids":[]},{"permission":"ai.execution_read","scope":"workspace","team_ids":[]},{"permission":"ai.run_export","scope":"workspace","team_ids":[]},{"permission":"ai.run_read","scope":"workspace","team_ids":[]},{"permission":"ai.usage_export","scope":"workspace","team_ids":[]},{"permission":"ai.usage_read","scope":"workspace","team_ids":[]},{"permission":"ai.usage_rules_manage","scope":"workspace","team_ids":[]},{"permission":"audit.business_export","scope":"workspace","team_ids":[]},{"permission":"audit.business_read","scope":"workspace","team_ids":[]},{"permission":"audit.events_export","scope":"workspace","team_ids":[]},{"permission":"audit.events_read","scope":"workspace","team_ids":[]},{"permission":"audit.export","scope":"workspace","team_ids":[]},{"permission":"audit.read","scope":"workspace","team_ids":[]},{"permission":"company.read","scope":"workspace","team_ids":[]},{"permission":"customer.claim_directory","scope":"workspace","team_ids":[]},{"permission":"customer.claim_review","scope":"inherit","team_ids":[]},{"permission":"customer.create","scope":"inherit","team_ids":[]},{"permission":"customer.export","scope":"inherit","team_ids":[]},{"permission":"customer.read","scope":"inherit","team_ids":[]},{"permission":"customer.reference","scope":"workspace","team_ids":[]},{"permission":"customer.release","scope":"inherit","team_ids":[]},{"permission":"customer.resolve_owner","scope":"inherit","team_ids":[]},{"permission":"customer.update","scope":"inherit","team_ids":[]},{"permission":"directory.read","scope":"inherit","team_ids":[]},{"permission":"history.import","scope":"workspace","team_ids":[]},{"permission":"notification.mark_read","scope":"self","team_ids":[]},{"permission":"notification.read","scope":"self","team_ids":[]},{"permission":"opportunity.close","scope":"inherit","team_ids":[]},{"permission":"opportunity.export","scope":"inherit","team_ids":[]},{"permission":"opportunity.fde_members","scope":"inherit","team_ids":[]},{"permission":"opportunity.quote_create","scope":"inherit","team_ids":[]},{"permission":"opportunity.read","scope":"inherit","team_ids":[]},{"permission":"opportunity.reopen","scope":"inherit","team_ids":[]},{"permission":"opportunity.update","scope":"inherit","team_ids":[]},{"permission":"organization.manage","scope":"workspace","team_ids":[]},{"permission":"organization.read","scope":"workspace","team_ids":[]},{"permission":"overview.read","scope":"inherit","team_ids":[]},{"permission":"partner.manage","scope":"workspace","team_ids":[]},{"permission":"partner.read","scope":"workspace","team_ids":[]},{"permission":"rule.draft","scope":"workspace","team_ids":[]},{"permission":"rule.preview","scope":"workspace","team_ids":[]},{"permission":"rule.read","scope":"workspace","team_ids":[]},{"permission":"target.approve","scope":"inherit","team_ids":[]},{"permission":"target.fde_department","scope":"inherit","team_ids":[]},{"permission":"target.history","scope":"inherit","team_ids":[]},{"permission":"target.manage","scope":"inherit","team_ids":[]},{"permission":"target.read","scope":"inherit","team_ids":[]},{"permission":"target.submit","scope":"inherit","team_ids":[]},{"permission":"task.accept","scope":"inherit","team_ids":[]},{"permission":"task.assign","scope":"inherit","team_ids":[]},{"permission":"task.cancel","scope":"inherit","team_ids":[]},{"permission":"task.complete","scope":"inherit","team_ids":[]},{"permission":"task.coordinate","scope":"inherit","team_ids":[]},{"permission":"task.create_customer","scope":"inherit","team_ids":[]},{"permission":"task.create_daily","scope":"inherit","team_ids":[]},{"permission":"task.decline","scope":"inherit","team_ids":[]},{"permission":"task.read","scope":"inherit","team_ids":[]},{"permission":"task.review","scope":"inherit","team_ids":[]},{"permission":"visit.download_original","scope":"inherit","team_ids":[]},{"permission":"visit.read","scope":"inherit","team_ids":[]}]},{"role_code":"administrator","name":"系统管理员","permissions":[{"permission":"access.console","scope":"workspace","team_ids":[]},{"permission":"account.create","scope":"workspace","team_ids":[]},{"permission":"account.password_policy","scope":"workspace","team_ids":[]},{"permission":"account.reset_password","scope":"workspace","team_ids":[]},{"permission":"account.unlock","scope":"workspace","team_ids":[]},{"permission":"account.update","scope":"workspace","team_ids":[]},{"permission":"actual.create","scope":"inherit","team_ids":[]},{"permission":"actual.read","scope":"inherit","team_ids":[]},{"permission":"actual.void","scope":"inherit","team_ids":[]},{"permission":"ai.config_publish","scope":"workspace","team_ids":[]},{"permission":"ai.config_read","scope":"workspace","team_ids":[]},{"permission":"ai.config_rollback","scope":"workspace","team_ids":[]},{"permission":"ai.config_test","scope":"workspace","team_ids":[]},{"permission":"ai.execution_read","scope":"workspace","team_ids":[]},{"permission":"ai.run_export","scope":"workspace","team_ids":[]},{"permission":"ai.run_read","scope":"workspace","team_ids":[]},{"permission":"ai.usage_export","scope":"workspace","team_ids":[]},{"permission":"ai.usage_read","scope":"workspace","team_ids":[]},{"permission":"ai.usage_rules_manage","scope":"workspace","team_ids":[]},{"permission":"audit.business_export","scope":"workspace","team_ids":[]},{"permission":"audit.business_read","scope":"workspace","team_ids":[]},{"permission":"audit.events_export","scope":"workspace","team_ids":[]},{"permission":"audit.events_read","scope":"workspace","team_ids":[]},{"permission":"audit.export","scope":"workspace","team_ids":[]},{"permission":"audit.read","scope":"workspace","team_ids":[]},{"permission":"authorization.accounts_manage","scope":"workspace","team_ids":[]},{"permission":"authorization.audit","scope":"workspace","team_ids":[]},{"permission":"authorization.read","scope":"workspace","team_ids":[]},{"permission":"authorization.roles_manage","scope":"workspace","team_ids":[]},{"permission":"company.read","scope":"workspace","team_ids":[]},{"permission":"company.update","scope":"workspace","team_ids":[]},{"permission":"customer.claim_directory","scope":"workspace","team_ids":[]},{"permission":"customer.claim_review","scope":"inherit","team_ids":[]},{"permission":"customer.create","scope":"inherit","team_ids":[]},{"permission":"customer.export","scope":"inherit","team_ids":[]},{"permission":"customer.read","scope":"inherit","team_ids":[]},{"permission":"customer.reference","scope":"workspace","team_ids":[]},{"permission":"customer.release","scope":"inherit","team_ids":[]},{"permission":"customer.resolve_owner","scope":"inherit","team_ids":[]},{"permission":"customer.update","scope":"inherit","team_ids":[]},{"permission":"directory.read","scope":"inherit","team_ids":[]},{"permission":"feishu.configure","scope":"workspace","team_ids":[]},{"permission":"feishu.control","scope":"workspace","team_ids":[]},{"permission":"feishu.read","scope":"workspace","team_ids":[]},{"permission":"feishu.recover","scope":"workspace","team_ids":[]},{"permission":"history.import","scope":"workspace","team_ids":[]},{"permission":"notification.mark_read","scope":"self","team_ids":[]},{"permission":"notification.read","scope":"self","team_ids":[]},{"permission":"opportunity.close","scope":"inherit","team_ids":[]},{"permission":"opportunity.create","scope":"inherit","team_ids":[]},{"permission":"opportunity.create_for_others","scope":"inherit","team_ids":[]},{"permission":"opportunity.export","scope":"inherit","team_ids":[]},{"permission":"opportunity.fde_members","scope":"inherit","team_ids":[]},{"permission":"opportunity.quote_create","scope":"inherit","team_ids":[]},{"permission":"opportunity.read","scope":"inherit","team_ids":[]},{"permission":"opportunity.reopen","scope":"inherit","team_ids":[]},{"permission":"opportunity.update","scope":"inherit","team_ids":[]},{"permission":"organization.manage","scope":"workspace","team_ids":[]},{"permission":"organization.read","scope":"workspace","team_ids":[]},{"permission":"overview.read","scope":"inherit","team_ids":[]},{"permission":"partner.manage","scope":"workspace","team_ids":[]},{"permission":"partner.read","scope":"workspace","team_ids":[]},{"permission":"rule.draft","scope":"workspace","team_ids":[]},{"permission":"rule.preview","scope":"workspace","team_ids":[]},{"permission":"rule.publish","scope":"workspace","team_ids":[]},{"permission":"rule.read","scope":"workspace","team_ids":[]},{"permission":"rule.restore","scope":"workspace","team_ids":[]},{"permission":"target.approve","scope":"inherit","team_ids":[]},{"permission":"target.fde_department","scope":"inherit","team_ids":[]},{"permission":"target.history","scope":"inherit","team_ids":[]},{"permission":"target.manage","scope":"inherit","team_ids":[]},{"permission":"target.read","scope":"inherit","team_ids":[]},{"permission":"target.submit","scope":"inherit","team_ids":[]},{"permission":"task.accept","scope":"inherit","team_ids":[]},{"permission":"task.assign","scope":"inherit","team_ids":[]},{"permission":"task.cancel","scope":"inherit","team_ids":[]},{"permission":"task.complete","scope":"inherit","team_ids":[]},{"permission":"task.coordinate","scope":"inherit","team_ids":[]},{"permission":"task.create_customer","scope":"inherit","team_ids":[]},{"permission":"task.create_daily","scope":"inherit","team_ids":[]},{"permission":"task.decline","scope":"inherit","team_ids":[]},{"permission":"task.read","scope":"inherit","team_ids":[]},{"permission":"task.review","scope":"inherit","team_ids":[]},{"permission":"visit.download_original","scope":"inherit","team_ids":[]},{"permission":"visit.read","scope":"inherit","team_ids":[]}]}]$defaults$::jsonb) AS x(role_code text,name text,permissions jsonb);

CREATE FUNCTION security.initialize_permission_roles(p_workspace uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 INSERT INTO config.permission_role(workspace_id,name,builtin_role_code)
 SELECT p_workspace,name,role_code FROM config.permission_role_default;
 INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code)
 SELECT p_workspace,r.id,g->>'permission',g->>'scope'
 FROM config.permission_role r JOIN config.permission_role_default d ON d.role_code=r.builtin_role_code
 CROSS JOIN LATERAL jsonb_array_elements(d.permissions) g WHERE r.workspace_id=p_workspace;
END $$;
REVOKE ALL ON FUNCTION security.initialize_permission_roles(uuid) FROM PUBLIC;
SELECT security.initialize_permission_roles(id) FROM platform.workspace;
CREATE FUNCTION security.initialize_workspace_permissions() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN PERFORM security.initialize_permission_roles(NEW.id); RETURN NEW; END $$;
REVOKE ALL ON FUNCTION security.initialize_workspace_permissions() FROM PUBLIC;
CREATE TRIGGER initialize_workspace_permissions AFTER INSERT ON platform.workspace
 FOR EACH ROW EXECUTE FUNCTION security.initialize_workspace_permissions();
CREATE TRIGGER authorization_audit_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON config.authorization_audit
 FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();
ALTER TABLE config.permission_catalog ENABLE ROW LEVEL SECURITY;
ALTER TABLE config.permission_catalog FORCE ROW LEVEL SECURITY;
CREATE POLICY catalog_read ON config.permission_catalog FOR SELECT USING (true);
ALTER TABLE config.permission_role_default ENABLE ROW LEVEL SECURITY;
ALTER TABLE config.permission_role_default FORCE ROW LEVEL SECURITY;
CREATE POLICY defaults_read ON config.permission_role_default FOR SELECT USING (true);
REVOKE ALL ON FUNCTION security.authorization_has(text),security.authorization_snapshot(uuid) FROM PUBLIC,salegent_feishu_worker;

CREATE FUNCTION security.validate_authorization_grants(p_items jsonb,p_overrides boolean DEFAULT false) RETURNS void
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
   OR (p_overrides AND NOT denied AND v_scope='inherit') OR (denied AND v_scope<>'inherit') THEN
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
REVOKE ALL ON FUNCTION security.validate_authorization_grants(jsonb,boolean) FROM PUBLIC;

CREATE FUNCTION security.require_permission_administrator() RETURNS void
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.workspace_id=common.current_workspace_id()
  AND u.status='active' AND u.deleted_at IS NULL
  AND NULLIF(btrim(u.account_code),'') IS NOT NULL AND EXISTS(SELECT 1 FROM platform.password_credential credential
   WHERE credential.user_ref_id=u.id AND credential.workspace_id=u.workspace_id)
  AND (
   SELECT count(DISTINCT g.permission_code)=4 FROM security.authorization_grants_for(u.workspace_id,u.id) g
   WHERE g.permission_code IN ('access.console','authorization.read','authorization.roles_manage','authorization.accounts_manage')
    AND g.effect='allow' AND NOT EXISTS(SELECT 1 FROM security.authorization_grants_for(u.workspace_id,u.id) d
     WHERE d.permission_code=g.permission_code AND d.effect='deny'))) THEN
  RAISE EXCEPTION '必须保留至少一位可登录后台并管理权限的有效管理员' USING ERRCODE='22023';
 END IF;
END $$;
REVOKE ALL ON FUNCTION security.require_permission_administrator() FROM PUBLIC;

CREATE FUNCTION security.save_permission_role(p_id uuid,p_body jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
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
   status=p_body->>'status',version_no=version_no+1,updated_at=clock_timestamp() WHERE id=p_id RETURNING version_no INTO next_version;
  DELETE FROM config.permission_role_grant WHERE role_id=p_id AND workspace_id=ws;
 ELSE
  INSERT INTO config.permission_role(id,workspace_id,name,description,status)
   VALUES(rid,ws,btrim(p_body->>'name'),COALESCE(p_body->>'description',''),p_body->>'status') RETURNING version_no INTO next_version;
 END IF;
 FOR g IN SELECT * FROM jsonb_array_elements(p_body->'permissions') LOOP
  INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code,team_ids)
   VALUES(ws,rid,g->>'permission',COALESCE(g->>'scope','inherit'),ARRAY(SELECT v::uuid FROM jsonb_array_elements_text(COALESCE(g->'team_ids','[]')) v));
 END LOOP;
 PERFORM security.require_permission_administrator();
 result:=jsonb_build_object('id',rid,'version_no',next_version,'name',btrim(p_body->>'name'),'status',p_body->>'status','permissions',p_body->'permissions');
 INSERT INTO config.authorization_audit(workspace_id,actor_user_ref_id,subject_type,subject_id,before_snapshot,after_snapshot,reason)
  VALUES(ws,common.current_user_ref_id(),'role',rid,old,result,btrim(p_body->>'reason'));
 PERFORM set_config('app.authorization_context','',true);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION security.save_permission_role(uuid,jsonb) FROM PUBLIC;

CREATE FUNCTION security.save_account_authorization(p_user uuid,p_body jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
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
  INSERT INTO config.account_permission_role(workspace_id,user_ref_id,role_id,scope_code,team_ids)
   VALUES(ws,p_user,(item->>'role_id')::uuid,item->>'scope',teams);
 END LOOP;
 FOR item IN SELECT * FROM jsonb_array_elements(p_body->'overrides') LOOP
  INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code,team_ids)
  VALUES(ws,p_user,item->>'permission',item->>'effect',CASE WHEN item->>'effect'='allow' THEN item->>'scope' END,
   ARRAY(SELECT v::uuid FROM jsonb_array_elements_text(COALESCE(item->'team_ids','[]')) v));
 END LOOP;
 PERFORM security.require_permission_administrator();
 result:=jsonb_build_object('user_id',p_user,'version_no',next_version,'roles',p_body->'roles','overrides',p_body->'overrides');
 INSERT INTO config.authorization_audit(workspace_id,actor_user_ref_id,subject_type,subject_id,before_snapshot,after_snapshot,reason)
  VALUES(ws,common.current_user_ref_id(),'account',p_user,old,result,btrim(p_body->>'reason'));
 PERFORM set_config('app.authorization_context','',true);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION security.save_account_authorization(uuid,jsonb) FROM PUBLIC;

CREATE FUNCTION security.authorization_directory() RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT security.authorization_has('authorization.read') THEN RAISE insufficient_privilege; END IF;
 RETURN jsonb_build_object(
 'accounts',(SELECT COALESCE(jsonb_agg(jsonb_build_object('id',u.id,'name',u.display_name,'account_code',u.account_code,'status',u.status) ORDER BY u.display_name,u.id),'[]')
  FROM platform.user_ref u WHERE u.workspace_id=common.current_workspace_id() AND u.deleted_at IS NULL),
 'teams',(SELECT COALESCE(jsonb_agg(jsonb_build_object('id',t.id,'name',t.name) ORDER BY t.name,t.id),'[]')
  FROM platform.team t WHERE t.workspace_id=common.current_workspace_id() AND t.status='active' AND t.deleted_at IS NULL
   AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to));
END $$;
REVOKE ALL ON FUNCTION security.authorization_directory() FROM PUBLIC;

CREATE OR REPLACE FUNCTION security.company_directory()
 RETURNS jsonb
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 SELECT COALESCE(jsonb_agg(jsonb_build_object(
  'id',w.id,'name',w.name,'code',w.external_workspace_id,'kind',COALESCE(w.attributes->>'kind','standard'),
  'status',w.status,'version_no',w.version_no,
  'account_count',(SELECT count(*) FROM platform.user_ref u WHERE u.workspace_id=w.id AND u.deleted_at IS NULL AND u.status='active'),
  'department_count',(SELECT count(*) FROM platform.team t WHERE t.workspace_id=w.id AND t.deleted_at IS NULL AND t.status='active')
 ) ORDER BY w.created_at,w.id),'[]'::jsonb)
 FROM platform.workspace w WHERE w.status='active' AND w.deleted_at IS NULL
 AND security.authorization_has('access.console') AND (w.id=common.current_workspace_id() OR security.company_management_actor(w.id) IS NOT NULL);
$function$
;

ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v125;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_v125();
 FOR r IN SELECT rolname FROM pg_roles
 WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
 AND rolname NOT LIKE 'pg\_%' ESCAPE '\'
 AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE')
 LOOP
  EXECUTE format('GRANT SELECT ON config.permission_catalog,config.permission_role_default TO %I',r.rolname);
  EXECUTE format('GRANT SELECT ON config.permission_role,config.permission_role_grant,config.account_authorization,config.account_permission_role,config.account_permission_override TO %I',r.rolname);
  EXECUTE format('GRANT SELECT ON config.authorization_audit TO %I',r.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.authorization_has(text),security.authorization_snapshot(uuid),security.authorization_refresh(),security.authorization_current_grants(),security.save_permission_role(uuid,jsonb),security.save_account_authorization(uuid,jsonb),security.authorization_directory() TO %I',r.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION security.authorization_grants_for(uuid,uuid),security.authorization_legacy_fde_visit(uuid,uuid,text),security.initialize_permission_roles(uuid),security.initialize_workspace_permissions() FROM %I',r.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_v125() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V126','Configurable permission roles and account overrides');
COMMIT;
