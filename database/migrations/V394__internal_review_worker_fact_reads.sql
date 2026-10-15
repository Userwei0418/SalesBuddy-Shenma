-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V246__review_worker_fact_reads.sql.
-- Original SHA-256: 947f157f37bf721bef5b0e8bbae5e28cb223a4a82b07fb6db81c3a3f07611d33. See shenma_whole_121_manifest.json.
BEGIN;

-- Report jobs run as the ordinary Worker, with the requesting actor and RLS.
-- Keep the API/Feishu/platform policies and all business writes unchanged.
-- target_scope_write returns only the editable flag used by the shared target
-- projection; it does not mutate targets or grant target-management authority.
-- Preserve the pre-Review reconciler for rollback inspection, but do not invoke
-- it here: its historical target-reader branch widens API/Worker ACLs when the
-- new Worker sales_target SELECT grant becomes visible. This migration owns
-- only the exact Review fact-read delta below.
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_review_worker_reads;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; runtime record;
BEGIN
 total:=0;
 FOR runtime IN SELECT r.rolname FROM pg_roles r WHERE NOT r.rolsuper AND NOT r.rolbypassrls
  AND r.rolname NOT LIKE 'pg\_%' ESCAPE '\'
 LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants_before_review_worker_reads(),security.reconcile_runtime_grants() FROM %I',runtime.rolname);
  IF has_function_privilege(runtime.rolname,'ops.claim_job(text,integer,text[],boolean)','EXECUTE')
   AND has_function_privilege(runtime.rolname,'security.work_report_source_access(jsonb,jsonb)','EXECUTE')
   AND has_table_privilege(runtime.rolname,'agent.work_report_snapshot','SELECT,UPDATE')
   AND NOT has_function_privilege(runtime.rolname,'security.save_account_authorization(uuid,jsonb)','EXECUTE') THEN
   -- An older reconciler recognizes target readers by sales_target SELECT and
   -- adds approval-request tables on the next call. Report collection never
   -- needs those rows; keep this wrapper idempotent at the five-table boundary.
   EXECUTE format('REVOKE SELECT ON crm.sales_target_batch_request,crm.sales_target_change_request FROM %I',runtime.rolname);
   EXECUTE format('GRANT SELECT ON crm.sales_target,crm.review_metric,crm.review_objective,crm.review_key_result,crm.review_key_result_progress TO %I',runtime.rolname);
   EXECUTE format('GRANT EXECUTE ON FUNCTION security.target_scope_read(text,uuid,uuid,text),security.target_scope_write(text,uuid,uuid,text),security.review_objective_readable(uuid),security.task_alignment_read(uuid) TO %I',runtime.rolname);
   total:=total+1;
  END IF;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_review_worker_reads() FROM PUBLIC;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description)
 VALUES('V394','Read-only Review fact sources for actor-scoped work report Worker jobs');
COMMIT;
