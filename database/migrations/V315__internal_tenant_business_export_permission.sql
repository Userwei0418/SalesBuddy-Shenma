-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V167__tenant_business_export_permission.sql.
-- Original SHA-256: 397986f5e1a33fcc182a3c822e47bcb685436576d73b0dceff06fbf71b016287. See shenma_whole_121_manifest.json.
BEGIN;
-- Packaging does not override any business read/export permission. No platform grant.
INSERT INTO config.permission_catalog VALUES
 ('company.export','导出本公司业务数据包','公司管理',ARRAY['workspace'],true);
INSERT INTO control.permission_module VALUES ('company.export','core');
UPDATE config.permission_role_default SET permissions=permissions ||
 '[{"permission":"company.export","scope":"workspace","team_ids":[]}]'::jsonb
 WHERE role_code='administrator';
-- Only untouched built-in administrators receive the new default. Customized
-- templates retain their exact grants; administrators can configure it explicitly.
INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code)
 SELECT workspace_id,id,'company.export','workspace' FROM config.permission_role
 WHERE builtin_role_code='administrator' AND version_no=1;
UPDATE config.permission_role SET version_no=version_no+1,updated_at=clock_timestamp()
 WHERE builtin_role_code='administrator' AND version_no=1;
COMMIT;
