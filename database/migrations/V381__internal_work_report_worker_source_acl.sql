-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V233__work_report_worker_source_acl.sql.
-- Original SHA-256: 674828c24635694938ee702160d8cc292cdebdbb448e713a75d525efc28af71e. See shenma_whole_121_manifest.json.
BEGIN;
-- Version the report-source ACL contract together with its runtime policy.
-- The reviewed V233 runtime_permissions.json grants only SELECT on these two
-- relations to the worker service. Installation/upgrade reconciliation applies
-- that exact policy; this marker never grants to guessed role names or PUBLIC.
-- Managed upgrades keep writers stopped until both migration and ACL verification
-- finish. A V232 bundle cannot be restarted against this V233 ledger.
DO $$
BEGIN
 IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     JOIN (VALUES ('visit_opportunity',true),('visit_participant',false))
       expected(name,force_rls) ON c.relname=expected.name
     WHERE n.nspname='activity' AND c.relkind='r' AND c.relrowsecurity
       AND c.relforcerowsecurity=expected.force_rls) <> 2 THEN
  RAISE EXCEPTION 'Work report source tables must retain their reviewed RLS settings';
 END IF;
END $$;
INSERT INTO ops.schema_migration(version,description)
VALUES('V381','Version worker report-source SELECT permissions with existing row isolation');
COMMIT;
