BEGIN;
CREATE OR REPLACE FUNCTION security.company_rule_supported(p_code text) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
SELECT p_code IN ('customer_quadrant','visit_admission','home_display','task_schedule');
$$;
INSERT INTO config.rule_set(rule_code,name,rule_type,version_no,status,definition)
VALUES('task_schedule','今日待办默认时间既有基线','company_policy',1,'active',$policy${"schema_version": 1, "timezone": "UTC", "today_at": "09:00", "next_day_at": "02:00", "minimum_lead_minutes": 5}$policy$::jsonb);
INSERT INTO ops.schema_migration(version,description) VALUES('V059','新待办默认未来时间按公司版本执行');
COMMIT;
