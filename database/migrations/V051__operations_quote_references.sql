BEGIN;
ALTER TABLE crm.opportunity ADD CONSTRAINT opportunity_id_workspace_unique UNIQUE(id,workspace_id);
CREATE TABLE crm.opportunity_quote_reference (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,opportunity_id uuid NOT NULL,
 reference_no text NOT NULL,title text NOT NULL,url text,amount numeric(18,2) CHECK(amount>=0),
 created_by_user_ref_id uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id),
 FOREIGN KEY(created_by_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 UNIQUE(opportunity_id,reference_no),CHECK(url IS NULL OR url ~ '^https?://')
);
COMMENT ON TABLE crm.opportunity_quote_reference IS '运营关联已存在的报价编号/链接；不代表本系统制作、审批或签发报价';
ALTER TABLE crm.opportunity_quote_reference ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.opportunity_quote_reference FORCE ROW LEVEL SECURITY;
CREATE POLICY quote_read ON crm.opportunity_quote_reference FOR SELECT USING(workspace_id=common.current_workspace_id() AND security.has_opportunity_access(opportunity_id));
CREATE POLICY quote_write ON crm.opportunity_quote_reference FOR INSERT WITH CHECK(workspace_id=common.current_workspace_id() AND security.management_actor() AND created_by_user_ref_id=common.current_user_ref_id());
CREATE TRIGGER quote_reference_audit AFTER INSERT OR UPDATE OR DELETE ON crm.opportunity_quote_reference FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row();
CREATE POLICY administrator_runtime_config ON config.agent_runtime_config
 USING(workspace_id=common.current_workspace_id() AND common.current_role_code()='administrator' AND security.has_active_role('administrator'))
 WITH CHECK(workspace_id=common.current_workspace_id() AND common.current_role_code()='administrator' AND security.has_active_role('administrator'));
CREATE POLICY administrator_runtime_release ON config.agent_runtime_release
 USING(workspace_id=common.current_workspace_id() AND common.current_role_code()='administrator' AND security.has_active_role('administrator'))
 WITH CHECK(workspace_id=common.current_workspace_id() AND common.current_role_code()='administrator' AND security.has_active_role('administrator'));
-- Extend only explicit runtime grants already present in this installation.
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
 WHERE table_schema='crm' AND table_name='customer' AND privilege_type='SELECT' AND grantee<>'PUBLIC' LOOP
  EXECUTE format('GRANT SELECT ON crm.customer_ownership,crm.customer_claim_request,crm.customer_ownership_event,ops.system_event,ops.ai_usage_alert TO %I',r.grantee);
  EXECUTE format('GRANT SELECT,INSERT,UPDATE ON ops.ai_usage_rule TO %I',r.grantee);
  EXECUTE format('GRANT SELECT,INSERT ON crm.opportunity_quote_reference TO %I',r.grantee);
  EXECUTE format('REVOKE ALL ON platform.password_credential,security.login_throttle FROM %I',r.grantee);
  EXECUTE format('REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON crm.customer_ownership,crm.customer_claim_request,crm.customer_ownership_event FROM %I',r.grantee);
  EXECUTE format('REVOKE UPDATE,DELETE,TRUNCATE ON ops.audit_log FROM %I',r.grantee);
 END LOOP;
END $$;
COMMIT;
