-- Business guidance is company-scoped, revisioned and published via the existing
-- append-only company-policy workflow. It grants neither tools nor data access.
CREATE OR REPLACE FUNCTION security.company_rule_supported(p_code text) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT p_code IN (
 'customer_quadrant','visit_admission','home_display','task_schedule','fde_capabilities',
 'score.maturity','score.efficiency','score.competency',
 'agent_execution.battle_map_review','agent_execution.opportunity_draft','agent_execution.personal_risks',
 'agent_execution.visit_entry','agent_execution.visit_quality','agent_execution.today_tasks',
 'agent_execution.operating_report','agent_execution.chatbi','agent_execution.customer_advice',
 'agent_execution.opportunity_advice','agent_execution.visit_advice','agent_execution.competency_review',
 'agent_business.battle_map_review','agent_business.opportunity_draft','agent_business.personal_risks',
 'agent_business.visit_entry','agent_business.visit_quality','agent_business.today_tasks',
 'agent_business.operating_report','agent_business.chatbi','agent_business.customer_advice',
 'agent_business.opportunity_advice','agent_business.visit_advice','agent_business.competency_review');
$$;

INSERT INTO config.rule_set(rule_code,name,rule_type,version_no,status,definition)
SELECT 'agent_business.'||code,label||'业务指引基线','company_policy',1,'active',
 '{"schema_version":1,"guidance":"","calibration_examples":""}'::jsonb
FROM (VALUES
 ('visit_quality','拜访记录质检'),
 ('customer_advice','客户经营建议'),
 ('opportunity_advice','商机经营建议'),
 ('visit_advice','单次拜访建议'),
 ('battle_map_review','客户作战地图评估'),
 ('opportunity_draft','商机新建/更新判断'),
 ('personal_risks','个人客户风险识别'),
 ('visit_entry','拜访记录结构化'),
 ('today_tasks','今日待办规划'),
 ('operating_report','经营即时总结'),
 ('competency_review','销售六维能力复盘'),
 ('chatbi','经营问数（二期）')
) AS capabilities(code,label);
