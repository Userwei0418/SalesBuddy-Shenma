BEGIN;
CREATE OR REPLACE FUNCTION security.company_rule_supported(p_code text) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
SELECT p_code IN ('customer_quadrant','visit_admission','home_display','task_schedule','agent_execution.battle_map_review','agent_execution.opportunity_draft','agent_execution.personal_risks','agent_execution.visit_entry','agent_execution.today_tasks','agent_execution.operating_report','agent_execution.chatbi');
$$;
INSERT INTO config.rule_set(rule_code,name,rule_type,version_no,status,definition) VALUES('agent_execution.battle_map_review','客户作战地图评估运行配置基线','company_policy',1,'active',$policy${"schema_version": 1, "strategy": "inherit", "override_budget": false, "platform_seconds": 12, "total_seconds": 45}$policy$::jsonb);
INSERT INTO config.rule_set(rule_code,name,rule_type,version_no,status,definition) VALUES('agent_execution.opportunity_draft','商机新建/更新判断运行配置基线','company_policy',1,'active',$policy${"schema_version": 1, "strategy": "inherit", "override_budget": false, "platform_seconds": 12, "total_seconds": 45}$policy$::jsonb);
INSERT INTO config.rule_set(rule_code,name,rule_type,version_no,status,definition) VALUES('agent_execution.personal_risks','个人客户风险识别运行配置基线','company_policy',1,'active',$policy${"schema_version": 1, "strategy": "inherit", "override_budget": false, "platform_seconds": 12, "total_seconds": 45}$policy$::jsonb);
INSERT INTO config.rule_set(rule_code,name,rule_type,version_no,status,definition) VALUES('agent_execution.visit_entry','拜访结构化与质检运行配置基线','company_policy',1,'active',$policy${"schema_version": 1, "strategy": "inherit", "override_budget": false, "platform_seconds": 12, "total_seconds": 45}$policy$::jsonb);
INSERT INTO config.rule_set(rule_code,name,rule_type,version_no,status,definition) VALUES('agent_execution.today_tasks','今日待办规划运行配置基线','company_policy',1,'active',$policy${"schema_version": 1, "strategy": "inherit", "override_budget": false, "platform_seconds": 12, "total_seconds": 45}$policy$::jsonb);
INSERT INTO config.rule_set(rule_code,name,rule_type,version_no,status,definition) VALUES('agent_execution.operating_report','经营即时总结运行配置基线','company_policy',1,'active',$policy${"schema_version": 1, "strategy": "inherit", "override_budget": false, "platform_seconds": 12, "total_seconds": 45}$policy$::jsonb);
INSERT INTO config.rule_set(rule_code,name,rule_type,version_no,status,definition) VALUES('agent_execution.chatbi','经营问数（二期）运行配置基线','company_policy',1,'active',$policy${"schema_version": 1, "strategy": "inherit", "override_budget": false, "platform_seconds": 12, "total_seconds": 45}$policy$::jsonb);
CREATE OR REPLACE FUNCTION security.save_company_rule(p_code text,p_name text,p_definition jsonb,p_reason text,
 p_id uuid,p_revision integer,p_base uuid,p_restored uuid DEFAULT NULL) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_id uuid; v_current uuid; v_old config.rule_set;
BEGIN
 IF NOT security.management_actor() THEN RAISE insufficient_privilege; END IF;
 IF p_code LIKE 'agent_execution.%' AND common.current_role_code()<>'administrator' THEN RAISE insufficient_privilege; END IF;
 IF NOT security.company_rule_supported(p_code) OR jsonb_typeof(p_definition)<>'object'
 OR octet_length(p_definition::text)>16000 OR length(trim(p_reason)) NOT BETWEEN 1 AND 500 THEN
 RAISE EXCEPTION '规则或变更原因不合法'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(common.current_workspace_id()::text || ':rule:' || p_code,0));
 v_current:=security.active_company_rule(p_code);
 IF p_base IS DISTINCT FROM v_current THEN RAISE EXCEPTION '生效规则已变化，请刷新并重新核对'; END IF;
 IF p_restored IS NOT NULL AND NOT EXISTS(SELECT 1 FROM config.rule_set WHERE id=p_restored
   AND rule_code=p_code AND status IN ('active','retired')
   AND (workspace_id=common.current_workspace_id() OR workspace_id IS NULL)) THEN RAISE no_data_found; END IF;
 IF p_id IS NULL THEN
   INSERT INTO config.rule_set(workspace_id,rule_code,name,rule_type,version_no,status,definition,
      created_by_user_ref_id,updated_by_user_ref_id,change_reason,base_rule_id,restored_from_id)
   SELECT common.current_workspace_id(),p_code,p_name,'company_policy',COALESCE(max(version_no),0)+1,'draft',
      p_definition,common.current_user_ref_id(),common.current_user_ref_id(),p_reason,p_base,p_restored
   FROM config.rule_set WHERE workspace_id=common.current_workspace_id() AND rule_code=p_code RETURNING id INTO v_id;
 ELSE
   SELECT * INTO v_old FROM config.rule_set WHERE id=p_id AND workspace_id=common.current_workspace_id() FOR UPDATE;
   IF NOT FOUND THEN RAISE no_data_found; END IF;
   IF v_old.status<>'draft' OR v_old.rule_code<>p_code OR v_old.revision_no<>p_revision THEN
     RAISE EXCEPTION '草稿已变化或已发布，请刷新'; END IF;
   UPDATE config.rule_set SET definition=p_definition,change_reason=p_reason,revision_no=revision_no+1,
       updated_by_user_ref_id=common.current_user_ref_id(),base_rule_id=p_base WHERE id=p_id;
   v_id:=p_id;
 END IF;
 INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,object_type,object_id,
     after_snapshot,result_code,sensitivity)
 VALUES(common.current_workspace_id(),common.current_user_ref_id(),common.current_role_code(),
     'company_rule.draft','company_rule',v_id,jsonb_build_object('rule_code',p_code,'reason',p_reason),'success','internal');
 RETURN v_id;
END; $$;

CREATE OR REPLACE FUNCTION security.publish_company_rule(p_id uuid,p_revision integer) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_rule config.rule_set; v_current uuid;
BEGIN
 IF NOT security.management_actor() OR common.current_role_code()<>'administrator' THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO v_rule FROM config.rule_set WHERE id=p_id AND workspace_id=common.current_workspace_id();
 IF NOT FOUND THEN RAISE no_data_found; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(common.current_workspace_id()::text || ':rule:' || v_rule.rule_code,0));
 SELECT * INTO v_rule FROM config.rule_set WHERE id=p_id AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF NOT security.company_rule_supported(v_rule.rule_code) THEN RAISE insufficient_privilege; END IF;
 IF v_rule.status<>'draft' OR v_rule.revision_no<>p_revision THEN RAISE EXCEPTION '草稿已变化或已发布，请刷新'; END IF;
 v_current:=security.active_company_rule(v_rule.rule_code);
 IF v_rule.base_rule_id IS DISTINCT FROM v_current THEN RAISE EXCEPTION '生效规则已变化，请重新核对草稿'; END IF;
 UPDATE config.rule_set SET status='retired',effective_to=clock_timestamp()
 WHERE id=v_current AND workspace_id=common.current_workspace_id();
 UPDATE config.rule_set SET status='active',effective_from=clock_timestamp(),effective_to='infinity',
   published_at=clock_timestamp(),published_by_user_ref_id=common.current_user_ref_id(),revision_no=revision_no+1 WHERE id=p_id;
 INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,object_type,object_id,
     before_snapshot,after_snapshot,result_code,sensitivity)
 VALUES(common.current_workspace_id(),common.current_user_ref_id(),common.current_role_code(),
     'company_rule.publish','company_rule',p_id,jsonb_build_object('active_rule_id',v_current),
     jsonb_build_object('rule_code',v_rule.rule_code,'version',v_rule.version_no,'reason',v_rule.change_reason,
                       'restored_from_id',v_rule.restored_from_id),'success','internal');
 RETURN p_id;
END; $$;
INSERT INTO ops.schema_migration(version,description) VALUES('V060','管理员按Agent发布运行开关和等待预算，保留既有范围');
COMMIT;
