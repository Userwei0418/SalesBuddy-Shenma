-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V223__finance_reference_explanation_permission.sql.
-- Original SHA-256: 8a9ea67ddc277a2b59723404602306b9275e2675dc8dd632aaff613c2b6f7f12. See shenma_whole_121_manifest.json.
BEGIN;

-- An optional scoped AI action. Read-only deterministic preview remains usable
-- without this permission. Role/account grants are configured explicitly.
INSERT INTO config.permission_catalog(code,label,module,scopes,sensitive) VALUES
 ('agent.finance_assistant','解释商机财务参考','智能助手',ARRAY['self','assigned','teams','workspace'],false);
INSERT INTO control.permission_module(permission_code,module_code) VALUES
 ('agent.finance_assistant','ai');
UPDATE config.authorization_scope_revision SET revision=revision+1;

INSERT INTO ops.schema_migration(version,description)
 VALUES('V371','Register scoped optional finance reference explanation; no default grants');
COMMIT;
