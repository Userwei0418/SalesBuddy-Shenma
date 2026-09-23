-- Imported CRM history can lack a first-visit assertion. Normal entry retains
-- its boolean default and cannot silently start writing unknown flags.
ALTER TABLE activity.visit ALTER COLUMN is_first_visit DROP NOT NULL;
ALTER TABLE activity.visit ADD CONSTRAINT visit_unknown_first_requires_history
 CHECK (is_first_visit IS NOT NULL OR
        (import_meta->>'import_type') IS NOT DISTINCT FROM 'crm_history');
COMMENT ON COLUMN activity.visit.is_first_visit IS
 '首次拜访标记；历史CRM来源未记录时为NULL，不得用false代替未知；正常录入默认false';
