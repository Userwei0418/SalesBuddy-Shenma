-- A database-derived ordering key: this is not a second writable visit date.
-- Preserve the existing recorded_on / Shanghai created_at fallback exactly.
-- Stored typed columns allow cursor predicates to be index conditions under RLS;
-- a timezone expression in the predicate otherwise runs after authorization.
ALTER TABLE activity.visit ADD COLUMN history_sort_date date
  GENERATED ALWAYS AS (COALESCE(recorded_on,timezone('Asia/Shanghai',created_at)::date)) STORED;
COMMENT ON COLUMN activity.visit.history_sort_date IS
  '只用于历史排序/游标索引的自动派生日期；源为recorded_on，缺失时取created_at上海日期；不可独立填写';

CREATE INDEX idx_visit_customer_history_cursor ON activity.visit
  (workspace_id,customer_id,interaction_at DESC NULLS LAST,history_sort_date DESC,id DESC)
  WHERE deleted_at IS NULL;
CREATE INDEX idx_visit_opportunity_history_cursor ON activity.visit
  (workspace_id,opportunity_id,customer_id,interaction_at DESC NULLS LAST,history_sort_date DESC,id DESC)
  WHERE deleted_at IS NULL;
-- No policy, function owner, grants or write authorization is changed.

-- Populate statistics for the new generated key in existing installations.
ANALYZE activity.visit;
