BEGIN;
SELECT pg_advisory_xact_lock(2026091002);
ALTER TABLE activity.visit ADD COLUMN IF NOT EXISTS visit_goal text;
ALTER TABLE activity.visit ALTER COLUMN interaction_at DROP NOT NULL;
ALTER TABLE activity.visit ADD COLUMN IF NOT EXISTS collaborator_user_ref_ids uuid[] NOT NULL DEFAULT '{}';
ALTER TABLE activity.visit ADD COLUMN IF NOT EXISTS source_import_id uuid;
COMMENT ON COLUMN activity.visit.visit_goal IS '人工确认的本次拜访目标；历史记录可为空';
COMMENT ON COLUMN activity.visit.collaborator_user_ref_ids IS '参与拜访的同事ID，服务端校验同工作空间，不改变客户权限';

CREATE TABLE IF NOT EXISTS activity.visit_import (
 id uuid PRIMARY KEY, workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 created_by_user_ref_id uuid NOT NULL REFERENCES platform.user_ref(id),
 filename text NOT NULL, file_path text NOT NULL, file_size bigint NOT NULL,
 status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','processing','succeeded','failed')),
 extracted_text text, error_message text, evidence jsonb NOT NULL DEFAULT '[]',
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE activity.visit_import ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='activity' AND tablename='visit_import') THEN
  CREATE POLICY visit_import_owner ON activity.visit_import
   USING (workspace_id=common.current_workspace_id() AND created_by_user_ref_id=common.current_user_ref_id())
   WITH CHECK (workspace_id=common.current_workspace_id() AND created_by_user_ref_id=common.current_user_ref_id());
 END IF;
END $$;

-- Copy table privileges from the existing RLS-protected visit table, without adding roles.
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
          WHERE table_schema='activity' AND table_name='visit' AND privilege_type='INSERT' AND grantee <> 'PUBLIC'
 LOOP EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON activity.visit_import TO %I',r.grantee); END LOOP;
END $$;

INSERT INTO config.field_definition (object_type,field_key,label,data_type)
SELECT 'visit','visit_goal','拜访目标','long_text'
WHERE NOT EXISTS (SELECT 1 FROM config.field_definition WHERE object_type='visit' AND field_key='visit_goal');
INSERT INTO config.form_version(id,form_definition_id,version_no,status,schema_snapshot)
SELECT '31000000-0000-0000-0000-000000000035','30000000-0000-0000-0000-000000000001',
 COALESCE(max(version_no),0)+1,'active','{"contract":"visit.v2","core":["visit_goal","follow_up_record","next_action"],"threshold":70}'
FROM config.form_version WHERE form_definition_id='30000000-0000-0000-0000-000000000001'
ON CONFLICT(id) DO NOTHING;
INSERT INTO config.form_version_field(form_version_id,field_definition_id,display_order,is_required)
SELECT '31000000-0000-0000-0000-000000000035',id,
 row_number() OVER(ORDER BY field_key)::int,
 field_key IN ('visit_goal','follow_up_record','next_action','customer_name')
FROM config.field_definition WHERE object_type='visit' AND is_active
ON CONFLICT DO NOTHING;
INSERT INTO ops.schema_migration(version,description) VALUES('V035','拜访三段正文、协同人和持久化文件处理') ON CONFLICT DO NOTHING;
COMMIT;
