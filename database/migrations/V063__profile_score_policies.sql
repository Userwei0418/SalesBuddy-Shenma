BEGIN;
CREATE OR REPLACE FUNCTION security.company_rule_supported(p_code text) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
SELECT p_code IN ('customer_quadrant','visit_admission','home_display','task_schedule','agent_execution.battle_map_review','agent_execution.opportunity_draft','agent_execution.personal_risks','agent_execution.visit_entry','agent_execution.today_tasks','agent_execution.operating_report','agent_execution.chatbi','score.maturity','score.efficiency','score.competency');
$$;
INSERT INTO config.rule_set(rule_code,name,rule_type,version_no,status,definition) VALUES('score.maturity','营销成熟度总分权重基线','company_policy',1,'active','{"schema_version": 1, "collection": 40, "recognized": 40, "retention": 20}'::jsonb);
INSERT INTO config.rule_set(rule_code,name,rule_type,version_no,status,definition) VALUES('score.efficiency','营销效率总分权重基线','company_policy',1,'active','{"schema_version": 1, "followup": 40, "customers": 30, "opportunities": 30}'::jsonb);
INSERT INTO config.rule_set(rule_code,name,rule_type,version_no,status,definition) VALUES('score.competency','销售画像总分权重基线','company_policy',1,'active','{"schema_version": 1, "needs_discovery": 18, "stakeholder_navigation": 16, "solution_communication": 18, "opportunity_advancement": 20, "relationship_management": 14, "followup_discipline": 14}'::jsonb);
INSERT INTO ops.schema_migration(version,description) VALUES('V063','三类总分统一服务端权重与可发布公司规则');
COMMIT;
