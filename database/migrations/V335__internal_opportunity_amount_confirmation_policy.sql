-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V187__opportunity_amount_confirmation_policy.sql.
-- Original SHA-256: 2e3dc3494dc7f6efeb0c41adfa54aa1f9ead2d8c47919fa8b615efa51eef2f46. See shenma_whole_121_manifest.json.
-- Add an independently published company policy; amounts remain yuan.
-- Existing rule codes, permissions and append-only releases are preserved.
CREATE OR REPLACE FUNCTION security.company_rule_supported(p_code text) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT p_code IN (
 'opportunity_amount','customer_quadrant','visit_admission','home_display','task_schedule','fde_capabilities',
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
VALUES ('opportunity_amount','商机金额提醒默认规则','company_policy',1,'active',
        '{"schema_version":1,"warning_threshold_wan":1000}'::jsonb);
