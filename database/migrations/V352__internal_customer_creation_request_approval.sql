-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V204__customer_creation_request_approval.sql.
-- Original SHA-256: 20e996aa00c31462e8aae305dffa5db7cd48081b121fa71db3b6750898298c82. See shenma_whole_121_manifest.json.
BEGIN;

-- A salesperson submits a request; only an operations/administrator actor can
-- turn that request into a crm.customer row. The payload is kept as the
-- reviewed snapshot so approval never depends on mutable form state.
CREATE TABLE IF NOT EXISTS crm.customer_creation_request (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 applicant_user_ref_id uuid NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','cancelled')),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 normalized_name text NOT NULL CHECK(length(btrim(normalized_name))>0),
 company_reference text NOT NULL CHECK(length(btrim(company_reference))>0),
 requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 reviewed_at timestamptz,
 reviewer_user_ref_id uuid,
 decision_reason text NOT NULL DEFAULT '',
 created_customer_id uuid,
 version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 CHECK((status='pending')=(reviewed_at IS NULL)),
 FOREIGN KEY(applicant_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(reviewer_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(created_customer_id,workspace_id) REFERENCES crm.customer(id,workspace_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS customer_creation_one_pending
 ON crm.customer_creation_request(workspace_id,applicant_user_ref_id,normalized_name)
 WHERE status='pending';
CREATE INDEX IF NOT EXISTS customer_creation_queue
 ON crm.customer_creation_request(workspace_id,status,requested_at DESC,id DESC);

ALTER TABLE crm.customer_creation_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.customer_creation_request FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS creation_request_applicant_read ON crm.customer_creation_request;
CREATE POLICY creation_request_applicant_read ON crm.customer_creation_request
 FOR SELECT USING(workspace_id=common.current_workspace_id()
   AND applicant_user_ref_id=common.current_user_ref_id()
   AND security.authorization_has('customer.create_request'));
DROP POLICY IF EXISTS creation_request_reviewer_read ON crm.customer_creation_request;
CREATE POLICY creation_request_reviewer_read ON crm.customer_creation_request
 FOR SELECT USING(workspace_id=common.current_workspace_id()
   AND security.authorization_has('customer.create_review'));
DROP POLICY IF EXISTS creation_request_applicant_insert ON crm.customer_creation_request;
CREATE POLICY creation_request_applicant_insert ON crm.customer_creation_request
 FOR INSERT WITH CHECK(workspace_id=common.current_workspace_id()
   AND applicant_user_ref_id=common.current_user_ref_id()
   AND status='pending' AND security.authorization_has('customer.create_request'));
DROP POLICY IF EXISTS creation_request_reviewer_update ON crm.customer_creation_request;
CREATE POLICY creation_request_reviewer_update ON crm.customer_creation_request
 FOR UPDATE USING(workspace_id=common.current_workspace_id()
   AND security.authorization_has('customer.create_review'))
 WITH CHECK(workspace_id=common.current_workspace_id()
   AND security.authorization_has('customer.create_review'));
REVOKE ALL ON crm.customer_creation_request FROM PUBLIC,salegent_feishu_worker;

INSERT INTO config.permission_catalog(code,label,module,scopes,sensitive) VALUES
 ('customer.create_request','提交客户新建申请','客户管理',ARRAY['self'],false),
 ('customer.create_review','审批客户新建申请','客户管理',ARRAY['workspace'],false)
 ON CONFLICT(code) DO NOTHING;
INSERT INTO control.permission_module(permission_code,module_code) VALUES
 ('customer.create_request','core'),('customer.create_review','core')
 ON CONFLICT(permission_code,module_code) DO NOTHING;

-- Existing untouched built-in templates inherit the new actions. Customized
-- roles remain unchanged and can be configured through the authorization UI.
UPDATE config.permission_role_default
 SET permissions=permissions||$permissions$[{"permission":"customer.create_request","scope":"self","team_ids":[]}]$permissions$::jsonb
 WHERE role_code IN ('sales','supervisor','manager')
   AND NOT (permissions @> $permissions$[{"permission":"customer.create_request"}]$permissions$::jsonb);
UPDATE config.permission_role_default
 SET permissions=permissions||$permissions$[{"permission":"customer.create_review","scope":"workspace","team_ids":[]}]$permissions$::jsonb
 WHERE role_code IN ('operations','administrator')
   AND NOT (permissions @> $permissions$[{"permission":"customer.create_review"}]$permissions$::jsonb);
-- Existing customer role grants were migrated by V160; retain their decisions.

-- Match the runtime grant convention used by earlier CRM request tables.
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT rolname FROM pg_roles
  WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
    AND rolname NOT LIKE 'pg\_%' ESCAPE '\'
    AND has_schema_privilege(oid,'crm','USAGE')
 LOOP
  EXECUTE format('GRANT SELECT,INSERT,UPDATE ON crm.customer_creation_request TO %I',r.rolname);
 END LOOP;
END $$;

COMMENT ON TABLE crm.customer_creation_request IS '销售新建客户申请；申请阶段不写 crm.customer，批准时在同一事务落库';
INSERT INTO ops.schema_migration(version,description)
VALUES('V352','Customer creation request and operations approval MVP');
COMMIT;
