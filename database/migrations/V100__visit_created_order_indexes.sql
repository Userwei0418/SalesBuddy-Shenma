-- Optional entry-time ordering; no changes to visit dates, records or permissions.
CREATE INDEX idx_visit_customer_created_cursor ON activity.visit
 (workspace_id,customer_id,created_at DESC,id DESC) WHERE deleted_at IS NULL;
CREATE INDEX idx_visit_opportunity_created_cursor ON activity.visit
 (workspace_id,opportunity_id,customer_id,created_at DESC,id DESC) WHERE deleted_at IS NULL;
COMMENT ON INDEX activity.idx_visit_customer_created_cursor IS
 '跟进历史可选created_desc：按真实系统录入created_at排序，不使用人工recorded_on日期';
ANALYZE activity.visit;
