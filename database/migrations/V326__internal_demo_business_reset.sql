-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V178__demo_business_reset.sql.
-- Original SHA-256: 60338c63e009cd89e2568c80084eff8b4aa81441e75b4e28ff23b22b79f131bc. See shenma_whole_121_manifest.json.
BEGIN;
-- Maintenance-only, explicit legacy-purpose attestation. Never infer eligibility
-- from a display name or turn an existing company into a newly activated one.
CREATE TABLE control.demo_reset_registration (
 workspace_id uuid PRIMARY KEY REFERENCES platform.workspace(id),
 company_code text NOT NULL,
 workspace_version integer NOT NULL CHECK(workspace_version>0),
 purpose text NOT NULL CHECK(purpose='demo'),
 administrator_id uuid NOT NULL,
 registered_by text NOT NULL,
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 500),
 registered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(administrator_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
CREATE TABLE control.demo_reset_receipt (
 operation_id uuid PRIMARY KEY,
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 company_code text NOT NULL,
 workspace_version integer NOT NULL CHECK(workspace_version>0),
 plan_sha256 text NOT NULL CHECK(plan_sha256 ~ '^[a-f0-9]{64}$'),
 template_sha256 text NOT NULL CHECK(template_sha256 ~ '^[a-f0-9]{64}$'),
 backup_sha256 text NOT NULL CHECK(backup_sha256 ~ '^[a-f0-9]{64}$'),
 source_revision text NOT NULL CHECK(source_revision ~ '^[a-f0-9]{40}$'),
 as_of date NOT NULL,
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 500),
 executor text NOT NULL,
 summary jsonb NOT NULL,
 completed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON control.demo_reset_registration,control.demo_reset_receipt FROM PUBLIC;
CREATE TRIGGER demo_registration_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
 ON control.demo_reset_registration FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();
CREATE TRIGGER demo_reset_receipt_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
 ON control.demo_reset_receipt FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();
COMMIT;
