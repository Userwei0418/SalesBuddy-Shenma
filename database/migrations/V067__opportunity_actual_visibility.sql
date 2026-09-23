BEGIN;
-- A linked actual follows its opportunity, independently of customer ownership.
-- Unallocated customer actuals retain customer permission; writes remain management-only.
DROP POLICY actual_read ON crm.customer_actual;
CREATE POLICY actual_read ON crm.customer_actual FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND CASE WHEN opportunity_id IS NULL
 THEN security.has_customer_access(customer_id) ELSE security.has_opportunity_access(opportunity_id) END);
COMMENT ON POLICY actual_read ON crm.customer_actual IS '商机实绩按商机权限；客户未分摊实绩按客户权限，不因一次跟进而开放客户账本';
COMMIT;
