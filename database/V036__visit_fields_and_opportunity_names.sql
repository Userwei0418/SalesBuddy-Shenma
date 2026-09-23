BEGIN;
SELECT pg_advisory_xact_lock(2026091003);
ALTER TABLE activity.visit ADD COLUMN IF NOT EXISTS customer_type_code_snapshot text;
ALTER TABLE activity.visit ADD COLUMN IF NOT EXISTS created_by_user_ref_id uuid REFERENCES platform.user_ref(id);
UPDATE activity.visit SET created_by_user_ref_id=COALESCE(confirmed_by_user_ref_id,recorder_user_ref_id)
 WHERE created_by_user_ref_id IS NULL;
COMMENT ON COLUMN activity.visit.customer_type_code_snapshot IS '归档时客户类型；历史未知值保持空';
COMMENT ON COLUMN activity.visit.created_by_user_ref_id IS '记录创建人；服务端身份写入，历史沿用确认人或记录人';
COMMENT ON COLUMN activity.visit.recorder_user_ref_id IS '跟进人，默认创建人，与创建人独立保存';
COMMENT ON COLUMN activity.visit.interaction_at IS '实际拜访日期；新表单按北京时间日期选择，历史时刻保留';
INSERT INTO config.field_definition(object_type,field_key,label,data_type)
SELECT 'visit','customer_type','客户类型','text'
WHERE NOT EXISTS(SELECT 1 FROM config.field_definition WHERE object_type='visit' AND field_key='customer_type');
UPDATE config.field_definition SET label=CASE field_key
 WHEN 'interaction_at' THEN '拜访时间' WHEN 'recorder_user_id' THEN '跟进人'
 WHEN 'follow_up_record' THEN '沟通内容' WHEN 'next_action' THEN '下一步计划' ELSE label END
 WHERE object_type='visit';
CREATE OR REPLACE FUNCTION crm.normalize_opportunity_name(value text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$ SELECT lower(regexp_replace(value, '\s+', '', 'g')) $$;
-- Authorized demo cleanup: preserve IDs and relationships, audit each rename.
LOCK TABLE crm.opportunity IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE r record; candidate text; suffix integer; BEGIN
 FOR r IN SELECT * FROM (
   SELECT id,workspace_id,customer_id,name,
     row_number() OVER(PARTITION BY workspace_id,customer_id,crm.normalize_opportunity_name(name)
                       ORDER BY created_at,id) AS position
   FROM crm.opportunity WHERE deleted_at IS NULL
 ) duplicates WHERE position>1 ORDER BY workspace_id,customer_id,name,position
 LOOP
   suffix:=r.position;
   LOOP
     candidate:=left(btrim(r.name),170)||'（演示项目 '||suffix||'）';
     EXIT WHEN NOT EXISTS(SELECT 1 FROM crm.opportunity WHERE workspace_id=r.workspace_id
       AND customer_id=r.customer_id AND deleted_at IS NULL
       AND crm.normalize_opportunity_name(name)=crm.normalize_opportunity_name(candidate));
     suffix:=suffix+1;
   END LOOP;
   UPDATE crm.opportunity SET name=candidate,version_no=version_no+1,updated_at=clock_timestamp(),
     import_meta=COALESCE(import_meta,'{}'::jsonb)||jsonb_build_object('name_dedup_v036',
       jsonb_build_object('previous_name',r.name,'renamed_name',candidate,'reason','用户确认演示商机消歧'))
     WHERE id=r.id;
 END LOOP;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_opportunity_customer_name
 ON crm.opportunity(workspace_id,customer_id,crm.normalize_opportunity_name(name)) WHERE deleted_at IS NULL;
INSERT INTO ops.schema_migration(version,description)
VALUES('V036','拜访客户类型快照与创建人、演示商机消歧及客户内名称唯一') ON CONFLICT DO NOTHING;
COMMIT;
