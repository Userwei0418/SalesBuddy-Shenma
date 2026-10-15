-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V168__demo_template_receipt.sql.
-- Original SHA-256: e1489bdd86b2a47e1f1bd15752e61a830e181d295f662207c2bdd364ff5bea74. See shenma_whole_121_manifest.json.
BEGIN;
-- Maintenance-only installation receipt. Tenant/platform runtime credentials
-- cannot initialize templates or read bootstrap credential fingerprints.
CREATE TABLE control.demo_template_receipt (
 workspace_id uuid PRIMARY KEY REFERENCES platform.workspace(id),
 template_id text NOT NULL,
 template_sha256 text NOT NULL CHECK(template_sha256 ~ '^[a-f0-9]{64}$'),
 source_revision text NOT NULL CHECK(source_revision ~ '^[a-f0-9]{40}$'),
 credential_receipt_sha256 text NOT NULL CHECK(credential_receipt_sha256 ~ '^[a-f0-9]{64}$'),
 as_of date NOT NULL,
 initialized_by text NOT NULL,
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 500),
 summary jsonb NOT NULL,
 initialized_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON control.demo_template_receipt FROM PUBLIC;
COMMIT;
