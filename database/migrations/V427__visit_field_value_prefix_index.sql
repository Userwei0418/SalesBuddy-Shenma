BEGIN;
-- Valid form text can exceed a B-tree index tuple's byte limit. Existing
-- visit-field lookups need the tenant/field prefix, not the full value_text.
-- Keep all stored values, constraints, permissions and RLS unchanged.
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

-- DROP INDEX requires this table lock too. Acquire it before building the
-- replacement so contention fails before any DDL; the managed migration
-- records its version/checksum in the same transaction as these changes.
LOCK TABLE activity.visit_field_value IN ACCESS EXCLUSIVE MODE;
CREATE INDEX idx_visit_field_value_workspace_field
    ON activity.visit_field_value USING btree (workspace_id, field_definition_id);
DROP INDEX activity.idx_visit_field_value_text;

COMMENT ON INDEX activity.idx_visit_field_value_workspace_field IS
    'Tenant and form-field lookup; full field text remains in the table without a B-tree size bound';
COMMIT;
