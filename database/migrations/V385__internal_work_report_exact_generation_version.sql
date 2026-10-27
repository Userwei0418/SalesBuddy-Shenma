-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V237__work_report_exact_generation_version.sql.
-- Original SHA-256: 5925f37840577a57e63e7e098a447e651cfc0ec3bc18a144a69e7d7a02e582f1. See shenma_whole_121_manifest.json.
BEGIN;
-- A fresh run is INSERTed at v1; attaching its job produces queued v2.
-- start_run subsequently produces running v3. Snapshot admission stays v1.
-- Preserve old migration bytes and all existing business/queue rows.
DO $$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid=
  'ops.claim_exact_work_report(text,integer,uuid,uuid,uuid,uuid,text,text,text,integer,timestamptz,timestamptz)'::regprocedure
  AND md5(p.prosrc)='d34fbd44aee218aa4e7403625824fbcf'
  AND p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog']) THEN
  RAISE EXCEPTION 'WORK_REPORT_EXACT_CLAIM_PREDECESSOR_CHANGED' USING ERRCODE='55000';
 END IF;
END $$;

CREATE OR REPLACE FUNCTION ops.claim_exact_work_report(
 p_worker_id text,p_lock_seconds integer,p_job_id uuid,p_workspace_id uuid,
 p_aggregate_id uuid,p_user_id uuid,p_job_type text,p_payload_sha256 text,
 p_input_sha256 text,p_version integer,p_not_before timestamptz,p_deadline timestamptz)
RETURNS SETOF ops.job
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE capacity integer; target record;
BEGIN
 IF p_lock_seconds IS NULL OR p_lock_seconds NOT BETWEEN 3 AND 120
  OR COALESCE(length(btrim(p_worker_id)),0) NOT BETWEEN 1 AND 200
  OR p_job_id IS NULL OR p_workspace_id IS NULL OR p_aggregate_id IS NULL OR p_user_id IS NULL
  OR '00000000-0000-0000-0000-000000000000'::uuid IN (p_job_id,p_workspace_id,p_aggregate_id,p_user_id)
  OR p_job_type IS NULL OR p_job_type NOT IN ('work_report.snapshot','work_report.generate')
  OR p_payload_sha256 IS NULL OR p_payload_sha256 !~ '^[0-9a-f]{64}$'
  OR p_input_sha256 IS NULL OR p_input_sha256 !~ '^[0-9a-f]{64}$'
  OR p_version IS NULL OR p_version<>(CASE WHEN p_job_type='work_report.generate' THEN 2 ELSE 1 END)
  OR p_not_before IS NULL OR p_deadline IS NULL
  OR p_deadline<=p_not_before OR p_deadline>p_not_before+interval '30 minutes'
  OR clock_timestamp()<p_not_before OR clock_timestamp()>=p_deadline THEN
  RAISE EXCEPTION 'INVALID_EXACT_REPORT_CLAIM' USING ERRCODE='22023';
 END IF;
 PERFORM 1 FROM platform.workspace WHERE id=p_workspace_id AND status='active'
  AND deleted_at IS NULL AND security.workspace_is_active(id)
  AND security.workspace_job_enabled(id,p_job_type) FOR SHARE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;
 SELECT concurrency_limit INTO capacity FROM control.tenant_job_schedule
  WHERE workspace_id=p_workspace_id FOR UPDATE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;
 IF (SELECT count(*) FROM ops.job WHERE workspace_id=p_workspace_id
     AND status='running' AND locked_until>clock_timestamp())>=capacity THEN RETURN; END IF;
 SELECT j.* INTO target FROM ops.job j
  WHERE j.id=p_job_id AND j.workspace_id=p_workspace_id
   AND j.job_type=p_job_type AND j.aggregate_type='work_report' AND j.aggregate_id=p_aggregate_id
   AND jsonb_typeof(j.payload)='object'
   AND j.payload->>'workspace_id'=p_workspace_id::text AND j.payload->>'user_id'=p_user_id::text
   AND encode(sha256(convert_to(j.payload::text,'UTF8')),'hex')=p_payload_sha256
   AND j.created_at>=p_not_before AND j.created_at<p_deadline
   AND j.status='queued' AND j.attempts=0 AND j.max_attempts>0
   AND j.available_at<=clock_timestamp()
   AND j.locked_by IS NULL AND j.locked_until IS NULL AND j.lease_token IS NULL
   AND j.started_at IS NULL AND j.completed_at IS NULL
   AND j.last_error_code IS NULL AND j.last_error_detail IS NULL
   AND NOT EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=j.id)
  FOR UPDATE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;
 IF p_job_type='work_report.snapshot' THEN
  PERFORM 1 FROM agent.work_report_snapshot s WHERE s.id=p_aggregate_id
   AND s.workspace_id=p_workspace_id AND s.owner_user_ref_id=p_user_id AND s.job_id=p_job_id
   AND s.created_at>=p_not_before AND s.created_at<p_deadline AND s.status='preparing'
   AND s.payload IS NULL AND s.error IS NULL AND s.data_as_of IS NULL
   AND encode(sha256(convert_to(jsonb_build_object('selection',s.selection,
       'period',s.period,'scope',s.scope)::text,'UTF8')),'hex')=p_input_sha256
   FOR SHARE SKIP LOCKED;
 ELSE
  PERFORM 1 FROM agent.work_report_run r WHERE r.id=p_aggregate_id
   AND r.workspace_id=p_workspace_id AND r.owner_user_ref_id=p_user_id AND r.job_id=p_job_id
   AND r.created_at>=p_not_before AND r.created_at<p_deadline
   AND r.status='queued' AND r.version_no=p_version
   AND r.started_at IS NULL AND r.finished_at IS NULL AND r.result IS NULL AND r.error IS NULL
   AND r.revision_id IS NULL
   AND encode(sha256(convert_to(r.generation_input::text,'UTF8')),'hex')=p_input_sha256
   AND EXISTS(SELECT 1 FROM agent.work_report_snapshot s WHERE s.id=r.snapshot_id
       AND s.workspace_id=p_workspace_id AND s.owner_user_ref_id=p_user_id AND s.status='ready')
   FOR SHARE SKIP LOCKED;
 END IF;
 IF NOT FOUND THEN RETURN; END IF;
 IF clock_timestamp()>=p_deadline OR NOT security.workspace_is_active(p_workspace_id)
  OR NOT security.workspace_job_enabled(p_workspace_id,p_job_type) THEN RETURN; END IF;
 RETURN QUERY UPDATE ops.job j SET status='running',attempts=1,locked_by=p_worker_id,
  lease_token=gen_random_uuid(),locked_until=LEAST(p_deadline,clock_timestamp()+make_interval(secs=>p_lock_seconds)),
  started_at=clock_timestamp(),updated_at=clock_timestamp()
  WHERE j.id=target.id AND j.workspace_id=p_workspace_id RETURNING j.*;
 IF FOUND THEN
  UPDATE control.tenant_job_schedule SET last_claimed_at=clock_timestamp() WHERE workspace_id=p_workspace_id;
 END IF;
END $$;

INSERT INTO ops.schema_migration(version,description)
VALUES('V385','Correct exact report generation claim for queued v2 and running v3');
COMMIT;
