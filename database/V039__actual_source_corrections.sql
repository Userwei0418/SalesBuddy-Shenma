BEGIN;
SELECT pg_advisory_xact_lock(2026091006);
-- Keep the original source reference when correcting an explicitly voided fact.
-- Only one effective fact per customer/kind/source is allowed; historical facts remain immutable.
ALTER TABLE crm.customer_actual DROP CONSTRAINT customer_actual_workspace_id_customer_id_kind_source_ref_key;
CREATE UNIQUE INDEX uq_actual_active_source ON crm.customer_actual(workspace_id,customer_id,kind,source_ref)
 WHERE voided_at IS NULL;
INSERT INTO ops.schema_migration(version,description) VALUES('V039','实绩作废后可沿用来源编号更正，有效记录仍防重复');
COMMIT;
