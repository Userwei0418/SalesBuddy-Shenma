BEGIN;

-- Production creates migrations with a maintenance identity, so schema-owner
-- default ACLs do not apply. Preserve the existing model-accounting access
-- boundary for new receipts; never grant DELETE, ownership or RLS bypass.
DO $$
DECLARE permission record;
BEGIN
  FOR permission IN
    SELECT DISTINCT r.rolname,a.privilege_type
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) a
    JOIN pg_catalog.pg_roles r ON r.oid=a.grantee
    WHERE n.nspname='agent' AND c.relname='model_invocation'
      AND a.grantee<>c.relowner
      AND a.privilege_type IN ('SELECT','INSERT','UPDATE')
  LOOP
    EXECUTE format('GRANT %s ON agent.inference_operation TO %I',
                   permission.privilege_type,permission.rolname);
  END LOOP;
END $$;

INSERT INTO ops.schema_migration(version,description)
VALUES('V061','新推理回执继承既有模型调用表的运行权限，保留RLS且不授予删除');

COMMIT;
