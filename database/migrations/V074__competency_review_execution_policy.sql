BEGIN;
-- Sales competency reviews use the existing administrator-controlled Agent
-- execution policy lifecycle. Preserve all earlier supported policy codes.
CREATE OR REPLACE FUNCTION security.company_rule_supported(p_code text) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT p_code IN ('customer_quadrant','visit_admission','home_display','task_schedule','fde_capabilities',
 'agent_execution.battle_map_review','agent_execution.opportunity_draft','agent_execution.personal_risks',
 'agent_execution.visit_entry','agent_execution.today_tasks','agent_execution.operating_report','agent_execution.chatbi',
 'score.maturity','score.efficiency','score.competency','agent_execution.customer_advice',
 'agent_execution.opportunity_advice','agent_execution.visit_advice','agent_execution.competency_review');
$$;
INSERT INTO config.rule_set(rule_code,name,rule_type,version_no,status,definition)
VALUES('agent_execution.competency_review','销售六维能力复盘运行配置基线','company_policy',1,'active',
 '{"schema_version":1,"strategy":"inherit","override_budget":false,"platform_seconds":12,"total_seconds":45}'::jsonb);
INSERT INTO ops.schema_migration(version,description)
VALUES('V074','销售六维能力复盘接入原生Agent运行策略、管理员发布与历史版本');
COMMIT;
