\set ON_ERROR_STOP on
\pset pager off

SET TIME ZONE 'UTC';

-- V1 基线校验只确认结构和安全边界，不依赖某一批演示数据或旧迁移编号。
DO $verify$
DECLARE
  v_count bigint;
  v_missing text;
BEGIN
  SELECT count(*) INTO v_count
  FROM information_schema.schemata
  WHERE schema_name IN ('platform','config','crm','activity','workflow','insight','agent','ops');
  IF v_count <> 8 THEN
    RAISE EXCEPTION 'Expected 8 business schemas, got %', v_count;
  END IF;

  SELECT string_agg(format('%s.%s', x.schema_name, x.table_name), ', ' ORDER BY x.schema_name, x.table_name)
  INTO v_missing
  FROM (VALUES
    ('crm','customer'), ('crm','opportunity'), ('activity','visit'),
    ('activity','visit_field_value'), ('workflow','task'), ('insight','risk'),
    ('agent','run'), ('ops','job'), ('ops','schema_migration')
  ) AS x(schema_name, table_name)
  WHERE NOT EXISTS (
    SELECT 1 FROM information_schema.tables t
    WHERE t.table_schema = x.schema_name AND t.table_name = x.table_name
  );
  IF v_missing IS NOT NULL THEN
    RAISE EXCEPTION 'Missing V1 tables: %', v_missing;
  END IF;

  SELECT count(*) INTO v_count
  FROM pg_policies
  WHERE schemaname IN ('platform','config','crm','activity','workflow','insight','agent','ops');
  IF v_count < 40 THEN
    RAISE EXCEPTION 'Expected at least 40 RLS policies, got %', v_count;
  END IF;
END
$verify$;

SELECT
  (SELECT count(*) FROM information_schema.tables
    WHERE table_schema IN ('platform','config','crm','activity','workflow','insight','agent','ops')) AS business_tables,
  (SELECT count(*) FROM pg_policies
    WHERE schemaname IN ('platform','config','crm','activity','workflow','insight','agent','ops')) AS rls_policies,
  (SELECT version FROM ops.schema_migration ORDER BY version DESC LIMIT 1) AS latest_recorded_migration;

SELECT 'DATABASE_V1_STRUCTURE_OK' AS result;
