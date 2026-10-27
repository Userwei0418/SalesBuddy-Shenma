BEGIN;

-- Backend-owned bridge between one immutable SalesBuddy version and one
-- provider document. Clients can select materials, never provider IDs.
CREATE TABLE knowledge.provider_binding (
 workspace_id uuid NOT NULL,
 material_id uuid NOT NULL,
 version_id uuid NOT NULL,
 provider_dataset_id text NOT NULL CHECK(length(provider_dataset_id) BETWEEN 1 AND 300),
 provider_document_id text NOT NULL CHECK(length(provider_document_id) BETWEEN 1 AND 300),
 state text NOT NULL DEFAULT 'active' CHECK(state IN ('active','disabled')),
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,material_id,version_id),
 FOREIGN KEY(workspace_id,material_id,version_id)
   REFERENCES knowledge.version(workspace_id,material_id,id)
);
CREATE UNIQUE INDEX knowledge_provider_binding_document
 ON knowledge.provider_binding(workspace_id,provider_dataset_id,provider_document_id)
 WHERE state='active';
CREATE INDEX knowledge_provider_binding_material
 ON knowledge.provider_binding(workspace_id,material_id,version_id);
ALTER TABLE knowledge.provider_binding ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.provider_binding FORCE ROW LEVEL SECURITY;
REVOKE ALL ON knowledge.provider_binding FROM PUBLIC;
CREATE POLICY provider_binding_read ON knowledge.provider_binding FOR SELECT USING (
 workspace_id=common.current_workspace_id() AND security.knowledge_can(material_id,'read')
);

INSERT INTO ops.schema_migration(version,description)
 VALUES('V412','Knowledge provider document bindings for exact version retrieval');
COMMIT;
