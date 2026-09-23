-- Raw file storage is independent of parsed text and immutable visit snapshots.
ALTER TABLE activity.visit_import
 ADD COLUMN storage_profile text NOT NULL DEFAULT 'legacy-local',
 ADD COLUMN storage_driver text NOT NULL DEFAULT 'local' CHECK(storage_driver IN ('local','postgres','s3')),
 ADD COLUMN storage_key text,
 ADD COLUMN content_sha256 text CHECK(content_sha256 IS NULL OR content_sha256 ~ '^[a-f0-9]{64}$'),
 ADD CONSTRAINT visit_import_workspace_unique UNIQUE(id,workspace_id);
UPDATE activity.visit_import SET storage_key=file_path;
ALTER TABLE activity.visit_import ALTER COLUMN storage_key SET NOT NULL;
CREATE TABLE activity.visit_import_content (
 import_id uuid NOT NULL,
 workspace_id uuid NOT NULL,
 chunk_no integer NOT NULL CHECK(chunk_no>=0),
 content bytea NOT NULL CHECK(octet_length(content)>0 AND octet_length(content)<=1048576),
 PRIMARY KEY(import_id,chunk_no),
 FOREIGN KEY(import_id,workspace_id) REFERENCES activity.visit_import(id,workspace_id) ON DELETE CASCADE
);
ALTER TABLE activity.visit_import_content ENABLE ROW LEVEL SECURITY;
CREATE POLICY visit_material_scope ON activity.visit_import_content
 USING(workspace_id=common.current_workspace_id() AND EXISTS(
  SELECT 1 FROM activity.visit_import i WHERE i.id=visit_import_content.import_id AND i.workspace_id=visit_import_content.workspace_id))
 WITH CHECK(workspace_id=common.current_workspace_id() AND EXISTS(
  SELECT 1 FROM activity.visit_import i WHERE i.id=visit_import_content.import_id AND i.workspace_id=visit_import_content.workspace_id
    AND i.created_by_user_ref_id=common.current_user_ref_id()));
-- Keep historical rows intact. NOT VALID checks new/changed rows; validate after orphan preflight.
ALTER TABLE activity.visit ADD CONSTRAINT visit_source_import_fk
 FOREIGN KEY(source_import_id,workspace_id) REFERENCES activity.visit_import(id,workspace_id) NOT VALID;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM activity.visit v LEFT JOIN activity.visit_import i
 ON i.id=v.source_import_id AND i.workspace_id=v.workspace_id
 WHERE v.source_import_id IS NOT NULL AND i.id IS NULL) THEN
 ALTER TABLE activity.visit VALIDATE CONSTRAINT visit_source_import_fk;
 END IF;
END $$;
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
 WHERE table_schema='activity' AND table_name='visit_import' AND privilege_type='INSERT' AND grantee<>'PUBLIC'
 LOOP EXECUTE format('GRANT SELECT,INSERT,DELETE ON activity.visit_import_content TO %I',r.grantee); END LOOP;
END $$;
COMMENT ON TABLE activity.visit_import_content IS 'Original bytes in 1MiB chunks. Parsed text remains on visit_import; no knowledge base ingestion.';
COMMENT ON COLUMN activity.visit_import.storage_profile IS 'Immutable profile ID per file; default config affects new uploads only.';

-- New chain creates follow-up tasks only after explicit adoption of post-archive advice.
ALTER TABLE activity.visit ADD COLUMN follow_up_task_mode text NOT NULL DEFAULT 'legacy'
 CHECK(follow_up_task_mode IN ('legacy','human_advice'));
COMMENT ON COLUMN activity.visit.follow_up_task_mode IS 'legacy: old today planner; human_advice: explicit human adoption only';

CREATE OR REPLACE FUNCTION security.company_rule_supported(p_code text) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT p_code IN ('customer_quadrant','visit_admission','home_display','task_schedule','fde_capabilities',
 'agent_execution.battle_map_review','agent_execution.opportunity_draft','agent_execution.personal_risks',
 'agent_execution.visit_entry','agent_execution.visit_quality','agent_execution.today_tasks','agent_execution.operating_report','agent_execution.chatbi',
 'score.maturity','score.efficiency','score.competency','agent_execution.customer_advice',
 'agent_execution.opportunity_advice','agent_execution.visit_advice','agent_execution.competency_review');
$$;
INSERT INTO config.rule_set(rule_code,name,rule_type,version_no,status,definition) VALUES
('agent_execution.visit_quality','拜访记录质检运行配置基线','company_policy',1,'active',
'{"schema_version":1,"strategy":"inherit","override_budget":false,"platform_seconds":12,"total_seconds":45}'::jsonb);
